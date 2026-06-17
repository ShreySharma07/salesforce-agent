"""
Plan endpoints — now authenticated and per-user scoped.

Every route resolves the current user and operates through a ScopedRepo
bound to that user, so a user can only see/modify their own plans. A plan
that exists but belongs to someone else returns 404 (no cross-tenant leak).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.schemas.plan import Plan, PlanStatus
from app.api.deps import get_scoped_repo_dep
from app.services.scoping import ScopedRepo

router = APIRouter(prefix="/plans", tags=["plans"])


class UpsertPlanResponse(BaseModel):
    plan: Plan
    created: bool


@router.get("", response_model=list[Plan])
async def list_plans(repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    return await repo.list_plans()


@router.post("", response_model=UpsertPlanResponse)
async def upsert_plan(plan: Plan, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    existing = await repo.get_plan(plan.id)
    await repo.save_plan(plan)
    return UpsertPlanResponse(plan=plan, created=existing is None)


@router.get("/{plan_id}", response_model=Plan)
async def get_plan(plan_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    return plan


@router.post("/{plan_id}/approve", response_model=Plan)
async def approve_plan(plan_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
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
async def correct_plan(plan_id: str, body: CorrectionBody,
                       repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    plan.correction_history.append(body.feedback)
    plan.version += 1
    plan.status = PlanStatus.PENDING_APPROVAL
    await repo.save_plan(plan)
    return plan