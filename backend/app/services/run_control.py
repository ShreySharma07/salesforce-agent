"""
Run lifecycle control: cancel, resume, and orphan cleanup.

Before this existed a paused run was a dead end — the plan stopped, the
container was torn down, and nothing could carry the run forward, so
`Run.interventions` was never written and episodic memory could never learn
what a human did to unstick the agent. These three operations close that loop.

Resume is deliberately NOT "reattach to the old container". Sandboxes are
one-per-run and destroyed on teardown, so resuming starts a FRESH run that
carries the earlier run's variables forward.

The fresh container also has a blank browser: no session, no open record. So
the new run does not simply jump to the paused step. Steps before it are
handled in two ways: data-changing steps are skipped, because the paused run
already performed them, while context steps (open_app, navigate, wait) are
REPLAYED to rebuild the session and get back to the right page. See
`_is_context_step` in the sandbox executor.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks

from app.schemas.run import HumanIntervention, Run, RunStatus, RunTrigger
from app.services.run_repo import get_repository

log = logging.getLogger(__name__)

# Statuses that mean "this run is still in flight somewhere".
IN_FLIGHT_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.PROVISIONING,
    RunStatus.RUNNING,
}


async def fail_orphaned_runs() -> int:
    """Close out runs abandoned by a previous backend process.

    Runs execute as in-process background tasks, so a restart or crash leaves
    their rows in PROVISIONING/RUNNING with no container behind them and
    nothing to ever finish them. Called once at startup; returns how many
    rows it closed.
    """
    repo = get_repository()
    closed = 0
    for run in await repo.list_runs():
        if run.status not in IN_FLIGHT_STATUSES:
            continue
        run.status = RunStatus.FAILED
        run.error = (
            "run was abandoned when the backend process stopped; its sandbox "
            "is gone. Start a new run."
        )
        run.finished_at = datetime.utcnow()
        await repo.save_run(run)
        closed += 1
    return closed


async def cancel_run(run: Run, *, user_id: str) -> Run | None:
    """Tear down an in-flight run's sandbox and mark it CANCELED.

    Returns the updated Run, or None if it was not in a cancelable state
    (already finished, already canceled) so the caller can answer 409.
    """
    if run.status not in IN_FLIGHT_STATUSES:
        return None

    if run.sandbox_id:
        # Tear the container down directly by id — the handle object lives in
        # the background task, but teardown only needs the id and is
        # idempotent on an already-dead container.
        from app.services.sandbox import SandboxHandle, get_sandbox_runner

        runner = get_sandbox_runner()
        try:
            await runner.teardown(SandboxHandle(
                sandbox_id=run.sandbox_id,
                api_url="", live_view_url="", runner_kind=runner.runner_kind,
            ))
        except Exception as e:
            log.warning("cancel_run: teardown of %s failed: %s", run.sandbox_id, e)

    run.status = RunStatus.CANCELED
    run.finished_at = datetime.utcnow()
    run.error = run.error or "canceled by user"
    run.mcp_token_hash = None   # revoke: the container is gone
    await get_repository().save_run(run, user_id=user_id)
    log.info("run %s canceled by %s", run.id, user_id)
    return run


def paused_step_id(run: Run) -> str | None:
    """The step a paused run stopped on, i.e. where a resume should begin."""
    for se in reversed(run.step_executions):
        if se.status == "paused":
            return se.step_id
    # A run can also pause because a failing step's policy said `pause`.
    for se in reversed(run.step_executions):
        if se.status == "failed":
            return se.step_id
    return None


def collected_variables(run: Run) -> dict[str, Any]:
    """Every variable the paused run extracted, in the order it found them."""
    variables: dict[str, Any] = {}
    for se in run.step_executions:
        variables.update(se.extracted_variables or {})
    return variables


async def resume_run(
    run: Run, *, response: str, user_id: str, background: BackgroundTasks,
) -> Run | None:
    """Answer a paused run and continue the work in a fresh run.

    Records the human's answer as a HumanIntervention on the paused run —
    this is the highest-value episodic memory the system collects, since it
    captures what a person did when the agent was stuck — then queues a new
    run that starts at the paused step with the collected variables plus
    `${human_response}`.

    Returns the new Run, or None if the run was not paused (caller → 409).
    """
    if run.status != RunStatus.PAUSED_FOR_INPUT:
        return None

    repo = get_repository()
    resume_at = paused_step_id(run)

    # 1. Record the intervention on the paused run, then close it out.
    pause_reason = next(
        (se.pause_reason or se.error or "" for se in reversed(run.step_executions)
         if se.status in ("paused", "failed")),
        run.error or "agent paused for human input",
    )
    run.interventions.append(HumanIntervention(
        at=datetime.utcnow(),
        reason=pause_reason,
        user_response=response,
        responded_at=datetime.utcnow(),
    ))
    run.summary = (run.summary or "") + " — resumed by user"
    await repo.save_run(run, user_id=user_id)

    # 2. Reflect on the ANSWERED run so the human's fix reaches episodic
    # memory. The original reflection ran before the answer existed.
    auto = await repo.get_automation(run.automation_id)
    plan = await repo.get_plan(auto.plan_id) if auto else None
    if plan is not None:
        try:
            from app.services.memory.integration import reflect_after_run

            await reflect_after_run(
                user_id=user_id, plan=plan,
                step_executions=run.step_executions,
                interventions=run.interventions,
                run_id=run.id, run_succeeded=False,
            )
        except Exception as e:
            log.warning("memory: reflection on resumed run %s failed: %s", run.id, e)

    # 3. Queue a fresh run that picks up where this one stopped.
    variables = collected_variables(run)
    variables["human_response"] = response

    new_run = Run(
        id=f"run_{uuid.uuid4().hex[:10]}",
        automation_id=run.automation_id,
        plan_version=run.plan_version,
        triggered_by=RunTrigger.MANUAL,
        triggered_by_user=user_id,
        status=RunStatus.PROVISIONING,
        # Continue the SAME approved plan the paused run was executing. Re-
        # reading the plan here would let an edit made during the pause slip
        # into a run the user already approved and already partly executed.
        plan_snapshot=run.plan_snapshot,
    )
    await repo.save_run(new_run, user_id=user_id)

    from app.services.run_executor import execute_run

    background.add_task(
        execute_run, new_run.id,
        start_at_step_id=resume_at,
        initial_variables=variables,
    )
    log.info("run %s resumed as %s (starting at step %s)", run.id, new_run.id, resume_at)
    return new_run
