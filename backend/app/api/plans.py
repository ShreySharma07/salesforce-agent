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


import logging
log = logging.getLogger(__name__)
 
 
class CorrectionBody(BaseModel):
    feedback: str
 
 
@router.post("/{plan_id}/correct", response_model=Plan)
async def correct_plan(plan_id: str, body: CorrectionBody,
                       repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Apply the user's stated intent: regenerate the plan via the LLM so the
    user never hand-edits steps — they describe what they want and the agent
    produces a revised plan. Loops until the user approves.
 
    The previous plan stays the anchor (faithful to the demonstrated actions);
    the feedback reshapes it (e.g. 'do this for all NEW cases, not 00001378').
    """
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
 
    # Try to load the original captions for maximum fidelity; fall back to
    # caption-less regeneration if they aren't persisted.
    captions = await _load_captions_for_plan(plan)
 
    try:
        from app.agent.plan_generator import regenerate_plan_from_intent
        revised = regenerate_plan_from_intent(
            previous_plan=plan,
            user_feedback=body.feedback,
            captions=captions,
        )
    except Exception as e:
        log.exception("plan correction failed for %s", plan_id)
        raise HTTPException(
            500, f"could not regenerate plan from feedback: {type(e).__name__}: {e}"
        )
 
    # Persist the new version under the same plan id (scoped to this user).
    await repo.save_plan(revised)
    return revised
 
 
async def _load_captions_for_plan(plan: Plan):
    """Return the FrameCaptions for the plan's source recording if they were
    persisted, else None (correction then runs caption-less). Adjust the body
    to match wherever your pipeline stores captions.
 
    Common storage spots to wire up here:
      - a JSON file under local_storage keyed by source_video_id
      - a captions table / column
    Returns None safely if nothing is found, so correction still works.
    """
    if not plan.source_video_id:
        return None
    try:
        import json, os
        from app.config import get_settings
        from app.agent.keyframe_captioner import FrameCaption
        root = get_settings().local_storage_root
        # Adjust this path to wherever captions are written by your pipeline:
        candidate = os.path.join(root, "captions", f"{plan.source_video_id}.json")
        if os.path.exists(candidate):
            data = json.loads(open(candidate).read())
            return [
                FrameCaption(
                    timestamp_seconds=c.get("timestamp_seconds", 0.0),
                    description=c.get("description", ""),
                )
                for c in data
            ]
    except Exception:
        pass
    return None
 