"""
Run execution — spawn a sandbox, feed it the Plan, persist what came back.

This is the heart of the platform, and it lives in the service layer (not in
a route handler) because two callers need it: POST /automations/{id}/run and
POST /runs/{id}/resume. Keeping it here also honors the layering rule — the
API never touches the sandbox or the DB directly.

One run, start to finish:
  1. mint a RUN_TOKEN (store only its SHA-256 hash on the Run)
  2. build the sandbox env: backend URL, run id, token, per-provider frontdoor
  3. prime per-step hints from memory
  4. spawn the container, wait for health, POST the Plan
  5. translate the sandbox's response into the Run record (status, per-step
     executions with their real timings, cost)
  6. reflect on the run so the next one starts smarter
  7. tear the container down — always

Concurrency is capped (`max_concurrent_runs`): local_docker maps a host port
per container, so unbounded spawning exhausts ports and memory.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import traceback
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.core.budget import estimate_cost, get_budget_tracker
from app.db.base import get_sessionmaker
from app.schemas.plan import Plan
from app.schemas.run import Run, RunStatus, StepExecution
from app.services.memory.integration import prime_steps, reflect_after_run
from app.services.run_repo import get_repository
from app.services.sandbox import SpawnConfig, get_sandbox_runner
from app.services.vault import get_credential_row

log = logging.getLogger(__name__)

# Providers the backend mints a per-run frontdoor path for. A provider only
# gets one if THIS user actually has an OAuth credential in the vault.
_FRONTDOOR_PROVIDERS = ["salesforce"]

# Sandbox step status -> StepExecution status. The sandbox never reports
# pending/running; those are backend-only states.
_STEP_STATUS_MAP = {
    "succeeded": "succeeded",
    "failed": "failed",
    "skipped": "skipped",
    "paused": "paused",
}

_run_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    """Process-wide cap on concurrently running sandboxes."""
    global _run_slots
    if _run_slots is None:
        _run_slots = asyncio.Semaphore(get_settings().max_concurrent_runs)
    return _run_slots


def _backend_url_for_sandbox() -> str:
    """Return the URL the sandbox should use to reach this backend.

    When running local_docker and the configured public URL points at
    localhost, the container can't resolve 'localhost' as the host machine —
    use Docker's magic hostname instead.
    """
    settings = get_settings()
    if (
        settings.sandbox_runner == "local_docker"
        and "localhost" in settings.public_backend_base_url
    ):
        return f"http://host.docker.internal:{settings.backend_port}"
    return settings.public_backend_base_url


async def _frontdoor_env(user_id: str, run_token: str) -> dict[str, str]:
    """Per-provider frontdoor paths for the providers this user has connected.

    The sandbox's `open_app` action navigates to one of these; the backend
    validates the token and 302s into a logged-in session. The provider's
    real access token never leaves the backend.
    """
    env: dict[str, str] = {}
    async with get_sessionmaker()() as session:
        for provider in _FRONTDOOR_PROVIDERS:
            row = await get_credential_row(session, user_id=user_id, provider=provider)
            if row is not None and row.kind == "oauth":
                # ret_url lands the browser on the neutral home page regardless
                # of which app was open in the last session.
                env[f"{provider.upper()}_FRONTDOOR_PATH"] = (
                    f"/sandbox/frontdoor/{provider}"
                    f"?run_token={run_token}"
                    f"&ret_url=%2Flightning%2Fpage%2Fhome"
                )
    return env


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp reported by the sandbox, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _build_step_executions(
    step_results: list[dict], *, run_start: datetime, run_end: datetime,
) -> list[StepExecution]:
    """Convert the sandbox's step results into StepExecution rows.

    Prefers the real per-step timings the sandbox reports. Only when they are
    absent (an older sandbox image) does it fall back to spreading the run's
    elapsed time evenly across steps — an estimate, and marked as such here so
    nobody mistakes it for measurement.
    """
    out: list[StepExecution] = []
    n = max(len(step_results), 1)
    even_slice = (run_end - run_start) / n

    for i, sr in enumerate(step_results):
        sr_status = sr.get("status", "failed")
        started = _parse_ts(sr.get("started_at")) or (run_start + even_slice * i)
        finished = _parse_ts(sr.get("finished_at")) or (run_start + even_slice * (i + 1))
        out.append(StepExecution(
            step_id=sr["step_id"],
            started_at=started,
            finished_at=finished,
            status=_STEP_STATUS_MAP.get(sr_status, "failed"),
            attempts=int(sr.get("attempts", 1) or 1),
            error=sr.get("detail") if sr_status != "succeeded" else None,
            extracted_variables=sr.get("extracted", {}) or {},
            trace=sr.get("trace", []) or [],
            pause_reason=sr.get("pause_reason"),
        ))
    return out


def _apply_cost(run: Run) -> None:
    """Fill in the run's cost from the budget ledger, or from its traces.

    The LLM proxy meters every sandbox model call for this run, so its ledger
    is authoritative (it counts calls the traces may not carry). If the ledger
    is empty — e.g. an all-`sequence` run — fall back to summing the ReAct
    trace tokens and pricing them at the configured model's rate.
    """
    spend = get_budget_tracker().clear(run.id)
    if spend is not None and spend.calls:
        run.cost.llm_calls = spend.calls
        run.cost.input_tokens = spend.input_tokens
        run.cost.output_tokens = spend.output_tokens
        run.cost.cost_usd = round(spend.cost_usd, 6)
        return

    calls = in_tok = out_tok = 0
    for se in run.step_executions:
        for it in se.trace:
            calls += 1
            in_tok += it.input_tokens
            out_tok += it.output_tokens
    run.cost.llm_calls = calls
    run.cost.input_tokens = in_tok
    run.cost.output_tokens = out_tok
    run.cost.cost_usd = round(estimate_cost(get_settings().llm_model, in_tok, out_tok), 6)


def _status_for(sandbox_status: str | None, step_results: list[dict], error: str | None) -> RunStatus:
    """Map the sandbox's terminal status onto a RunStatus.

    'aborted' is overloaded on the sandbox side — it covers budget/quota
    exhaustion as well as hitting max_steps — so the error text decides
    between BUDGET_EXCEEDED and FAILED.
    """
    if sandbox_status == "completed":
        has_failures = any(sr.get("status") != "succeeded" for sr in step_results)
        return RunStatus.COMPLETED_WITH_FAILURES if has_failures else RunStatus.COMPLETED
    if sandbox_status == "paused":
        return RunStatus.PAUSED_FOR_INPUT
    if sandbox_status == "aborted":
        low = (error or "").lower()
        if "quota" in low or "budget" in low:
            return RunStatus.BUDGET_EXCEEDED
    return RunStatus.FAILED


async def execute_run(
    run_id: str,
    *,
    start_at_step_id: str | None = None,
    initial_variables: dict[str, Any] | None = None,
) -> None:
    """Background task: run one Run to completion and persist the outcome.

    `start_at_step_id` / `initial_variables` are set when resuming a paused
    run: the sandbox fast-forwards to that step with the earlier run's
    collected variables, instead of replaying everything from the top.

    Never raises — a failure is recorded on the Run and the container is torn
    down in every path.
    """
    settings = get_settings()
    repo = get_repository()
    runner = get_sandbox_runner()
    handle = None

    run = await repo.get_run(run_id)
    if run is None:
        log.error("execute_run: run %s disappeared before it started", run_id)
        return
    auto = await repo.get_automation(run.automation_id)
    # The background task runs detached, so ownership comes from the
    # automation it loads. Used to stamp the run and to scope memory.
    user_id = (auto.user_id if auto else None) or settings.default_user_id
    plan: Plan | None = await repo.get_plan(auto.plan_id) if auto else None
    if plan is None:
        run.status = RunStatus.FAILED
        run.error = "linked plan disappeared"
        run.finished_at = datetime.utcnow()
        await repo.save_run(run, user_id=user_id)
        return

    plan_for_memory = plan
    async with _slots():
        try:
            # Per-run token: only the hash is persisted; the plaintext goes to
            # the sandbox once and is never stored.
            run_token = secrets.token_urlsafe(32)
            run.mcp_token_hash = hashlib.sha256(run_token.encode()).hexdigest()
            await repo.save_run(run, user_id=user_id)

            config = SpawnConfig(
                image=settings.sandbox_image,
                env={
                    **settings.llm_env_for_sandbox(),
                    "BACKEND_MCP_URL": _backend_url_for_sandbox(),
                    "RUN_ID": run.id,
                    "RUN_TOKEN": run_token,
                    **await _frontdoor_env(user_id, run_token),
                },
                dev_mount=settings.sandbox_dev_mount,
            )
            handle = await runner.spawn(config)

            run.sandbox_id = handle.sandbox_id
            run.live_view_url = handle.live_view_url
            run.started_at = datetime.utcnow()
            await repo.save_run(run, user_id=user_id)

            if not await runner.wait_healthy(handle, timeout_seconds=60):
                logs = await runner.get_logs(handle)
                run.status = RunStatus.FAILED
                run.error = (
                    f"sandbox failed to become healthy within 60s. "
                    f"Container logs (last lines):\n{logs[-3000:]}"
                )
                run.finished_at = datetime.utcnow()
                return

            run.status = RunStatus.RUNNING
            await repo.save_run(run, user_id=user_id)

            # MEMORY (priming): per-step hints from past runs. Best-effort —
            # a memory failure must never break a run.
            memory_hints: dict[str, str] = {}
            try:
                memory_hints = await prime_steps(user_id=user_id, plan=plan)
            except Exception as e:
                log.warning("memory: priming failed for run %s: %s", run.id, e)

            result = await runner.execute_plan(
                handle,
                plan.model_dump(mode="json"),
                max_steps=settings.sandbox_default_max_steps,
                max_seconds=settings.sandbox_default_max_seconds,
                memory_hints=memory_hints,
                start_at_step_id=start_at_step_id,
                initial_variables=initial_variables or {},
            )

            step_results = result.get("step_results", []) or []
            run.error = result.get("error") or None
            run.status = _status_for(result.get("status"), step_results, run.error)
            run.finished_at = datetime.utcnow()
            run.step_executions.extend(_build_step_executions(
                step_results,
                run_start=run.started_at or run.finished_at,
                run_end=run.finished_at,
            ))
            _apply_cost(run)
            run.summary = (
                f"{sum(1 for s in run.step_executions if s.status == 'succeeded')} of "
                f"{len(run.step_executions)} steps succeeded"
            )

            if auto is not None:
                auto.total_runs += 1
                if run.status in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_FAILURES):
                    auto.successful_runs += 1
                auto.last_run_at = run.finished_at
                await repo.save_automation(auto, user_id=user_id)

        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            run.finished_at = datetime.utcnow()
        finally:
            await repo.save_run(run, user_id=user_id)
            if handle is not None:
                try:
                    await runner.teardown(handle)
                except Exception:
                    pass  # an already-dead container is fine

    # MEMORY (reflection) runs OUTSIDE the try/finally that owns the run's
    # status: learning from a run must never be able to flip a finished run
    # to FAILED, and it must not hold a concurrency slot while it works.
    try:
        await reflect_after_run(
            user_id=user_id,
            plan=plan_for_memory,
            step_executions=run.step_executions,
            interventions=run.interventions,
            run_id=run.id,
            run_succeeded=run.status == RunStatus.COMPLETED,
        )
    except Exception as e:
        log.warning("memory: reflection failed for run %s: %s", run.id, e)
