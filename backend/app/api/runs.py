"""
Run endpoints. The dashboard polls these for status updates.

  GET    /runs                       list all runs (most recent first)
  GET    /runs/{run_id}              get one
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.run import Run
from app.services.run_repo import get_repository

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[Run])
async def list_runs(automation_id: str | None = None):
    return await get_repository().list_runs(automation_id=automation_id)


@router.get("/{run_id}", response_model=Run)
async def get_run(run_id: str):
    run = await get_repository().get_run(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run