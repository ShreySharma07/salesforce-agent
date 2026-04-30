"""
Run schema. Tracks one execution of a Plan from start to finish.
Becomes the audit trail.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"      # spinning up sandbox
    RUNNING = "running"
    PAUSED_FOR_INPUT = "paused_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    BUDGET_EXCEEDED = "budget_exceeded"


class RunTrigger(str, Enum):
    MANUAL = "manual"             # user clicked Run
    SCHEDULE = "schedule"         # cron
    MCP = "mcp"                   # external agent invoked us
    API = "api"                   # direct REST call


class StepExecution(BaseModel):
    """One step's worth of execution data inside a Run."""
    step_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "running", "succeeded", "failed", "skipped", "paused"]
    attempts: int = 0
    error: str | None = None
    extracted_variables: dict[str, Any] = Field(default_factory=dict)


class HumanIntervention(BaseModel):
    """Recorded each time the run paused for human input."""
    at: datetime
    reason: str
    options: list[str] | None = None
    user_response: str | None = None
    responded_at: datetime | None = None


class CostBreakdown(BaseModel):
    """Token + dollar usage for one run."""
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
    live_view_url: str | None = Field(
        None,
        description="Signed WebSocket URL. Always generated; user can opt to open it.",
    )
    live_view_watched: bool = Field(
        default=False,
        description="True if any user actually opened the stream",
    )

    step_executions: list[StepExecution] = Field(default_factory=list)
    interventions: list[HumanIntervention] = Field(default_factory=list)

    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    summary: str | None = Field(None, description="Final outcome summary")
    error: str | None = None

    # For deep audit / replay
    recording_url: str | None = Field(None, description="S3 key of full session recording")
    audit_log_count: int = 0