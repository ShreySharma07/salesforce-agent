"""
Wiring tests: repository, run pipeline, and the API contract between them.

These don't spawn Docker — the SandboxRunner is mocked — but they exercise the
real repository, the real routes, and the real run executor, which is where
the wiring bugs live. Each test below corresponds to something that was
silently broken:

  - a run's per-step timings were invented by dividing elapsed time evenly
  - a run's cost was always $0
  - /runs returned every user's runs with no authentication
  - a paused run was a dead end with no way to answer it
  - an unapproved or invalid plan could still be sent to a sandbox
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.schemas.automation import Automation
from app.schemas.plan import Plan, PlanStatus, Step, StepKind
from app.services.sandbox import SandboxHandle


# ---------------------------------------------------------------------------
# Schema + repository fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """Create every table on the temp test database (conftest points the URL
    at a throwaway file). Importing the memory store registers its tables too."""
    from app.db.base import Base, get_engine
    from app.db import models  # noqa: F401 — registers core tables
    from app.services.memory import sql_store  # noqa: F401 — registers memory tables

    async def _create():
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    yield

    async def _drop():
        from app.db.base import close_engine
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await close_engine()

    asyncio.run(_drop())


@pytest.fixture
def repo(db):
    from app.services.run_repo import get_repository
    return get_repository()


def _plan(pid="plan_test_001", *, status=PlanStatus.APPROVED) -> Plan:
    return Plan(
        id=pid, goal="Test goal", status=status,
        steps=[Step(id="s1", kind=StepKind.NAVIGATE, description="Open SF",
                    details={"url": "https://example.com"})],
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

async def test_save_and_get_plan(repo):
    await repo.save_plan(_plan(), user_id="u1")
    loaded = await repo.get_plan("plan_test_001", user_id="u1")
    assert loaded is not None
    assert loaded.goal == "Test goal"
    assert loaded.steps[0].id == "s1"


async def test_get_missing_plan_returns_none(repo):
    assert await repo.get_plan("nope", user_id="u1") is None


async def test_plan_is_not_readable_by_another_user(repo):
    await repo.save_plan(_plan("plan_owned"), user_id="alice")
    assert await repo.get_plan("plan_owned", user_id="bob") is None


async def test_list_plans_is_scoped(repo):
    for i in range(3):
        await repo.save_plan(_plan(f"plan_{i}"), user_id="u1")
    await repo.save_plan(_plan("plan_other"), user_id="u2")
    assert {p.id for p in await repo.list_plans(user_id="u1")} == {"plan_0", "plan_1", "plan_2"}


async def test_save_and_get_automation(repo):
    auto = Automation(id="auto_001", user_id="u1", name="Daily Triage", plan_id="plan_001")
    await repo.save_automation(auto, user_id="u1")
    loaded = await repo.get_automation("auto_001", user_id="u1")
    assert loaded is not None and loaded.name == "Daily Triage"


# ---------------------------------------------------------------------------
# API + run pipeline, with a mocked sandbox
# ---------------------------------------------------------------------------

class MockRunner:
    """Stand-in for SandboxRunner. Records calls so tests can verify wiring."""
    runner_kind = "mock"
    spawn_called = False
    teardown_called = False
    last_plan = None
    last_kwargs: dict = {}
    # What the fake sandbox reports back; tests override per scenario.
    response: dict = {}

    async def spawn(self, config):
        MockRunner.spawn_called = True
        MockRunner.last_env = dict(config.env)
        return SandboxHandle(
            sandbox_id="sandbox_test", api_url="http://mock:8000",
            live_view_url="http://mock:6080/vnc.html", runner_kind="mock",
        )

    async def wait_healthy(self, handle, *, timeout_seconds=60):
        return True

    async def execute_plan(self, handle, plan_dict, **kwargs):
        MockRunner.last_plan = plan_dict
        MockRunner.last_kwargs = kwargs
        return MockRunner.response

    async def teardown(self, handle):
        MockRunner.teardown_called = True

    async def get_logs(self, handle):
        return ""


@pytest.fixture
def client(db, monkeypatch):
    """TestClient with the sandbox runner replaced by MockRunner."""
    from app.services import run_executor

    monkeypatch.setattr(run_executor, "get_sandbox_runner", lambda: MockRunner())
    MockRunner.spawn_called = False
    MockRunner.teardown_called = False
    MockRunner.last_plan = None
    MockRunner.last_kwargs = {}
    MockRunner.response = {
        "status": "completed",
        "step_results": [{
            "step_id": "s1", "status": "succeeded", "detail": "did the thing",
            "started_at": "2026-01-01T10:00:00", "finished_at": "2026-01-01T10:00:07",
            "attempts": 1,
            "trace": [{"iteration": 1, "action": "click", "observation": "ok",
                       "input_tokens": 1000, "output_tokens": 200}],
        }],
        "final_url": "https://example.com",
        "elapsed_seconds": 1.0,
    }

    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def _seed_approved_plan(client) -> dict:
    """Upsert an approved plan through the API and return it."""
    plan = _plan("plan_e2e", status=PlanStatus.DRAFT)
    r = client.post("/plans", json=plan.model_dump(mode="json"))
    assert r.status_code == 200, r.text
    r = client.post("/plans/plan_e2e/approve")
    assert r.status_code == 200, r.text
    return r.json()


def test_health_endpoint(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "llm_provider" in body and "sandbox_runner" in body


def test_create_and_run_automation_e2e(client):
    _seed_approved_plan(client)

    r = client.post("/automations", json={"name": "Test Auto", "plan_id": "plan_e2e"})
    assert r.status_code == 200, r.text
    auto = r.json()

    r = client.post(f"/automations/{auto['id']}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "provisioning"

    # TestClient runs background tasks before returning from the request.
    assert MockRunner.spawn_called
    assert MockRunner.last_plan is not None
    assert MockRunner.last_plan["id"] == "plan_e2e"
    assert MockRunner.teardown_called

    final = client.get(f"/runs/{run['id']}").json()
    assert final["status"] == "completed"
    assert final["live_view_url"] == "http://mock:6080/vnc.html"


def test_run_token_is_never_stored_raw_and_is_revoked(client):
    """The sandbox gets the raw token; the Run row never stores it, and the
    token is revoked once the run finishes so a leaked copy is worthless."""
    _seed_approved_plan(client)
    auto = client.post("/automations", json={"name": "A", "plan_id": "plan_e2e"}).json()
    run = client.post(f"/automations/{auto['id']}/run").json()

    raw_token = MockRunner.last_env["RUN_TOKEN"]
    assert raw_token, "the sandbox must receive a token"

    final = client.get(f"/runs/{run['id']}").json()
    assert raw_token not in json.dumps(final), "raw token must never be persisted"
    assert final["mcp_token_hash"] is None, "token must be revoked when the run ends"


def test_step_timings_come_from_the_sandbox(client):
    """Per-step timings are the sandbox's real clock, not an even split."""
    _seed_approved_plan(client)
    auto = client.post("/automations", json={"name": "A", "plan_id": "plan_e2e"}).json()
    run = client.post(f"/automations/{auto['id']}/run").json()

    step = client.get(f"/runs/{run['id']}").json()["step_executions"][0]
    assert step["started_at"].startswith("2026-01-01T10:00:00")
    assert step["finished_at"].startswith("2026-01-01T10:00:07")
    assert step["attempts"] == 1


def test_run_cost_is_recorded(client):
    """Cost is accumulated from the run's traces rather than left at zero."""
    _seed_approved_plan(client)
    auto = client.post("/automations", json={"name": "A", "plan_id": "plan_e2e"}).json()
    run = client.post(f"/automations/{auto['id']}/run").json()

    cost = client.get(f"/runs/{run['id']}").json()["cost"]
    assert cost["llm_calls"] == 1
    assert cost["input_tokens"] == 1000
    assert cost["output_tokens"] == 200


def test_unapproved_plan_cannot_run(client):
    """A draft plan must never reach a sandbox."""
    plan = _plan("plan_draft", status=PlanStatus.DRAFT)
    client.post("/plans", json=plan.model_dump(mode="json"))
    auto = client.post("/automations", json={"name": "Draft", "plan_id": "plan_draft"}).json()

    MockRunner.spawn_called = False
    r = client.post(f"/automations/{auto['id']}/run")
    assert r.status_code == 409
    assert not MockRunner.spawn_called


def test_invalid_plan_is_rejected_on_upsert(client):
    """Guardrails reject a plan whose loop body names a step that isn't there."""
    bad = Plan(
        id="plan_bad", goal="Broken",
        steps=[Step(id="s1", kind=StepKind.LOOP, description="drain",
                    details={"over": "__drain__", "body": ["does_not_exist"]})],
    )
    r = client.post("/plans", json=bad.model_dump(mode="json"))
    assert r.status_code == 422
    assert "does_not_exist" in r.text


def test_paused_run_can_be_resumed(client):
    """A paused run records the human's answer and continues from that step."""
    _seed_approved_plan(client)
    auto = client.post("/automations", json={"name": "A", "plan_id": "plan_e2e"}).json()

    MockRunner.response = {
        "status": "paused",
        "step_results": [{
            "step_id": "s1", "status": "paused", "detail": "needs a human",
            "pause_reason": "human_input", "extracted": {"case_number": "00001378"},
        }],
        "error": "paused at step s1",
    }
    run = client.post(f"/automations/{auto['id']}/run").json()
    assert client.get(f"/runs/{run['id']}").json()["status"] == "paused_for_input"

    MockRunner.response = {"status": "completed", "step_results": [
        {"step_id": "s1", "status": "succeeded", "detail": "done"}]}
    r = client.post(f"/runs/{run['id']}/resume", json={"response": "use case 00001378"})
    assert r.status_code == 200, r.text
    new_run = r.json()
    assert new_run["id"] != run["id"]

    # The resumed run starts at the paused step, carrying its variables.
    assert MockRunner.last_kwargs["start_at_step_id"] == "s1"
    assert MockRunner.last_kwargs["initial_variables"]["case_number"] == "00001378"
    assert MockRunner.last_kwargs["initial_variables"]["human_response"] == "use case 00001378"

    # The human's answer is recorded on the original run — this is what
    # episodic memory learns from.
    paused = client.get(f"/runs/{run['id']}").json()
    assert paused["interventions"][0]["user_response"] == "use case 00001378"


def test_resume_rejects_a_run_that_is_not_paused(client):
    _seed_approved_plan(client)
    auto = client.post("/automations", json={"name": "A", "plan_id": "plan_e2e"}).json()
    run = client.post(f"/automations/{auto['id']}/run").json()
    r = client.post(f"/runs/{run['id']}/resume", json={"response": "hi"})
    assert r.status_code == 409


def test_runs_are_not_visible_to_another_user(client, monkeypatch):
    """/runs is scoped: another user's run is a 404, never its payload."""
    _seed_approved_plan(client)
    auto = client.post("/automations", json={"name": "A", "plan_id": "plan_e2e"}).json()
    run = client.post(f"/automations/{auto['id']}/run").json()

    from app.api import deps
    from app.schemas.auth import User, UserStatus

    async def _other_user():
        return User(id="someone_else", email="other@example.com", status=UserStatus.ACTIVE)

    client.app.dependency_overrides[deps.get_current_user] = _other_user
    try:
        assert client.get(f"/runs/{run['id']}").status_code == 404
        assert client.get("/runs").json() == []
    finally:
        client.app.dependency_overrides.clear()
