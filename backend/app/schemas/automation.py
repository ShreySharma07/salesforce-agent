"""Automation schema. A saved/named/scheduled Plan."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AutomationStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Schedule(BaseModel):
    cron: str
    timezone: str = "UTC"
    next_run_at: datetime | None = None


class LiveViewSettings(BaseModel):
    default: bool = False
    allow_join_in_progress: bool = True


class Automation(BaseModel):
    id: str
    user_id: str
    organization_id: str | None = None
    name: str
    description: str | None = None
    plan_id: str
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