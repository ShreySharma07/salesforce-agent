"""
Automation endpoints — the user-facing object. An Automation wraps a Plan
with a user-given name, schedule, etc.

The big one here:

  POST /automations/{automation_id}/run
        Queues a run: creates the Run record and returns immediately with
        PROVISIONING. The sandbox spawn, plan execution, and persistence all
        happen in the background (app/services/run_executor.py), so the
        dashboard can poll GET /runs/{id} and watch the live view.

This is the unified entry point that ties video → plan → execution into one
continuous flow.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user, get_scoped_repo_dep
from app.core.guardrails import validate_plan
from app.schemas.auth import User
from app.schemas.automation import Automation, AutomationStatus
from app.schemas.plan import PlanStatus
from app.schemas.run import Run, RunStatus, RunTrigger
from app.services.run_executor import execute_run
from app.services.scoping import ScopedRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/automations", tags=["automations"])


class CreateAutomationBody(BaseModel):
    name: str
    plan_id: str
    description: str | None = None
    # NOTE: no user_id here — the owner is the AUTHENTICATED user, never a
    # client-supplied value (which would let a user create data under
    # someone else's id).


@router.get("", response_model=list[Automation])
async def list_automations(repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Every automation owned by the current user."""
    return await repo.list_automations()


@router.get("/{automation_id}", response_model=Automation)
async def get_automation(automation_id: str,
                         repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Fetch one automation; 404 if missing or owned by another user."""
    auto = await repo.get_automation(automation_id)
    if auto is None:
        raise HTTPException(404, f"automation {automation_id} not found")
    return auto


@router.post("", response_model=Automation)
async def create_automation(body: CreateAutomationBody,
                            user: User = Depends(get_current_user),
                            repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Wrap one of this user's plans in a named, runnable Automation."""
    plan = await repo.get_plan(body.plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {body.plan_id} not found")
    auto = Automation(
        id=f"auto_{uuid.uuid4().hex[:10]}",
        user_id=user.id,                 # owner = authenticated user
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
    user: User = Depends(get_current_user),
    repo: ScopedRepo = Depends(get_scoped_repo_dep),
):
    """Queue a run of this automation's plan and return the Run record.

    Returns as soon as the Run row exists (status PROVISIONING) so the caller
    gets a run id immediately; the sandbox spawn and execution proceed in the
    background. The plan is re-validated here because it is the last gate
    before it drives a real browser.
    """
    auto = await repo.get_automation(automation_id)
    if auto is None:
        raise HTTPException(404, f"automation {automation_id} not found")
    if auto.status != AutomationStatus.ACTIVE:
        raise HTTPException(409, f"automation {automation_id} is {auto.status.value}, not active")

    plan = await repo.get_plan(auto.plan_id)
    if plan is None:
        raise HTTPException(404, f"linked plan {auto.plan_id} missing")
    if plan.status != PlanStatus.APPROVED:
        raise HTTPException(
            409,
            f"plan {plan.id} is {plan.status.value}; approve it before running "
            f"(POST /plans/{plan.id}/approve)",
        )
    report = validate_plan(plan)
    if report.errors:
        raise HTTPException(422, {"message": "plan failed validation", "errors": report.errors})
    for warning in report.warnings:
        log.warning("plan %s: %s", plan.id, warning)

    run = Run(
        id=f"run_{uuid.uuid4().hex[:10]}",
        automation_id=auto.id,
        plan_version=plan.version,
        triggered_by=RunTrigger.MANUAL,
        triggered_by_user=user.id,
        status=RunStatus.PROVISIONING,
        # Freeze exactly what was approved. The run executes THIS, not a fresh
        # read of the plan row, so a plan edited while the run sits queued
        # cannot change what the sandbox does.
        plan_snapshot=plan.model_dump(mode="json"),
    )
    await repo.save_run(run)
    log.info("queued run %s for automation %s (plan %s v%d approved by %s)",
             run.id, auto.id, plan.id, plan.version, plan.approved_by)

    # The dashboard polls GET /runs/{id} for progress from here.
    background.add_task(execute_run, run.id)
    return run
