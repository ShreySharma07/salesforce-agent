"""
Plan schema. The CONTRACT between video processor, plan generator,
guardrails, executor, dashboard, and (eventually) the MCP server.

A Plan is a structured representation of a recorded task. Once approved,
it becomes a reusable Automation.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Step types — each step is one atomic intent. The executor maps these to
# concrete actions (UI clicks, MCP tool calls, etc.) at runtime.
# ---------------------------------------------------------------------------

class StepKind(str, Enum):
    UI_ACTION = "ui_action"          # generic UI interaction (click, type, etc.)
    MCP_CALL = "mcp_call"            # invoke a tool on a connected MCP server
    NAVIGATE = "navigate"            # go to a URL (fast path for any web task)
    WAIT = "wait"                    # explicit wait with reason
    EXTRACT = "extract"              # read information from page into a variable
    DECISION = "decision"            # if/else branching based on extracted state
    LOOP = "loop"                    # repeat a sub-plan over a collection
    HUMAN_INPUT = "human_input"      # explicit pause for human input
    NOTIFY = "notify"                # send a message (Slack, email, etc.)
    SEQUENCE = "sequence"            # deterministic ordered sub-actions (no LLM)


class UIActionDetails(BaseModel):
    """High-level intent. The executor decides whether to use computer use
    coords, browser-aware DOM ref, or another technique at runtime."""
    intent: str = Field(..., description="Human-readable intent, e.g. 'Click the Login button'")
    target_description: str | None = Field(
        None,
        description="What to act on, in natural language: 'the username field', 'the submit button'",
    )
    value: str | None = Field(None, description="Text to type, value to select, etc.")


class MCPCallDetails(BaseModel):
    server: str = Field(..., description="MCP server name, e.g. 'salesforce', 'gmail'")
    tool: str = Field(..., description="Tool name on that server, e.g. 'create_lead'")
    arguments: dict[str, Any] = Field(default_factory=dict)


class NavigateDetails(BaseModel):
    url: str
    expected_title_contains: str | None = None


class WaitDetails(BaseModel):
    seconds: float
    reason: str


class ExtractDetails(BaseModel):
    """Read information from the current page into a variable that
    later steps can reference via ${variable_name}."""
    variable_name: str
    description: str = Field(..., description="What to extract, e.g. 'the order ID from the confirmation banner'")


class DecisionDetails(BaseModel):
    condition: str = Field(..., description="Plain English condition to evaluate")
    if_true: list[str] = Field(default_factory=list, description="Step IDs to execute if true")
    if_false: list[str] = Field(default_factory=list, description="Step IDs to execute if false")


class LoopDetails(BaseModel):
    over: str = Field(..., description="Variable to iterate over, e.g. '${unread_emails}'")
    item_variable: str = Field(..., description="Loop variable name, e.g. 'email'")
    body: list[str] = Field(default_factory=list, description="Step IDs to execute per item")


class HumanInputDetails(BaseModel):
    prompt: str = Field(..., description="Question to ask the user")
    options: list[str] | None = Field(None, description="Multiple choice options, if any")


class NotifyDetails(BaseModel):
    channel: Literal["dashboard", "email", "slack"] = "dashboard"
    message: str


# Discriminated union — Pydantic will pick the right shape based on `kind`
StepDetails = (
    UIActionDetails
    | MCPCallDetails
    | NavigateDetails
    | WaitDetails
    | ExtractDetails
    | DecisionDetails
    | LoopDetails
    | HumanInputDetails
    | NotifyDetails
)


class Step(BaseModel):
    """One step in a plan. Composable, addressable by id."""
    id: str = Field(..., description="Stable ID, e.g. 'step_001'")
    kind: StepKind
    description: str = Field(..., description="One-line human-readable description")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific details — see *Details classes above",
    )
    # Plain-English description of the state this step must produce.
    # The executor checks this BEFORE acting (skip if already true) and
    # AFTER acting (verify before emitting done).  Omit for non-state-
    # changing steps (navigate, wait, extract, control-flow).
    success_condition: str | None = Field(
        None,
        description=(
            "Target end-state for this step, e.g. "
            "'Status field shows Escalated in read-only view'. "
            "Checked before acting (idempotency) and after (verification)."
        ),
    )
    on_failure: Literal["pause", "skip", "abort", "retry"] = "pause"


# ---------------------------------------------------------------------------
# Plan & related
# ---------------------------------------------------------------------------

class Credential(BaseModel):
    """A credential the plan needs at execution time. The orchestrator
    fetches the actual token from Vault and injects scoped values into
    the sandbox at runtime."""
    name: str = Field(..., description="e.g. 'salesforce', 'gmail'")
    scopes: list[str] = Field(default_factory=list)


class DecisionRule(BaseModel):
    """Plain-English business rule the plan generator inferred from the
    recording. Surfaced to the user during plan approval so they can
    correct misunderstandings before execution."""
    rule: str = Field(..., description="e.g. 'Only process emails from the @company.com domain'")
    inferred_from: str | None = Field(None, description="Why we think this rule applies")


class PlanStatus(str, Enum):
    DRAFT = "draft"               # generator is still working
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"     # user gave correction, this version replaced


class Plan(BaseModel):
    """The structured plan produced from a recording. Shown to user for
    approval. Once approved, executed many times."""
    id: str = Field(..., description="Plan ID, stable across versions of an automation")
    automation_id: str | None = Field(None, description="Set once approved + saved")
    version: int = Field(default=1, description="Bumped each time the user gives a correction")
    status: PlanStatus = PlanStatus.DRAFT

    goal: str = Field(..., description="One-sentence statement of what this plan does")
    summary: str | None = Field(None, description="Multi-paragraph summary, optional")
    steps: list[Step] = Field(default_factory=list)
    decision_rules: list[DecisionRule] = Field(default_factory=list)
    required_credentials: list[Credential] = Field(default_factory=list)

    # Provenance
    source_video_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str | None = None

    # Approval metadata
    approved_at: datetime | None = None
    approved_by: str | None = None
    correction_history: list[str] = Field(
        default_factory=list,
        description="User's free-text corrections that led to this version",
    )


class PlanCorrectionRequest(BaseModel):
    """User feedback that triggers a new plan version."""
    plan_id: str
    feedback: str = Field(..., description="Free text from the user")