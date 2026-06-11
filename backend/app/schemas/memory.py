"""
Procedural memory schemas.

A Procedure is a distilled, reusable "recipe" for a task the agent has
succeeded at before. It is NOT a raw trace — it is the cleaned, generalized
action sequence plus the gotchas learned along the way.

Design choices (see the memory build discussion):
  - Structured, not vector blobs: every field is inspectable and debuggable.
  - Keyed by a normalized task signature so retrieval is exact + fast, no
    embedding model required for the first version.
  - Carries a success record so confidence is visible and procedures that
    rot (UI changed) can be detected and down-weighted, not trusted blindly.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProcedureStep(BaseModel):
    """One distilled action in a procedure. Derived from the successful
    LoopIteration(s) of a past run — the noise (retries, failed attempts,
    observe-only turns) is stripped out, leaving the action that worked."""
    action: str                                   # click / fill / open_app / navigate / ...
    action_args: dict[str, Any] = Field(default_factory=dict)
    # Human-readable intent for this action, lifted from the thought that
    # produced it. Lets the agent (and a human) understand WHY, not just what.
    intent: str = ""


class Obstruction(BaseModel):
    """A gotcha seen on the way to success — e.g. a welcome modal that had
    to be dismissed. Primed into future runs so the agent expects it."""
    description: str
    resolved_by: str = ""                         # e.g. "dismiss_obstruction on the close button"


class Procedure(BaseModel):
    """A reusable recipe for a task, distilled from one or more successful
    runs of the same task signature."""
    id: str
    user_id: str
    # The normalized task signature this procedure answers (see
    # signature.py). Retrieval matches on this.
    task_signature: str
    # The original human-readable goal, kept for display/debugging.
    goal: str = ""

    steps: list[ProcedureStep] = Field(default_factory=list)
    known_obstructions: list[Obstruction] = Field(default_factory=list)

    # --- confidence / freshness ---
    times_succeeded: int = 0
    times_attempted: int = 0
    last_verified_at: datetime | None = None
    source_run_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def success_rate(self) -> float:
        if self.times_attempted == 0:
            return 0.0
        return self.times_succeeded / self.times_attempted

    @property
    def is_trusted(self) -> bool:
        """Whether the agent should lean on this procedure. Conservative:
        needs a real track record, not a single lucky run."""
        return self.times_succeeded >= 2 and self.success_rate >= 0.6


# ===========================================================================
# Episodic memory
# ===========================================================================
# Where procedural memory answers "how do I do this task" (the happy path,
# distilled and generalized), episodic memory answers "have I been in THIS
# situation before, and what happened?" — the unhappy path. It stores
# discrete, specific events: a step that failed, a captcha, an obstruction,
# and (most valuable) what a human did when the agent got stuck.
#
# These are NOT generalized into a recipe. They are concrete recollections,
# retrieved by situation and surfaced as cautionary hints. The value shows on
# edge cases, so episodic complements — never replaces — procedural memory.


class EpisodeKind(str, Enum):
    """What kind of notable event this episode records."""
    STEP_FAILURE = "step_failure"        # a step failed (with its error)
    GAVE_UP = "gave_up"                  # the agent emitted give_up
    CAPTCHA = "captcha"                  # a bot-check blocked progress
    OBSTRUCTION = "obstruction"          # a modal/popup had to be cleared
    HUMAN_FIX = "human_fix"              # a human intervened and resolved it
    SUCCESS_NOTE = "success_note"        # a noteworthy thing that worked


class Episode(BaseModel):
    """One specific remembered event from a past run.

    Retrieval is by `situation_key` — a compact descriptor of WHERE/WHAT the
    event was (e.g. the step intent + page context), so a future run facing a
    similar situation can recall "this happened here before".
    """
    id: str
    user_id: str
    kind: EpisodeKind

    # Retrieval key: a normalized descriptor of the situation this episode
    # occurred in (built from the step intent / goal — see episodic.py).
    situation_key: str

    # Human-readable specifics — what actually happened, and if applicable,
    # how it was resolved. This is what gets surfaced to the agent.
    what_happened: str = ""
    resolution: str = ""                 # how it was fixed, if it was

    # Provenance + recency.
    run_id: str = ""
    step_id: str | None = None
    goal: str = ""
    occurred_at: datetime = Field(default_factory=datetime.utcnow)

    # How many times this same situation+kind has recurred. A repeatedly-seen
    # failure is a stronger signal than a one-off.
    seen_count: int = 1

    @property
    def is_actionable(self) -> bool:
        """An episode is worth surfacing as a hint if it carries a resolution
        (we know what to do) or it's a recurring problem worth warning about."""
        return bool(self.resolution) or self.seen_count >= 2


# ===========================================================================
# Reflection
# ===========================================================================
# Reflection is the layer that makes the other two BETTER. After a run it
# compares intent vs. outcome and emits structured Lessons — each one routed
# to an action: reinforce a procedure, down-weight a rotted one, record a new
# obstruction, flag an inefficiency, or capture a new failure pattern.
#
# The hard rule that keeps reflection from being theatre: a Lesson is only
# produced when there is a CONCRETE, ACTIONABLE finding. "I should be more
# careful" is not a lesson. "This run took 11 iterations on a step the known
# procedure does in 3 — the procedure may be stale here" is.


class LessonKind(str, Enum):
    REINFORCED_PROCEDURE = "reinforced_procedure"    # known recipe worked again
    PROCEDURE_MAY_BE_STALE = "procedure_may_be_stale" # known recipe led to failure/inefficiency
    NEW_OBSTRUCTION = "new_obstruction"              # hit an obstruction not in memory
    REPEATED_FAILURE = "repeated_failure"            # failed the same way as before
    INEFFICIENCY = "inefficiency"                    # succeeded but slower than known-good
    NEW_CAPABILITY = "new_capability"                # succeeded at a task with no prior memory


class Lesson(BaseModel):
    """A concrete, actionable finding from reflecting on a run.

    `routed_to` names what the system did with it (which store/op), so the
    effect of reflection is auditable — you can see that a lesson actually
    changed memory, not just got written down.
    """
    id: str
    user_id: str
    kind: LessonKind
    task_signature: str = ""
    summary: str                                     # the concrete finding
    run_id: str = ""
    goal: str = ""
    routed_to: str = ""                              # e.g. "procedure.reinforced", "episode.added"
    created_at: datetime = Field(default_factory=datetime.utcnow)