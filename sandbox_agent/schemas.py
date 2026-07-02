"""
Plan/Step schemas mirrored from backend/app/schemas/plan.py.

The sandbox is a separate deployable, so we duplicate these types instead
of importing from the backend (which would couple the two builds).

Phase 2b adds ReAct trace models: LoopIteration captures one
Reason->Act->Observe cycle; StepResult.trace is the full trajectory.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    SEQUENCE = "sequence"


class Step(BaseModel):
    id: str
    kind: StepKind
    description: str
    details: dict[str, Any] = Field(default_factory=dict)
    # Plain-English end-state checked before acting (idempotency) and after (verify).
    # None for non-state-changing steps (navigate, wait, extract, control-flow).
    success_condition: str | None = None
    on_failure: str = "pause"


class Credential(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    version: int = 1
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
    model_config = ConfigDict(extra="ignore")

    memory_hints: dict[str, str] = Field(default_factory=dict)

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
    # Phase 2b: per-step ReAct budget. A UI step's loop runs at most this
    # many Reason->Act->Observe iterations before being marked failed.
    max_iterations_per_step: int = 70
    # Per-step wall-time ceiling in seconds.  Must be well below max_seconds so
    # the run budget is actually enforced; 300s (5 min) gives headroom for
    # multi-field modal steps (New Task, record edit) without busting the run.
    max_seconds_per_step: int = 400


# ---------------------------------------------------------------------------
# Phase 2b — ReAct trace
# ---------------------------------------------------------------------------

class LoopIteration(BaseModel):
    """One Reason->Act->Observe cycle inside a UI step's ReAct loop."""
    iteration: int
    thought: str = ""
    action: str = ""                       # the action kind chosen
    action_args: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""                  # compact text result of the action
    screenshot_ref: str | None = None      # filename of the screenshot, if saved
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class StepResult(BaseModel):
    step_id: str
    status: Literal["succeeded", "failed", "skipped", "paused"]
    detail: str = ""
    extracted: dict[str, Any] = Field(default_factory=dict)
    # Phase 2b: full ReAct trajectory for UI steps. Empty for non-UI steps
    # (mcp_call, navigate, wait, etc.) which don't run a loop.
    trace: list[LoopIteration] = Field(default_factory=list)
    # Why the step paused, if it did (e.g. "captcha", "human_input").
    pause_reason: str | None = None
    # True if this step failed because the LLM daily quota was exhausted.
    # The executor uses this to abort the whole run (no point continuing).
    quota_exhausted: bool = False


class RunResponse(BaseModel):
    """HTTP body returned by POST /run."""
    status: Literal["completed", "failed", "paused", "aborted"]
    step_results: list[StepResult] = Field(default_factory=list)
    final_url: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0