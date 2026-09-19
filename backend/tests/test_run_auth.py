"""
Sandbox-facing endpoint authentication.

/mcp, /sandbox/llm and /sandbox/frontdoor are the three routes a container
can call. Each one hands out something valuable — a user's integration
credentials, the backend's LLM key, a logged-in Salesforce session — so each
must require the per-run token and must never fall back to a default user.

Every test here is a request that USED to succeed anonymously.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient

from app.schemas.automation import Automation
from app.schemas.plan import Plan, PlanStatus, Step, StepKind
from app.schemas.run import Run, RunStatus, RunTrigger


@pytest.fixture
def db():
    """Fresh schema on the temp test database."""
    from app.db import models  # noqa: F401
    from app.db.base import Base, close_engine, get_engine
    from app.services.memory import sql_store  # noqa: F401

    async def _create():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    yield

    async def _drop():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await close_engine()

    asyncio.run(_drop())


@pytest.fixture
def client(db):
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def live_run(db):
    """A persisted run with a known token, as a real sandbox would have."""
    from app.services.run_repo import get_repository

    token = secrets.token_urlsafe(32)
    repo = get_repository()

    async def _seed():
        await repo.save_plan(
            Plan(id="plan_auth", goal="g", status=PlanStatus.APPROVED,
                 steps=[Step(id="s1", kind=StepKind.NAVIGATE, description="go",
                             details={"url": "https://example.com"})]),
            user_id="owner_user",
        )
        await repo.save_automation(
            Automation(id="auto_auth", user_id="owner_user", name="A", plan_id="plan_auth"),
            user_id="owner_user",
        )
        run = Run(
            id="run_auth", automation_id="auto_auth", plan_version=1,
            triggered_by=RunTrigger.MANUAL, status=RunStatus.RUNNING,
            mcp_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        await repo.save_run(run, user_id="owner_user")

    asyncio.run(_seed())
    return {"run_id": "run_auth", "token": token}


# ---------------------------------------------------------------------------
# /mcp — a user's integration credentials
# ---------------------------------------------------------------------------

def test_mcp_rejects_call_with_no_run_id(client):
    """The old anonymous path: no run_id used to resolve to the default user."""
    r = client.post("/mcp/mock/create_record", json={"args": {"type": "Lead"}})
    assert r.status_code == 401


def test_mcp_rejects_unknown_run_id(client):
    r = client.post("/mcp/mock/create_record",
                    json={"args": {"type": "Lead"}, "run_id": "run_does_not_exist"})
    assert r.status_code == 401


def test_mcp_rejects_missing_token(client, live_run):
    r = client.post("/mcp/mock/create_record",
                    json={"args": {"type": "Lead"}, "run_id": live_run["run_id"]})
    assert r.status_code == 401


def test_mcp_rejects_wrong_token(client, live_run):
    r = client.post(
        "/mcp/mock/create_record",
        json={"args": {"type": "Lead"}, "run_id": live_run["run_id"]},
        headers={"Authorization": "Bearer not-the-right-token"},
    )
    assert r.status_code == 401


def test_mcp_accepts_the_runs_own_token(client, live_run):
    """The happy path still works — the mock server needs no credentials."""
    r = client.post(
        "/mcp/mock/create_record",
        json={"args": {"type": "Lead"}, "run_id": live_run["run_id"]},
        headers={"Authorization": f"Bearer {live_run['token']}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# /sandbox/llm — the backend's LLM key
# ---------------------------------------------------------------------------

def test_llm_proxy_rejects_anonymous_call(client):
    """Anyone who could reach the port used to be able to spend the API key."""
    r = client.post("/sandbox/llm/generate", json={"prompt": "hello"})
    assert r.status_code == 401


def test_llm_proxy_rejects_wrong_token(client, live_run):
    r = client.post(
        "/sandbox/llm/generate",
        json={"prompt": "hello", "run_id": live_run["run_id"]},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /sandbox/frontdoor — a logged-in Salesforce session
# ---------------------------------------------------------------------------

def test_frontdoor_requires_a_token(client):
    r = client.get("/sandbox/frontdoor/salesforce", follow_redirects=False)
    assert r.status_code == 401


def test_frontdoor_rejects_an_invalid_token(client, live_run):
    r = client.get("/sandbox/frontdoor/salesforce",
                   params={"run_token": "nope"}, follow_redirects=False)
    assert r.status_code == 401


def test_frontdoor_with_a_valid_token_fails_on_credentials_not_auth(client, live_run):
    """A valid token gets past auth; the user simply has no Salesforce
    connected, so it fails at the credential step (400) — not 401."""
    r = client.get("/sandbox/frontdoor/salesforce",
                   params={"run_token": live_run["token"]}, follow_redirects=False)
    assert r.status_code == 400
    assert "not connected" in r.text.lower()


# ---------------------------------------------------------------------------
# Token comparison
# ---------------------------------------------------------------------------

def test_token_hash_is_compared_not_the_raw_token(db, live_run):
    """Only the hash is persisted, and comparison is constant-time."""
    from app.services.run_auth import authenticate_run, hash_run_token
    from app.services.run_repo import get_repository

    run = asyncio.run(get_repository().get_run(live_run["run_id"]))
    assert run.mcp_token_hash == hash_run_token(live_run["token"])
    assert live_run["token"] not in (run.mcp_token_hash or "")

    ok = asyncio.run(authenticate_run(
        live_run["run_id"], f"Bearer {live_run['token']}"))
    assert ok.id == live_run["run_id"]
