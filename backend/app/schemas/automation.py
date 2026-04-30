"""
Automation schema. A saved, named Plan that the user can run on demand
or schedule. This is what shows up in the dashboard's automation list.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AutomationStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Schedule(BaseModel):
    """Cron-style schedule. Optional; many automations are manual-only."""
    cron: str = Field(..., description="e.g. '0 8 * * 1-5' for weekdays at 8am")
    timezone: str = Field(default="UTC")
    next_run_at: datetime | None = None


class LiveViewSettings(BaseModel):
    """Per-automation default for whether to surface the live view."""
    default: bool = Field(
        default=False,
        description=(
            "If False (recommended), runs go to background and notify when done. "
            "If True, dashboard auto-opens noVNC stream when user triggers a run."
        ),
    )
    allow_join_in_progress: bool = Field(
        default=True,
        description="If True, user can click 'watch now' mid-run.",
    )


class Automation(BaseModel):
    id: str
    user_id: str
    organization_id: str | None = None
    name: str = Field(..., description="User-given name, e.g. 'Morning Lead Triage'")
    description: str | None = None

    plan_id: str = Field(..., description="The currently-approved plan")
    plan_version: int = 1

    status: AutomationStatus = AutomationStatus.ACTIVE
    schedule: Schedule | None = None
    live_view: LiveViewSettings = Field(default_factory=LiveViewSettings)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_run_at: datetime | None = None
    total_runs: int = 0
    successful_runs: int = 0

    tags: list[str] = Field(default_factory=list)