"""
Plan endpoints — authenticated and per-user scoped.

Every route resolves the current user and operates through a ScopedRepo
bound to that user, so a user can only see/modify their own plans. A plan
that exists but belongs to someone else returns 404 (no cross-tenant leak).

  GET  /plans                    list this user's plans
  POST /plans                    create or overwrite a plan by id
  GET  /plans/{id}               fetch one
  POST /plans/{id}/approve       mark APPROVED (validated first)
  POST /plans/{id}/correct       regenerate from plain-language feedback
  GET  /plans/{id}/capabilities  what the plan needs vs what the connected org,
                                 user and agent have (show before approving)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_scoped_repo_dep
from app.config import get_settings
from app.db.base import get_session
from app.schemas.capability import CapabilityReport
from app.core.guardrails import PlanValidationError, validate_plan
from app.schemas.plan import Plan, PlanStatus
from app.services.scoping import ScopedRepo

router = APIRouter(prefix="/plans", tags=["plans"])
log = logging.getLogger(__name__)


class UpsertPlanResponse(BaseModel):
    plan: Plan
    created: bool
    warnings: list[str] = []


class CorrectionBody(BaseModel):
    feedback: str


@router.get("", response_model=list[Plan])
async def list_plans(repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """All plans owned by the current user, newest first."""
    return await repo.list_plans()


@router.post("", response_model=UpsertPlanResponse)
async def upsert_plan(plan: Plan, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Create or overwrite a plan by id (scripts/run_plan_e2e pushes JSON plans here).

    The plan is validated on the way in: structural errors (dangling step
    references, missing required details) are rejected with a 422, while
    softer issues come back as `warnings` so the user can still iterate.
    """
    report = validate_plan(plan)
    if report.errors:
        raise HTTPException(422, {"message": "plan failed validation", "errors": report.errors})
    await repo.save_plan(plan)
    existing = await repo.get_plan(plan.id)
    return UpsertPlanResponse(
        plan=plan, created=existing is None, warnings=report.warnings,
    )


@router.get("/{plan_id}", response_model=Plan)
async def get_plan(plan_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Fetch one plan; 404 if missing or owned by another user."""
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    return plan


@router.get("/{plan_id}/capabilities", response_model=CapabilityReport)
async def plan_capabilities(plan_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep),
                            session: AsyncSession = Depends(get_session)):
    """Diff the plan's needs against the connected org, the user's permissions
    and the agent's tools: "for this org I have these, I lack those".

    Read-only: describe calls with the user's own token, nothing is changed.
    Org checks come back `unknown` (not an error) when the app isn't connected.
    """
    from app.services.capabilities import check_plan_capabilities

    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    return await check_plan_capabilities(plan, session=session, user_id=repo.user_id)


@router.post("/{plan_id}/approve", response_model=Plan)
async def approve_plan(plan_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Mark a plan APPROVED so it can be wrapped in an Automation and run.

    Approval is the last gate before a plan drives a real browser, so the
    guardrails run here too — an invalid plan can never reach a sandbox.
    """
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")
    report = validate_plan(plan)
    if report.errors:
        raise HTTPException(422, {"message": "plan failed validation", "errors": report.errors})
    plan.status = PlanStatus.APPROVED
    plan.approved_at = datetime.utcnow()
    await repo.save_plan(plan)
    return plan


@router.post("/{plan_id}/correct", response_model=Plan)
async def correct_plan(plan_id: str, body: CorrectionBody,
                       repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Apply the user's stated intent by REGENERATING the plan with the LLM.

    The user never hand-edits steps — they describe what they want and the
    agent produces a revised plan. The previous plan stays the anchor (it is
    the record of what was demonstrated); the feedback reshapes it. Each
    correction bumps the version and returns to PENDING_APPROVAL.
    """
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {plan_id} not found")

    # Original captions anchor the revision to the recording when available;
    # without them the previous plan's steps are the record of what was done.
    captions = _load_captions_for_plan(plan)

    try:
        from app.agent.plan_generator import regenerate_plan_from_intent
        revised = regenerate_plan_from_intent(
            previous_plan=plan, user_feedback=body.feedback, captions=captions,
        )
    except Exception as e:
        log.exception("plan correction failed for %s", plan_id)
        raise HTTPException(
            500, f"could not regenerate plan from feedback: {type(e).__name__}: {e}"
        )

    await repo.save_plan(revised)
    return revised


def _load_captions_for_plan(plan: Plan):
    """Return the FrameCaptions for this plan's source recording, or None.

    The video pipeline writes them to `captions/<video_id>.json` under the
    storage root (see app/api/videos.py); a plan generated before that
    existed simply has no captions and regenerates from its own steps.
    """
    if not plan.source_video_id:
        return None
    try:
        from app.agent.keyframe_captioner import FrameCaption
        root = Path(get_settings().local_storage_root)
        candidate = root / "captions" / f"{plan.source_video_id}.json"
        if not candidate.exists():
            return None
        data = json.loads(candidate.read_text())
        return [
            FrameCaption(
                keyframe_index=c.get("keyframe_index", i),
                timestamp_seconds=c.get("timestamp_seconds", 0.0),
                description=c.get("description", ""),
                narration=c.get("narration", ""),
            )
            for i, c in enumerate(data)
        ]
    except Exception:
        log.warning("could not load captions for plan %s", plan.id, exc_info=True)
        return None
