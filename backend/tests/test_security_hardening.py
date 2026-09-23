"""
Regression tests for the security-review fixes.

Each test pins one hole shut: fail-open auth, the OAuth callback's reflected
XSS / open redirect / stale state, auth brute force, plan-condition `eval`,
LLM-steered navigation, Salesforce REST path injection, and the sandbox's
unauthenticated /run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


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


# ---------------------------------------------------------------------------
# Auth fails closed
# ---------------------------------------------------------------------------

def test_dev_mode_is_off_by_default():
    from app.config import Settings
    assert Settings(_env_file=None).auth_dev_mode is False


def test_unauthenticated_request_is_rejected_by_default(client, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    r = client.get("/plans")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# OAuth callback
# ---------------------------------------------------------------------------

def test_oauth_error_page_escapes_reflected_input(client):
    payload = "<script>alert(1)</script>"
    r = client.get("/oauth/salesforce/callback",
                   params={"error": payload, "error_description": payload})
    assert r.status_code == 400
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text


@pytest.mark.parametrize("return_to", [
    "https://evil.example/phish", "//evil.example", "/\\evil.example", "javascript:alert(1)",
])
def test_return_to_rejects_off_site_targets(return_to):
    from fastapi import HTTPException
    from app.api.oauth import _safe_return_to

    with pytest.raises(HTTPException):
        _safe_return_to(return_to)


@pytest.mark.parametrize("return_to", ["/dashboard", "http://localhost:3000/dashboard"])
def test_return_to_allows_own_paths_and_frontend_origins(return_to):
    from app.api.oauth import _safe_return_to
    assert _safe_return_to(return_to) == return_to


def test_expired_oauth_state_is_refused(client):
    from app.db.base import get_sessionmaker
    from app.db.models import OAuthState

    async def _seed():
        async with get_sessionmaker()() as s:
            s.add(OAuthState(
                state="stale_state", user_id="user_local", provider="salesforce",
                redirect_uri="http://localhost:8001/oauth/salesforce/callback",
                created_at=datetime.utcnow() - timedelta(minutes=30),
            ))
            await s.commit()

    asyncio.run(_seed())
    r = client.get("/oauth/salesforce/callback",
                   params={"code": "c", "state": "stale_state"})
    assert r.status_code == 400
    assert "expired" in r.text


# ---------------------------------------------------------------------------
# Auth brute force
# ---------------------------------------------------------------------------

def test_login_is_rate_limited_per_email(client):
    body = {"email": "victim@example.com", "password": "wrong-password"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(12)]
    assert codes[0] == 401
    assert codes[-1] == 429


def test_login_password_length_is_bounded(client):
    r = client.post("/auth/login",
                    json={"email": "a@example.com", "password": "x" * 10_000})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Plan conditions: no eval
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("condition,expected", [
    ("n > 2 and s == 'Open'", True),
    ("len(items) == 0", False),
    ("d['k'] in ['x', 'y']", True),
    ("not n", False),
    ("1 < n < 5", True),
])
def test_condition_supports_simple_expressions(condition, expected):
    from sandbox_agent.executor import _evaluate_condition
    variables = {"n": 3, "s": "Open", "items": [1], "d": {"k": "x"}}
    assert _evaluate_condition(condition, variables) is expected


@pytest.mark.parametrize("condition", [
    "().__class__.__base__.__subclasses__()",
    "__import__('os').system('id')",
    "[c for c in ().__class__.__mro__]",
    "(lambda: 1)()",
])
def test_condition_refuses_escape_attempts(condition):
    """The evaluator rejects these outright (no attribute access, no
    arbitrary calls); the step then falls back to its default branch."""
    import ast
    from sandbox_agent.executor import _UnsafeCondition, _evaluate_condition, _safe_eval

    with pytest.raises((_UnsafeCondition, NameError)):
        _safe_eval(ast.parse(condition, mode="eval"), {})
    assert _evaluate_condition(condition, {}) is True


# ---------------------------------------------------------------------------
# LLM-chosen navigation
# ---------------------------------------------------------------------------

def test_navigation_is_limited_to_plan_and_salesforce_hosts():
    from sandbox_agent import browser_mode as bm

    bm.set_navigation_allowlist(["https://acme.lightning.force.com/lightning/o/Case/list",
                                 "https://intranet.example.com/start"])
    assert bm.navigation_blocked_reason("https://acme.lightning.force.com/x") is None
    assert bm.navigation_blocked_reason("https://other.my.salesforce.com/x") is None
    assert bm.navigation_blocked_reason("https://intranet.example.com/page") is None
    assert bm.navigation_blocked_reason("https://evil.example/?leak=1") is not None
    assert bm.navigation_blocked_reason("https://force.com.evil.example/") is not None
    assert bm.navigation_blocked_reason("file:///etc/passwd") is not None
    assert bm.navigation_blocked_reason("javascript:alert(1)") is not None


# ---------------------------------------------------------------------------
# Salesforce MCP arguments
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {"object_type": "../../sobjects/User", "id": "001000000000001"},
    {"object_type": "Lead?x=", "id": "001000000000001"},
    {"object_type": "Lead", "id": "../limits"},
    {"soql": "DELETE FROM Lead"},
    {"sosl": "SELECT Id FROM Lead"},
    {"object_type": "Lead", "fields": "not-a-dict"},
])
def test_salesforce_mcp_rejects_path_and_query_injection(args):
    from app.services.mcp.base import MCPInvalidArgs
    from app.services.mcp.salesforce import SalesforceMCPServer

    with pytest.raises(MCPInvalidArgs):
        SalesforceMCPServer._require(args, [])


def test_salesforce_mcp_accepts_normal_arguments():
    from app.services.mcp.salesforce import SalesforceMCPServer

    SalesforceMCPServer._require(
        {"object_type": "Custom_Object__c", "id": "001A0000006Vm9rIAC",
         "fields": {"Name": "x"}, "soql": " select Id from Lead", "sosl": "FIND {x}"},
        ["object_type", "id"],
    )


# ---------------------------------------------------------------------------
# Sandbox /run
# ---------------------------------------------------------------------------

def test_sandbox_run_requires_the_run_token(monkeypatch):
    from sandbox_agent import main as sandbox_main

    monkeypatch.setenv("RUN_TOKEN", "secret-token")
    monkeypatch.setenv("BACKEND_MCP_URL", "http://backend")
    monkeypatch.setattr(sandbox_main, "run_plan", lambda req: None)
    c = TestClient(sandbox_main.app)
    body = {"plan": {"id": "p", "goal": "g", "steps": []}}

    assert c.post("/run", json=body).status_code == 401
    assert c.post("/run", json=body,
                  headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_sandbox_run_refuses_when_no_token_configured(monkeypatch):
    from sandbox_agent import main as sandbox_main

    monkeypatch.delenv("RUN_TOKEN", raising=False)
    c = TestClient(sandbox_main.app)
    r = c.post("/run", json={"plan": {"id": "p", "goal": "g", "steps": []}},
               headers={"Authorization": "Bearer "})
    assert r.status_code == 503
