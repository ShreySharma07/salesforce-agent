"""
Procedure store + retrieval.

The store persists Procedures and answers the agent's two questions:
  1. (at task start)  "Do I know how to do this task already?"  -> retrieve()
  2. (at task end)    "Here's what I just learned."             -> learn_from_run()

This module ships an in-memory implementation so the whole memory system can
be built and unit-tested now, with no DB and no LLM. A SQL-backed
implementation will mirror this interface later (same pattern as run_repo's
SqlRepo), so the agent code that consumes memory never changes.

It also provides `build_priming_text`: how a retrieved procedure is turned
into a compact hint injected into the agent's prompt. This is the payoff —
the agent is reminded "last time, this worked" instead of rediscovering the
task from scratch.
"""
from __future__ import annotations

from typing import Protocol

from app.schemas.memory import Procedure
from app.services.memory.distiller import (
    distill_run_to_procedure,
    merge_procedure,
    record_attempt_failure,
)
from app.services.memory.signature import signature_for_plan


class ProcedureStore(Protocol):
    async def get_by_signature(self, user_id: str, signature: str) -> Procedure | None: ...
    async def save(self, proc: Procedure) -> None: ...
    async def list_for_user(self, user_id: str) -> list[Procedure]: ...


class InMemoryProcedureStore:
    """Dict-backed store. Real enough to test all the logic; swapped for a
    SQL store in production without touching callers."""

    def __init__(self) -> None:
        # keyed by (user_id, signature)
        self._by_sig: dict[tuple[str, str], Procedure] = {}

    async def get_by_signature(self, user_id: str, signature: str) -> Procedure | None:
        return self._by_sig.get((user_id, signature))

    async def save(self, proc: Procedure) -> None:
        self._by_sig[(proc.user_id, proc.task_signature)] = proc

    async def list_for_user(self, user_id: str) -> list[Procedure]:
        return [p for (u, _), p in self._by_sig.items() if u == user_id]


# ---------------------------------------------------------------------------
# The two operations the agent pipeline calls
# ---------------------------------------------------------------------------

async def retrieve(
    store: ProcedureStore,
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
) -> Procedure | None:
    """Task-start lookup: is there a known procedure for this task?

    Returns the procedure if found (caller decides whether to trust it via
    .is_trusted). Returns None if we've never done this task.
    """
    sig = signature_for_plan(goal, step_descriptions)
    if not sig:
        return None
    return await store.get_by_signature(user_id, sig)


async def learn_from_run(
    store: ProcedureStore,
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
    step_executions: list,
    run_id: str,
    run_succeeded: bool,
) -> Procedure | None:
    """Task-end write: distill this run into (or fold into) a procedure.

    On a SUCCESSFUL run: distill, then either store fresh or reinforce the
    existing procedure for this signature.
    On a FAILED run where a procedure existed: record the failed attempt so
    a rotted procedure loses confidence.

    Returns the stored/updated procedure, or None if nothing was learned.
    """
    sig = signature_for_plan(goal, step_descriptions)
    existing = await store.get_by_signature(user_id, sig) if sig else None

    if not run_succeeded:
        # A failed run only matters for memory if we had a procedure that
        # presumably guided it — down-weight it.
        if existing is not None:
            record_attempt_failure(existing)
            await store.save(existing)
            return existing
        return None

    distilled = distill_run_to_procedure(
        user_id=user_id,
        goal=goal,
        step_descriptions=step_descriptions,
        step_executions=step_executions,
        run_id=run_id,
        run_succeeded=run_succeeded,
    )
    if distilled is None:
        return None

    if existing is None:
        await store.save(distilled)
        return distilled

    merged = merge_procedure(existing, distilled)
    await store.save(merged)
    return merged


# ---------------------------------------------------------------------------
# Priming — how a retrieved procedure becomes a prompt hint
# ---------------------------------------------------------------------------

def build_priming_text(proc: Procedure) -> str:
    """Render a procedure as a compact hint for the agent's prompt.

    Only call this for a procedure the caller has decided to trust. The text
    is intentionally framed as guidance, not gospel — the agent must still
    observe the live page and adapt, because the UI can change.
    """
    lines: list[str] = []
    lines.append(
        f"MEMORY \u2014 you have done a similar task before "
        f"(succeeded {proc.times_succeeded}/{proc.times_attempted} times). "
        f"Here is the path that worked. Treat it as a strong hint, but VERIFY "
        f"against the live page and adapt if the UI differs:"
    )
    for i, s in enumerate(proc.steps, 1):
        # The semantic target descriptor (from grounding) is UI structure,
        # not user data — rendering it is the point of stable identity:
        # "click button 'New' in header" is replayable; "(ref)" is not.
        target = s.action_args.get("target", "") if s.action_args else ""
        arg_str = f" {target}" if target else ""
        if not target and s.action_args:
            # No descriptor (pre-grounding trace) — fall back to key names,
            # still never leaking values.
            keys = ", ".join(k for k in sorted(s.action_args.keys())
                             if k not in ("text", "value"))
            arg_str = f" ({keys})" if keys else ""
        intent = f" \u2014 {s.intent}" if s.intent else ""
        lines.append(f"  {i}. {s.action}{arg_str}{intent}")
    if proc.known_obstructions:
        lines.append("Watch out for:")
        for o in proc.known_obstructions:
            lines.append(f"  - {o.description}")
    return "\n".join(lines)