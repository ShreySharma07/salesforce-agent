"""
Reflection: the layer that makes procedural + episodic memory better.

After a run finishes, `reflect_on_run` does three things:
  1. Compares INTENT vs OUTCOME and emits structured Lessons (only when there
     is a concrete, actionable finding — never vacuous "be more careful").
  2. Routes each lesson to a real effect: reinforce/down-weight a procedure,
     add an episode, etc. The lesson records what it did (routed_to), so the
     impact of reflection is auditable.
  3. Returns the lessons + a short human-readable reflection summary.

This is the orchestrator that ties the stack together — it calls the
procedural store (learn_from_run) and the episodic store (remember_run), then
layers the comparative analysis on top.

The analysis is DETERMINISTIC (no LLM) so it is testable now and cheap in
production. An optional LLM enrichment pass could later add free-text insight,
but the actionable routing must stay rule-based so it can't hallucinate a
change to memory.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.schemas.memory import Lesson, LessonKind, Procedure
from app.services.memory.signature import signature_for_plan
from app.services.memory.store import (
    ProcedureStore,
    retrieve as retrieve_procedure,
    learn_from_run,
)
from app.services.memory.episodic import (
    EpisodeStore,
    remember_run,
    capture_episodes,
)


# How much slower than the known-good iteration count counts as "inefficient".
_INEFFICIENCY_RATIO = 1.5


def _se_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _count_iterations(step_executions: list[Any]) -> int:
    total = 0
    for se in step_executions:
        total += len(_se_get(se, "trace", []) or [])
    return total


def _procedure_expected_iterations(proc: Procedure | None) -> int | None:
    """The iteration count the known procedure implies (its step count is a
    reasonable proxy for 'how many actions this should take')."""
    if proc is None or not proc.steps:
        return None
    return len(proc.steps)


async def reflect_on_run(
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
    step_executions: list[Any],
    interventions: list[Any],
    run_id: str,
    run_succeeded: bool,
    procedure_store: ProcedureStore,
    episode_store: EpisodeStore,
) -> dict[str, Any]:
    """Reflect on a finished run. Returns:
        {
          "lessons": list[Lesson],
          "summary": str,
          "procedure": Procedure | None,   # the proc after learning
          "episodes_added": int,
        }

    Side effects: updates the procedural + episodic stores.
    """
    lessons: list[Lesson] = []
    sig = signature_for_plan(goal, step_descriptions)

    # What did memory know BEFORE this run? (decides reinforce vs. new)
    prior_proc = await retrieve_procedure(
        procedure_store, user_id=user_id, goal=goal,
        step_descriptions=step_descriptions,
    )
    had_prior = prior_proc is not None
    prior_expected = _procedure_expected_iterations(prior_proc)

    # --- 1. Run the procedural + episodic learning (the other two layers) ---
    procedure = await learn_from_run(
        procedure_store, user_id=user_id, goal=goal,
        step_descriptions=step_descriptions, step_executions=step_executions,
        run_id=run_id, run_succeeded=run_succeeded,
    )
    episodes = await remember_run(
        episode_store, user_id=user_id, goal=goal,
        step_descriptions=step_descriptions, step_executions=step_executions,
        interventions=interventions, run_id=run_id,
    )

    # --- 2. Comparative analysis -> lessons ---

    def _add(kind: LessonKind, summary: str, routed_to: str) -> None:
        lessons.append(Lesson(
            id=f"lesson_{uuid.uuid4().hex[:10]}",
            user_id=user_id, kind=kind, task_signature=sig,
            summary=summary, run_id=run_id, goal=goal, routed_to=routed_to,
        ))

    if run_succeeded:
        if had_prior and procedure is not None:
            # Known recipe worked again -> reinforced (learn_from_run already
            # bumped its counters).
            _add(LessonKind.REINFORCED_PROCEDURE,
                 f"Known procedure for '{sig}' succeeded again "
                 f"(now {procedure.times_succeeded}/{procedure.times_attempted}).",
                 "procedure.reinforced")
            # Inefficiency check: did it take materially more iterations than
            # the known recipe implies?
            actual = _count_iterations(step_executions)
            if prior_expected and actual > prior_expected * _INEFFICIENCY_RATIO:
                _add(LessonKind.INEFFICIENCY,
                     f"Succeeded but took {actual} iterations vs ~{prior_expected} "
                     f"the known procedure implies — the recipe may be drifting or "
                     f"the page changed.",
                     "flagged")
        else:
            # Only a genuine new capability if a procedure was actually
            # distilled (the run had productive actions). A success with
            # nothing to distill (e.g. a wait-only step) teaches nothing.
            if procedure is not None:
                _add(LessonKind.NEW_CAPABILITY,
                     f"Learned a new procedure for '{sig}' from a first success.",
                     "procedure.created")
    else:
        # Failure analysis.
        if had_prior:
            # A known procedure was in play and the run still failed -> it may
            # be stale. learn_from_run already down-weighted it.
            _add(LessonKind.PROCEDURE_MAY_BE_STALE,
                 f"Known procedure for '{sig}' was available but the run failed "
                 f"(success rate now {procedure.success_rate:.0%}). The recipe may "
                 f"be stale (UI changed) — verify before trusting.",
                 "procedure.downweighted")
        # Repeated-failure detection: did this run fail the same way a prior
        # episode already records?
        repeated = await _detect_repeated_failure(
            episode_store, user_id=user_id, goal=goal,
            step_descriptions=step_descriptions, this_run_id=run_id,
        )
        if repeated:
            _add(LessonKind.REPEATED_FAILURE,
                 f"This failure mode has been seen before: {repeated}. "
                 f"A different approach is needed, not a retry.",
                 "flagged")

    # New-obstruction lesson: an obstruction episode whose situation had no
    # prior obstruction recorded is genuinely new knowledge.
    for ep in episodes:
        if ep.kind.value == "obstruction" and ep.seen_count == 1:
            _add(LessonKind.NEW_OBSTRUCTION,
                 f"Discovered a new obstruction: {ep.what_happened}",
                 "episode.added")
            break  # one such lesson per run is enough signal

    summary = _summarize(lessons, run_succeeded, len(episodes))
    return {
        "lessons": lessons,
        "summary": summary,
        "procedure": procedure,
        "episodes_added": len(episodes),
    }


async def _detect_repeated_failure(
    episode_store: EpisodeStore, *, user_id: str, goal: str,
    step_descriptions: list[str], this_run_id: str,
) -> str | None:
    """Was there already a failure/give-up episode for this situation from a
    DIFFERENT run? If so, this failure is a repeat — a stronger signal than a
    one-off."""
    from app.services.memory.episodic import situation_key
    keys = [situation_key(goal=goal)]
    for d in step_descriptions:
        keys.append(situation_key(goal=goal, step_intent=d))
    for k in keys:
        if not k:
            continue
        for ep in await episode_store.find(user_id, k):
            if ep.kind.value in ("step_failure", "gave_up") and ep.run_id != this_run_id:
                return ep.what_happened
            # seen_count > 1 means it recurred even within capture
            if ep.kind.value in ("step_failure", "gave_up") and ep.seen_count >= 2:
                return ep.what_happened
    return None


def _summarize(lessons: list[Lesson], succeeded: bool, n_episodes: int) -> str:
    if not lessons:
        return (
            f"Run {'succeeded' if succeeded else 'failed'}; no new actionable "
            f"lessons (memory unchanged beyond {n_episodes} episode(s))."
        )
    parts = [f"Reflection produced {len(lessons)} lesson(s):"]
    for l in lessons:
        parts.append(f"  - [{l.kind.value}] {l.summary}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Optional: a store for lessons, if you want to keep a reflection log.
# Lessons already take effect via routing; this just retains them for review.
# ---------------------------------------------------------------------------

class InMemoryLessonStore:
    def __init__(self) -> None:
        self._by_user: dict[str, list[Lesson]] = {}

    async def add_many(self, lessons: list[Lesson]) -> None:
        for l in lessons:
            self._by_user.setdefault(l.user_id, []).append(l)

    async def list_for_user(self, user_id: str) -> list[Lesson]:
        return list(self._by_user.get(user_id, []))