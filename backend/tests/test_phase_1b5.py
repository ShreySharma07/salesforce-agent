"""
Tests for Phase 1b.5: sandbox abstraction, repository, API endpoints.

These don't actually spawn Docker containers — that's an integration test.
We mock the SandboxRunner to verify the wiring is correct.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.automation import Automation
from app.schemas.plan import Plan, PlanStatus, Step, StepKind
from app.services.run_repo import JsonFileRepo
from app.services.sandbox import SandboxHandle


# ---------- Repository tests ------------------------------------------------

@pytest.fixture
def tmp_repo(monkeypatch):
    """Fresh JsonFileRepo with a temp directory. Also points the global
    factory at the same temp dir so API endpoints see our records."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("LOCAL_STORAGE_ROOT", tmp)
        get_settings.cache_clear()  # reload settings with new env
        yield JsonFileRepo(Path(tmp))
    get_settings.cache_clear()


async def test_save_and_get_plan(tmp_repo):
    plan = Plan(
        id="plan_test_001",
        goal="Test goal",
        steps=[Step(id="s1", kind=StepKind.NAVIGATE, description="Open SF",
                    details={"url": "https://example.com"})],
    )
    await tmp_repo.save_plan(plan)
    loaded = await tmp_repo.get_plan("plan_test_001")
    assert loaded is not None
    assert loaded.goal == "Test goal"
    assert loaded.steps[0].id == "s1"


async def test_get_missing_plan_returns_none(tmp_repo):
    assert await tmp_repo.get_plan("nope") is None


async def test_list_plans_sorted(tmp_repo):
    for i in range(3):
        await tmp_repo.save_plan(Plan(id=f"plan_{i}", goal=f"goal {i}"))
    plans = await tmp_repo.list_plans()
    assert len(plans) == 3
    assert {p.id for p in plans} == {"plan_0", "plan_1", "plan_2"}


async def test_save_and_get_automation(tmp_repo):
    auto = Automation(
        id="auto_001", user_id="u1", name="Daily Triage", plan_id="plan_001",
    )
    await tmp_repo.save_automation(auto)
    loaded = await tmp_repo.get_automation("auto_001")
    assert loaded.name == "Daily Triage"


# ---------- API tests with mocked sandbox -----------------------------------

class MockRunner:
    """Stand-in for SandboxRunner. Records calls so tests can verify wiring."""
    runner_kind = "mock"
    spawn_called = False
    teardown_called = False
    last_plan = None

    async def spawn(self, config):
        MockRunner.spawn_called = True
        return SandboxHandle(
            sandbox_id="sandbox_test",
            api_url="http://mock:8000",
            live_view_url="http://mock:6080/vnc.html",
            runner_kind="mock",
        )

    async def wait_healthy(self, handle, *, timeout_seconds=60):
        return True

    async def execute_plan(self, handle, plan_dict, *, max_steps=50, max_seconds=600):
        MockRunner.last_plan = plan_dict
        return {
            "status": "completed",
            "step_results": [
                {"step_id": "s1", "status": "succeeded", "detail": "did the thing"},
            ],
            "final_url": "https://example.com",
            "elapsed_seconds": 1.0,
        }

    async def teardown(self, handle):
        MockRunner.teardown_called = True


@pytest.fixture
def client(tmp_repo, monkeypatch):
    """FastAPI TestClient with sandbox runner replaced by MockRunner."""
    from app.services import sandbox as sandbox_pkg
    monkeypatch.setattr(sandbox_pkg, "get_sandbox_runner", lambda: MockRunner())
    # Also patch the import inside the automations module
    from app.api import automations
    monkeypatch.setattr(automations, "get_sandbox_runner", lambda: MockRunner())

    MockRunner.spawn_called = False
    MockRunner.teardown_called = False
    MockRunner.last_plan = None

    from app.main import create_app
    return TestClient(create_app())


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "llm_provider" in body
    assert "sandbox_runner" in body


def test_create_and_run_automation_e2e(client):
    # 1. Save a plan
    plan_data = Plan(
        id="plan_e2e",
        goal="Test e2e",
        status=PlanStatus.APPROVED,
        steps=[Step(id="s1", kind=StepKind.NAVIGATE, description="Go",
                    details={"url": "https://example.com"})],
    )
    asyncio.run(JsonFileRepo(Path(os.environ["LOCAL_STORAGE_ROOT"])).save_plan(plan_data))

    # 2. Create automation linked to the plan
    r = client.post("/automations", json={
        "name": "Test Auto", "plan_id": "plan_e2e", "user_id": "u1",
    })
    assert r.status_code == 200, r.text
    auto = r.json()

    # 3. Run it - should kick off background work and return immediately
    r = client.post(f"/automations/{auto['id']}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "provisioning"

    # 4. Background task runs synchronously in TestClient
    #    Verify the sandbox was spawned, plan was sent, teardown called
    assert MockRunner.spawn_called
    assert MockRunner.last_plan is not None
    assert MockRunner.last_plan["id"] == "plan_e2e"
    assert MockRunner.teardown_called

    # 5. Re-fetch run, should be COMPLETED
    r = client.get(f"/runs/{run['id']}")
    assert r.status_code == 200
    final = r.json()
    assert final["status"] == "completed"
    assert final["live_view_url"] == "http://mock:6080/vnc.html"
    assert len(final["step_executions"]) == 1