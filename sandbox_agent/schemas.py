"""
Plan/Step schemas mirrored from backend/app/schemas/plan.py.

The sandbox is a separate deployable, so we duplicate these types instead
of importing from the backend (which would couple the two builds).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class StepKind(str, Enum):
    UI_ACTION = "ui_action"
    MCP_CALL = "mcp_call"
    NAVIGATE = "navigate"
    WAIT = "wait"
    EXTRACT = "extract"
    DECISION = "decision"
    LOOP = "loop"
    HUMAN_INPUT = "human_input"
    NOTIFY = "notify"


class Step(BaseModel):
    id: str
    kind: StepKind
    description: str
    details: dict[str, Any] = Field(default_factory=dict)
    on_failure: str = "pause"


class Credential(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    id: str
    goal: str
    summary: str | None = None
    steps: list[Step] = Field(default_factory=list)
    required_credentials: list[Credential] = Field(default_factory=list)
    source_video_id: str | None = None


class ExecutionMode(str, Enum):
    """How the executor approaches actions inside the sandbox."""
    BROWSER = "browser"      # Playwright + DOM grounding
    COMPUTER = "computer"    # whole desktop via xdotool + screenshots


class RunRequest(BaseModel):
    """HTTP body for POST /run."""
    plan: Plan
    initial_url: str | None = Field(
        None,
        description="Starting URL for browser. If unset, browser starts blank.",
    )
    credentials: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="Map of credential name to {key:value} pairs.",
    )
    max_steps: int = 50
    max_seconds: int = 600


class StepResult(BaseModel):
    step_id: str
    status: Literal["succeeded", "failed", "skipped", "paused"]
    detail: str = ""


class RunResponse(BaseModel):
    """HTTP body returned by POST /run."""
    status: Literal["completed", "failed", "paused", "aborted"]
    step_results: list[StepResult] = Field(default_factory=list)
    final_url: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0