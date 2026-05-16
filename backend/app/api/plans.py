"""
Plan endpoints. Plans come out of the video processor; the user approves
or corrects them; once approved, they can be saved as Automations.

  GET    /plans                  list plans
  GET    /plans/{plan_id}        get one
  POST   /plans/{plan_id}/approve  flip status to approved
  POST   /plans/{plan_id}/correct  user feedback -> regenerate (calls LLM)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.schemas.plan import Plan, PlanStatus
from app.services.run_repo import get_repository

router = APIRouter(prefix="/plans", tags=["plans"])


class UpsertPlanResponse(BaseModel):
    plan: Plan
    created: bool


@router.get("", response_model=list[Plan])
async def list_plans():
    return await get_repository().list_plans()


@router.post("", response_model=UpsertPlanResponse)
async def upsert_plan(plan: Plan):
    """Upload a plan. Creates if new, updates if existing."""
    repo = get_repository()
    existing = await repo.get_plan(plan.id)
    await repo.save_plan(plan)
    return UpsertPlanResponse(plan=plan, created=existing is None)


@router.get("/{plan_id}", response_model=Plan)
async def get_plan(plan_id: str):
    repo = get_repository()
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    return plan


@router.post("/{plan_id}/approve", response_model=Plan)
async def approve_plan(plan_id: str):
    repo = get_repository()
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    plan.status = PlanStatus.APPROVED
    plan.approved_at = datetime.utcnow()
    await repo.save_plan(plan)
    return plan


class CorrectionBody(BaseModel):
    feedback: str


@router.post("/{plan_id}/correct", response_model=Plan)
async def correct_plan(plan_id: str, body: CorrectionBody):
    """Apply user feedback. For now: appends to history + sets status.
    The actual LLM-driven regeneration happens via the plan_generator
    module — wire that in once you want full re-synthesis. Stub returns
    the plan with feedback recorded."""
    repo = get_repository()
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    plan.correction_history.append(body.feedback)
    plan.version += 1
    plan.status = PlanStatus.PENDING_APPROVAL
    await repo.save_plan(plan)
    return plan