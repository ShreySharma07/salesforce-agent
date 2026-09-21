"""
Regression tests for the pre-launch hardening pass.

Every test here pins a behaviour that was wrong before, and each maps to a
specific reviewer finding:

  * `notify` reported success while sending nothing
  * a run with failed steps counted as a successful run
  * a client could POST a plan with status="approved" and skip review
  * a run re-read the plan at execution time instead of running what was approved
  * a run token stayed valid after its run finished
  * a run token could call MCP tools its plan never declared
  * a resumed run skipped navigation and lost its browser session
  * an answered human_input step paused again forever
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
from app.services.sandbox import SandboxHandle


@pytest.fixture
def db():
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


class MockRunner:
    runner_kind = "mock"
    response: dict = {}
    last_kwargs: dict = {}
    last_plan: dict | None = None

    async def spawn(self, config):
        MockRunner.last_env = dict(config.env)
        return SandboxHandle(sandbox_id="sbx", api_url="http://mock:8000",
                             live_view_url="http://mock:6080/vnc.html", runner_kind="mock")

    async def wait_healthy(self, handle, *, timeout_seconds=60):
        return True

    async def execute_plan(self, handle, plan_dict, **kwargs):
        MockRunner.last_plan = plan_dict
        MockRunner.last_kwargs = kwargs
        return MockRunner.response

    async def teardown(self, handle):
        return None

    async def get_logs(self, handle):
        return ""


@pytest.fixture
def client(db, monkeypatch):
    from app.services import run_executor

    monkeypatch.setattr(run_executor, "get_sandbox_runner", lambda: MockRunner())
    MockRunner.last_kwargs = {}
    MockRunner.last_plan = None
    MockRunner.response = {
        "status": "completed",
        "step_results": [{"step_id": "s1", "status": "succeeded", "detail": "ok"}],
    }
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def _plan(pid="plan_h", steps=None) -> Plan:
    return Plan(
        id=pid, goal="Escalate the case",
        steps=steps or [Step(id="s1", kind=StepKind.NAVIGATE, description="open",
                             details={"url": "https://example.com"})],
    )


def _approved_automation(client, plan: Plan | None = None) -> dict:
    plan = plan or _plan()
    assert client.post("/plans", json=plan.model_dump(mode="json")).status_code == 200
    assert client.post(f"/plans/{plan.id}/approve").status_code == 200
    r = client.post("/automations", json={"name": "A", "plan_id": plan.id})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Approval is server-controlled
# ---------------------------------------------------------------------------

def test_client_cannot_self_approve_a_plan(client):
    """A plan POSTed as already-approved must land in review anyway."""
    sneaky = _plan("plan_sneaky")
    sneaky.status = PlanStatus.APPROVED
    sneaky.approved_by = "somebody-else"

    body = client.post("/plans", json=sneaky.model_dump(mode="json")).json()
    assert body["plan"]["status"] == "pending_approval"
    assert body["plan"]["approved_by"] is None


def test_approval_records_the_authenticated_approver(client):
    client.post("/plans", json=_plan("plan_appr").model_dump(mode="json"))
    approved = client.post("/plans/plan_appr/approve").json()
    me = client.get("/auth/me").json()

    assert approved["status"] == "approved"
    assert approved["approved_by"] == me["id"]
    assert approved["approved_at"] is not None


def test_editing_an_approved_plan_sends_it_back_for_review(client):
    """An old approval must never cover newly-written steps."""
    plan = _plan("plan_edit")
    client.post("/plans", json=plan.model_dump(mode="json"))
    client.post("/plans/plan_edit/approve")

    plan.steps.append(Step(id="s2", kind=StepKind.NAVIGATE, description="extra",
                           details={"url": "https://evil.example.com"}))
    body = client.post("/plans", json=plan.model_dump(mode="json")).json()
    assert body["plan"]["status"] == "pending_approval"


# ---------------------------------------------------------------------------
# Runs execute the approved snapshot
# ---------------------------------------------------------------------------

def test_run_executes_the_approved_snapshot_not_a_later_edit(client):
    """The run record pins exactly what was approved, immune to later edits."""
    plan = _plan("plan_snap")
    auto = _approved_automation(client, plan)
    run = client.post(f"/automations/{auto['id']}/run").json()

    executed = MockRunner.last_plan
    assert executed["id"] == "plan_snap"
    assert len(executed["steps"]) == 1

    # Now rewrite the plan with a step that was never approved.
    plan.steps.append(Step(id="s2", kind=StepKind.NAVIGATE, description="added later",
                           details={"url": "https://added-after-approval.example.com"}))
    assert client.post("/plans", json=plan.model_dump(mode="json")).status_code == 200

    # The finished run still reports the one-step plan it was authorized for.
    snapshot = client.get(f"/runs/{run['id']}").json()["plan_snapshot"]
    assert [s["id"] for s in snapshot["steps"]] == ["s1"]
    assert "added-after-approval" not in str(snapshot)

    # And the live plan really did change, so the assertion above is meaningful.
    live = client.get("/plans/plan_snap").json()
    assert [s["id"] for s in live["steps"]] == ["s1", "s2"]


def test_resumed_run_inherits_the_same_snapshot(client):
    auto = _approved_automation(client, _plan("plan_resume_snap"))
    MockRunner.response = {
        "status": "paused",
        "step_results": [{"step_id": "s1", "status": "paused", "detail": "stuck",
                          "pause_reason": "human_input"}],
    }
    run = client.post(f"/automations/{auto['id']}/run").json()

    MockRunner.response = {"status": "completed", "step_results": []}
    new_run = client.post(f"/runs/{run['id']}/resume", json={"response": "go on"}).json()

    first = client.get(f"/runs/{run['id']}").json()["plan_snapshot"]
    second = client.get(f"/runs/{new_run['id']}").json()["plan_snapshot"]
    assert first == second


# ---------------------------------------------------------------------------
# Honest counters
# ---------------------------------------------------------------------------

def test_partial_run_is_not_counted_as_a_success(client):
    """A run with a failed step must not inflate successful_runs."""
    auto = _approved_automation(client, _plan("plan_partial"))
    MockRunner.response = {
        "status": "completed",
        "step_results": [
            {"step_id": "s1", "status": "succeeded", "detail": "ok"},
            {"step_id": "s2", "status": "failed", "detail": "nope"},
        ],
    }
    client.post(f"/automations/{auto['id']}/run")

    after = client.get(f"/automations/{auto['id']}").json()
    assert after["total_runs"] == 1
    assert after["successful_runs"] == 0
    assert after["partial_runs"] == 1


def test_skipped_step_also_prevents_a_clean_success(client):
    """An unimplemented notify is skipped, so the run is not a clean success."""
    auto = _approved_automation(client, _plan("plan_skip"))
    MockRunner.response = {
        "status": "completed",
        "step_results": [{"step_id": "s1", "status": "skipped",
                          "detail": "notify is not implemented"}],
    }
    run = client.post(f"/automations/{auto['id']}/run").json()
    assert client.get(f"/runs/{run['id']}").json()["status"] == "completed_with_failures"


# ---------------------------------------------------------------------------
# Run-token authority
# ---------------------------------------------------------------------------

def _seed_run(status: RunStatus, token: str, *, plan_snapshot=None) -> None:
    from app.services.run_repo import get_repository

    repo = get_repository()

    async def _seed():
        await repo.save_automation(
            Automation(id="auto_tok", user_id="owner", name="A", plan_id="plan_tok"),
            user_id="owner")
        await repo.save_run(Run(
            id=f"run_{status.value}", automation_id="auto_tok", plan_version=1,
            triggered_by=RunTrigger.MANUAL, status=status,
            mcp_token_hash=hashlib.sha256(token.encode()).hexdigest(),
            plan_snapshot=plan_snapshot,
        ), user_id="owner")

    asyncio.run(_seed())


def test_token_for_a_finished_run_is_rejected(client):
    token = secrets.token_urlsafe(16)
    _seed_run(RunStatus.COMPLETED, token, plan_snapshot={"steps": []})
    r = client.post("/mcp/mock/create_record",
                    json={"args": {"type": "Lead"}, "run_id": "run_completed"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert "no longer valid" in r.text


def test_token_cannot_call_a_tool_the_plan_never_declared(client):
    """The token proves which run is calling; the plan defines what it may do."""
    token = secrets.token_urlsafe(16)
    _seed_run(RunStatus.RUNNING, token, plan_snapshot={"steps": [
        {"kind": "mcp_call", "details": {"server": "mock", "tool": "query"}},
    ]})
    r = client.post("/mcp/mock/create_record",
                    json={"args": {"type": "Lead"}, "run_id": "run_running"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert "does not declare" in r.text


def test_token_may_call_a_tool_the_plan_declared(client):
    token = secrets.token_urlsafe(16)
    _seed_run(RunStatus.RUNNING, token, plan_snapshot={"steps": [
        {"kind": "mcp_call", "details": {"server": "mock", "tool": "create_record"}},
    ]})
    r = client.post("/mcp/mock/create_record",
                    json={"args": {"type": "Lead"}, "run_id": "run_running"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Sandbox executor behaviour (pure functions, no browser needed)
# ---------------------------------------------------------------------------

def test_notify_does_not_report_success_it_did_not_achieve():
    from sandbox_agent.executor import _run_step
    from sandbox_agent.schemas import RunRequest, Step as SbStep, StepKind as SbKind, Plan as SbPlan

    step = SbStep(id="n1", kind=SbKind.NOTIFY, description="tell the team",
                  details={"channel": "slack", "message": "case escalated"})
    req = RunRequest(plan=SbPlan(id="p", goal="g", steps=[step]))

    result = _run_step(None, None, step, req, variables={})
    assert result.status == "skipped", "notify sends nothing, so it must not claim success"
    assert "not implemented" in result.detail
    assert "NO message was sent" in result.detail


def test_answered_human_input_advances_instead_of_pausing_again():
    from sandbox_agent.executor import _run_step
    from sandbox_agent.schemas import RunRequest, Step as SbStep, StepKind as SbKind, Plan as SbPlan

    step = SbStep(id="h1", kind=SbKind.HUMAN_INPUT, description="which case?",
                  details={"prompt": "which case?"})
    req = RunRequest(plan=SbPlan(id="p", goal="g", steps=[step]))

    paused = _run_step(None, None, step, req, variables={})
    assert paused.status == "paused"

    answered = _run_step(None, None, step, req,
                         variables={"human_response": "case 00001378"})
    assert answered.status == "succeeded"
    assert "00001378" in answered.detail


def test_context_steps_are_replayed_on_resume_but_data_steps_are_not():
    """A resumed run gets a blank browser, so navigation must be replayed."""
    from sandbox_agent.executor import _is_context_step
    from sandbox_agent.schemas import Step as SbStep, StepKind as SbKind

    navigate = SbStep(id="a", kind=SbKind.NAVIGATE, description="go",
                      details={"url": "https://x"})
    open_app = SbStep(id="b", kind=SbKind.UI_ACTION, description="open salesforce",
                      details={"intent": "open_app", "target_description": "Salesforce"})
    wait = SbStep(id="c", kind=SbKind.WAIT, description="settle", details={"seconds": 1})
    edit = SbStep(id="d", kind=SbKind.UI_ACTION, description="set status",
                  details={"intent": "fill", "target_description": "Status"})
    sequence = SbStep(id="e", kind=SbKind.SEQUENCE, description="inline edit",
                      details={"steps": []})

    assert _is_context_step(navigate) and _is_context_step(open_app) and _is_context_step(wait)
    assert not _is_context_step(edit), "a field edit must NOT be repeated on resume"
    assert not _is_context_step(sequence), "a sequence must NOT be repeated on resume"


def test_resume_target_inside_a_loop_resolves_to_the_loop():
    """The linear walk never visits loop children, so resuming at one directly
    would skip the entire plan."""
    from sandbox_agent.executor import _resolve_resume_target
    from sandbox_agent.schemas import Step as SbStep, StepKind as SbKind

    body_step = SbStep(id="child", kind=SbKind.UI_ACTION, description="process", details={})
    loop = SbStep(id="loop1", kind=SbKind.LOOP, description="drain",
                  details={"over": "__drain__", "body": ["child"]})
    step_by_id = {"loop1": loop, "child": body_step}

    assert _resolve_resume_target("child", step_by_id, {"child"}) == "loop1"
    assert _resolve_resume_target("loop1", step_by_id, {"child"}) == "loop1"


def test_secrets_are_redacted_from_docker_logs():
    from app.services.sandbox.local_docker import _redact_cmd, _redact_text

    cmd = ["docker", "run", "-e", "RUN_TOKEN=supersecret",
           "-e", "LLM_PROVIDER=gemini", "img"]
    rendered = _redact_cmd(cmd)
    assert "supersecret" not in rendered
    assert "LLM_PROVIDER=gemini" in rendered, "non-secret vars stay visible for debugging"
    assert "AIzaSECRET" not in _redact_text("boom GEMINI_API_KEY=AIzaSECRET")


def test_a_run_that_fails_to_spawn_is_still_counted(client, monkeypatch):
    """A run that dies before the sandbox exists must still show up.

    The counters used to live in the happy path, so a container that failed to
    spawn left the automation reporting zero runs immediately after a failure
    the user had just watched happen.
    """
    from app.services import run_executor

    class ExplodingRunner(MockRunner):
        async def spawn(self, config):
            raise RuntimeError("docker run failed (exit 1)")

    auto = _approved_automation(client, _plan("plan_boom"))
    monkeypatch.setattr(run_executor, "get_sandbox_runner", lambda: ExplodingRunner())

    run = client.post(f"/automations/{auto['id']}/run").json()

    stored = client.get(f"/runs/{run['id']}").json()
    assert stored["status"] == "failed"
    assert stored["finished_at"] is not None
    assert stored["mcp_token_hash"] is None, "token must be revoked even on a crash"

    after = client.get(f"/automations/{auto['id']}").json()
    assert after["total_runs"] == 1
    assert after["failed_runs"] == 1
    assert after["successful_runs"] == 0
