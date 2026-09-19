"""
Run endpoints. The dashboard polls these for status updates.

  GET    /runs                       list the current user's runs (most recent first)
  GET    /runs/{run_id}              get one of the current user's runs
  POST   /runs/{run_id}/cancel       tear down the sandbox and mark the run CANCELED
  POST   /runs/{run_id}/resume       answer a paused run; re-runs from the paused step

Every route is authenticated and scoped: a run that exists but belongs to
another user returns 404, never its payload (payloads carry extracted
variables and full reasoning traces).
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user, get_scoped_repo_dep
from app.schemas.auth import User
from app.schemas.run import Run
from app.services.scoping import ScopedRepo

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[Run])
async def list_runs(automation_id: str | None = None,
                    repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """List the current user's runs, newest first, optionally per automation."""
    return await repo.list_runs(automation_id=automation_id)


@router.get("/{run_id}", response_model=Run)
async def get_run(run_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Fetch one run (full payload incl. reasoning traces); 404 unless owned by the caller."""
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run


@router.post("/{run_id}/cancel", response_model=Run)
async def cancel_run(run_id: str, repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Cancel an in-flight run: tear its sandbox down and mark it CANCELED."""
    from app.services.run_control import cancel_run as _cancel

    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    updated = await _cancel(run, user_id=repo.user_id)
    if updated is None:
        raise HTTPException(409, f"run {run_id} is not in a cancelable state ({run.status.value})")
    return updated


class ResumeBody(BaseModel):
    response: str = ""


@router.post("/{run_id}/resume", response_model=Run)
async def resume_run(run_id: str, body: ResumeBody, background: BackgroundTasks,
                     user: User = Depends(get_current_user),
                     repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """Answer a run paused for human input.

    Records the human's response as a HumanIntervention on the paused run,
    then starts a NEW run of the same automation that begins at the paused
    step with the paused run's variables (plus ${human_response}) carried
    over. Steps carry success_conditions, so re-running earlier steps is
    unnecessary — the new run skips straight to where the old one stopped.
    """
    from app.services.run_control import resume_run as _resume

    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    new_run = await _resume(run, response=body.response, user_id=user.id, background=background)
    if new_run is None:
        raise HTTPException(409, f"run {run_id} is not paused ({run.status.value})")
    return new_run
