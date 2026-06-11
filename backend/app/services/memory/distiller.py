"""
The distiller: Run (+ its traces) -> Procedure.

This is the core of procedural memory. A raw run trace is noisy — it has
observe-only turns, failed attempts the agent recovered from, retries, and
give-ups. The distiller strips that down to the clean sequence of actions
that actually moved the task forward, plus the obstructions encountered.

It operates on the data you ALREADY store (Run.step_executions[].trace of
LoopIteration), so it can be built and tested against existing runs with no
LLM calls and no Salesforce.

What counts as a "productive" action:
  - It's not an observe-only / reason-only / unparseable turn.
  - It did not error.
  - It's not a terminal meta-action (done / give_up / captcha_detected) —
    those mark the end, they aren't steps to replay.
A `dismiss_obstruction` action is recorded as a known_obstruction rather than
a replay step, because next time the obstruction may or may not appear.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.schemas.memory import Obstruction, Procedure, ProcedureStep
from app.services.memory.signature import signature_for_plan


# Actions that are meta/terminal or non-replayable — excluded from the
# distilled step list.
_TERMINAL_ACTIONS = {"done", "give_up", "captcha_detected"}
_NONPRODUCTIVE_ACTIONS = {
    "(reason)", "(observe)", "(unparseable)", "wait", "",
}


def _iter_get(it: Any, key: str, default: Any = None) -> Any:
    """Read a field from a LoopIteration whether it's a pydantic model or a
    plain dict (traces may arrive either way depending on call site)."""
    if isinstance(it, dict):
        return it.get(key, default)
    return getattr(it, key, default)


def _is_success(step_status: str) -> bool:
    return step_status == "succeeded"


def distill_run_to_procedure(
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
    step_executions: list[Any],
    run_id: str,
    run_succeeded: bool,
) -> Procedure | None:
    """Distill a single run into a Procedure.

    Returns None if the run isn't a useful teaching example (it failed
    overall, or produced no productive actions). We only learn procedures
    from runs that genuinely accomplished the task.
    """
    if not run_succeeded:
        return None

    proc_steps: list[ProcedureStep] = []
    obstructions: list[Obstruction] = []

    for se in step_executions:
        se_status = _iter_get(se, "status", "")
        trace = _iter_get(se, "trace", []) or []
        # Only learn from steps that themselves succeeded.
        if not _is_success(se_status):
            continue

        for it in trace:
            action = (_iter_get(it, "action", "") or "").strip()
            err = _iter_get(it, "error", None)
            thought = _iter_get(it, "thought", "") or ""
            args = _iter_get(it, "action_args", {}) or {}

            if err:
                continue
            if action in _TERMINAL_ACTIONS:
                continue
            if action == "dismiss_obstruction":
                obstructions.append(Obstruction(
                    description=thought or "an obstruction blocked the page",
                    resolved_by=f"dismiss_obstruction {args}".strip(),
                ))
                continue
            if action in _NONPRODUCTIVE_ACTIONS:
                continue

            proc_steps.append(ProcedureStep(
                action=action,
                action_args=args,
                intent=_summarize_intent(thought),
            ))

    if not proc_steps:
        # Run "succeeded" but had no productive UI actions (e.g. a pure
        # mcp_call or navigate-only plan). Nothing to distill into a recipe.
        return None

    sig = signature_for_plan(goal, step_descriptions)
    now = datetime.utcnow()
    return Procedure(
        id=f"proc_{uuid.uuid4().hex[:10]}",
        user_id=user_id,
        task_signature=sig,
        goal=goal,
        steps=proc_steps,
        known_obstructions=_dedupe_obstructions(obstructions),
        times_succeeded=1,
        times_attempted=1,
        last_verified_at=now,
        source_run_ids=[run_id],
        created_at=now,
        updated_at=now,
    )


def merge_procedure(existing: Procedure, new: Procedure) -> Procedure:
    """Fold a freshly-distilled procedure into an existing one for the same
    signature. We keep the existing step recipe (stable), bump the success
    counters, union obstructions, and refresh verification time.

    Rationale: once we have a working recipe, we don't churn it on every
    success — we *reinforce* it (higher confidence) and only learn NEW
    obstructions. This keeps procedures stable and their success_rate
    meaningful.
    """
    existing.times_succeeded += new.times_succeeded
    existing.times_attempted += new.times_attempted
    existing.last_verified_at = new.last_verified_at or existing.last_verified_at
    existing.updated_at = datetime.utcnow()
    for rid in new.source_run_ids:
        if rid not in existing.source_run_ids:
            existing.source_run_ids.append(rid)
    # Union obstructions by description.
    seen = {o.description for o in existing.known_obstructions}
    for o in new.known_obstructions:
        if o.description not in seen:
            existing.known_obstructions.append(o)
            seen.add(o.description)
    return existing


def record_attempt_failure(existing: Procedure) -> Procedure:
    """When a procedure was retrieved and used but the run FAILED, bump only
    the attempt counter. This is how a rotted procedure (UI changed) loses
    confidence over time and stops being trusted — visibly, via success_rate.
    """
    existing.times_attempted += 1
    existing.updated_at = datetime.utcnow()
    return existing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarize_intent(thought: str, max_len: int = 120) -> str:
    t = " ".join((thought or "").split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "\u2026"


def _dedupe_obstructions(obs: list[Obstruction]) -> list[Obstruction]:
    seen: set[str] = set()
    out: list[Obstruction] = []
    for o in obs:
        if o.description not in seen:
            out.append(o)
            seen.add(o.description)
    return out