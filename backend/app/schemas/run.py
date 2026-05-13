"""Run schema. One execution of a Plan."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    PAUSED_FOR_INPUT = "paused_for_input"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"
    CANCELED = "canceled"
    BUDGET_EXCEEDED = "budget_exceeded"


class RunTrigger(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    MCP = "mcp"
    API = "api"


class StepExecution(BaseModel):
    step_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "running", "succeeded", "failed", "skipped", "paused"]
    attempts: int = 0
    error: str | None = None
    extracted_variables: dict[str, Any] = Field(default_factory=dict)


class HumanIntervention(BaseModel):
    at: datetime
    reason: str
    options: list[str] | None = None
    user_response: str | None = None
    responded_at: datetime | None = None


class CostBreakdown(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    sandbox_seconds: float = 0.0


class Run(BaseModel):
    id: str
    automation_id: str
    plan_version: int
    status: RunStatus = RunStatus.QUEUED
    triggered_by: RunTrigger
    triggered_by_user: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    sandbox_id: str | None = None
    live_view_url: str | None = None
    live_view_watched: bool = False
    step_executions: list[StepExecution] = Field(default_factory=list)
    interventions: list[HumanIntervention] = Field(default_factory=list)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    summary: str | None = None
    error: str | None = None
    recording_url: str | None = None
    audit_log_count: int = 0