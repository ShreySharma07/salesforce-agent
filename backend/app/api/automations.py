"""
Automation endpoints — the user-facing object. An Automation wraps a
Plan with a user-given name, schedule, etc.

The big one here:

  POST /automations/{automation_id}/run
        Spawns a sandbox, executes the linked Plan, returns a Run object
        with sandbox_id and live_view_url.

This is the unified entry point that ties video → plan → execution into
one continuous flow.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.schemas.automation import Automation, AutomationStatus
from app.schemas.run import Run, RunStatus, RunTrigger, StepExecution
from app.services.run_repo import get_repository
from app.services.sandbox import SpawnConfig, get_sandbox_runner

router = APIRouter(prefix="/automations", tags=["automations"])


class CreateAutomationBody(BaseModel):
    name: str
    plan_id: str
    user_id: str = "local_user"
    description: str | None = None


@router.get("", response_model=list[Automation])
async def list_automations():
    return await get_repository().list_automations()


@router.get("/{automation_id}", response_model=Automation)
async def get_automation(automation_id: str):
    auto = await get_repository().get_automation(automation_id)
    if auto is None:
        raise HTTPException(404, f"automation {automation_id} not found")
    return auto


@router.post("", response_model=Automation)
async def create_automation(body: CreateAutomationBody):
    repo = get_repository()
    plan = await repo.get_plan(body.plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {body.plan_id} not found")
    auto = Automation(
        id=f"auto_{uuid.uuid4().hex[:10]}",
        user_id=body.user_id,
        name=body.name,
        description=body.description,
        plan_id=plan.id,
        plan_version=plan.version,
        status=AutomationStatus.ACTIVE,
    )
    await repo.save_automation(auto)
    return auto


@router.post("/{automation_id}/run", response_model=Run)
async def run_automation(
    automation_id: str,
    background: BackgroundTasks,
):
    """Spawn a sandbox, execute the plan inside it, return a Run record.
    The actual execution happens asynchronously - this endpoint returns
    quickly with the live_view_url so the user can watch."""
    settings = get_settings()
    repo = get_repository()

    auto = await repo.get_automation(automation_id)
    if auto is None:
        raise HTTPException(404, f"automation {automation_id} not found")
    plan = await repo.get_plan(auto.plan_id)
    if plan is None:
        raise HTTPException(404, f"linked plan {auto.plan_id} missing")

    run = Run(
        id=f"run_{uuid.uuid4().hex[:10]}",
        automation_id=auto.id,
        plan_version=plan.version,
        triggered_by=RunTrigger.MANUAL,
        status=RunStatus.PROVISIONING,
    )
    await repo.save_run(run)

    # Schedule the actual execution in the background. The endpoint
    # returns the Run with PROVISIONING status — the dashboard polls
    # GET /runs/{id} for updates.
    background.add_task(
        _execute_automation_in_sandbox,
        run.id,
    )
    return run


async def _execute_automation_in_sandbox(run_id: str) -> None:
    """Background task: spawn sandbox, run plan, update Run record."""
    settings = get_settings()
    repo = get_repository()
    runner = get_sandbox_runner()
    handle = None

    run = await repo.get_run(run_id)
    if run is None:
        return  # shouldn't happen, but defensive
    auto = await repo.get_automation(run.automation_id)
    plan = await repo.get_plan(auto.plan_id) if auto else None
    if plan is None:
        run.status = RunStatus.FAILED
        run.error = "linked plan disappeared"
        await repo.save_run(run)
        return

    try:
        # Spawn sandbox with API keys auto-injected from backend env
        config = SpawnConfig(
            image=settings.sandbox_image,
            env=settings.llm_env_for_sandbox(),
            dev_mount=settings.sandbox_dev_mount,
        )
        handle = await runner.spawn(config)

        run.sandbox_id = handle.sandbox_id
        run.live_view_url = handle.live_view_url
        run.started_at = datetime.utcnow()
        await repo.save_run(run)

        healthy = await runner.wait_healthy(handle, timeout_seconds=60)
        if not healthy:
            logs = await runner.get_logs(handle)
            run.status = RunStatus.FAILED
            run.error = (
                f"sandbox failed to become healthy within 60s. "
                f"Container logs (last lines):\n{logs[-3000:]}"
            )
            await repo.save_run(run)
            return

        run.status = RunStatus.RUNNING
        await repo.save_run(run)

        result = await runner.execute_plan(
            handle,
            plan.model_dump(mode="json"),
            max_steps=settings.sandbox_default_max_steps,
            max_seconds=settings.sandbox_default_max_seconds,
        )

        # Translate sandbox response into our Run model
        if result.get("status") == "completed":
            has_failures = any(
                sr.get("status") != "succeeded"
                for sr in result.get("step_results", [])
            )
            run.status = (
                RunStatus.COMPLETED_WITH_FAILURES if has_failures
                else RunStatus.COMPLETED
            )
        else:
            run.status = RunStatus.FAILED
        run_start = run.started_at or datetime.utcnow()
        elapsed_per_step = (
            (datetime.utcnow() - run_start) / max(len(result.get("step_results", [])), 1)
        )
        for i, sr in enumerate(result.get("step_results", [])):
            run.step_executions.append(StepExecution(
                step_id=sr["step_id"],
                started_at=run_start + elapsed_per_step * i,
                finished_at=run_start + elapsed_per_step * (i + 1),
                status="succeeded" if sr["status"] == "succeeded" else "failed",
                error=sr.get("detail") if sr["status"] == "failed" else None,
                extracted_variables=sr.get("extracted", {}),
            ))
        run.summary = (
            f"{sum(1 for s in run.step_executions if s.status == 'succeeded')} of "
            f"{len(run.step_executions)} steps succeeded"
        )
        if result.get("error"):
            run.error = result["error"]
        run.finished_at = datetime.utcnow()

        # Bump per-automation counters
        auto.total_runs += 1
        if run.status in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_FAILURES):
            auto.successful_runs += 1
        auto.last_run_at = run.finished_at
        await repo.save_automation(auto)

    except Exception as e:
        import traceback
        run.status = RunStatus.FAILED
        run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        run.finished_at = datetime.utcnow()
    finally:
        await repo.save_run(run)
        if handle is not None:
            try:
                await runner.teardown(handle)
            except Exception:
                pass  # already-dead container is fine