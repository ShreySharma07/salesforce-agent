"""
Memory integration for the run pipeline.

Two entry points the automation runner calls:

  prime_steps(...)  -> dict[step_id, hint_text]
      BEFORE a run: for each UI step, look up procedural + episodic memory and
      build the priming text the sandbox will inject into that step's prompt.

  reflect_after_run(...)
      AFTER a run: distill procedural memory, capture episodes, and run
      reflection to emit lessons. Updates the stores.

This module is the ONLY thing the runner needs to import. It owns the stores
(singletons) so the runner doesn't manage memory state. The store factory
returns SQL-backed stores in production and can be swapped for in-memory in
tests.
"""
from __future__ import annotations

import logging

from app.schemas.plan import Plan
from app.services.memory.store import (
    retrieve as retrieve_procedure,
    build_priming_text,
)
from app.services.memory.episodic import (
    recall,
    build_episodic_priming,
)
from app.services.memory.reflection import reflect_on_run
from app.services.memory.factory import (
    get_procedure_store,
    get_episode_store,
    get_lesson_store,
)

log = logging.getLogger(__name__)


def _plan_goal_and_steps(plan: Plan) -> tuple[str, list[str]]:
    goal = getattr(plan, "goal", "") or getattr(plan, "summary", "") or ""
    step_descs = [getattr(s, "description", "") or "" for s in plan.steps]
    return goal, step_descs


async def prime_steps(*, user_id: str, plan: Plan) -> dict[str, str]:
    """Build per-step priming hints from memory. Returns {step_id: hint}.

    Only UI-ish steps get a hint, and only when memory actually has something
    relevant (and trusted, for procedures). Steps with no relevant memory are
    simply absent from the map => no priming, no prompt bloat.
    """
    goal, step_descs = _plan_goal_and_steps(plan)
    proc_store = get_procedure_store()
    epi_store = get_episode_store()

    # Procedural: one recipe for the whole task (if trusted).
    proc = await retrieve_procedure(
        proc_store, user_id=user_id, goal=goal, step_descriptions=step_descs,
    )
    proc_text = build_priming_text(proc) if (proc and proc.is_trusted) else ""

    # Episodic: cautions relevant to this task's situations.
    episodes = await recall(
        epi_store, user_id=user_id, goal=goal, step_descriptions=step_descs,
    )
    epi_text = build_episodic_priming(episodes)

    combined = "\n\n".join([t for t in (proc_text, epi_text) if t])
    if not combined:
        return {}

    # Attach the combined hint to the FIRST UI step of the plan — that's where
    # the agent benefits most from "here's how this task goes / what to watch
    # for" before it starts acting. (A future refinement could scope hints
    # per-step; whole-task priming on step 1 is the high-value version.)
    hints: dict[str, str] = {}
    for s in plan.steps:
        kind = getattr(s, "kind", None)
        kind_val = getattr(kind, "value", kind)
        if kind_val in ("ui_action", "extract", "decision", "loop", "navigate"):
            hints[s.id] = combined
            break
    if hints:
        log.info("memory: primed step %s for user=%s (%d chars)",
                 next(iter(hints)), user_id, len(combined))
    return hints


async def reflect_after_run(
    *,
    user_id: str,
    plan: Plan,
    step_executions: list,
    interventions: list,
    run_id: str,
    run_succeeded: bool,
) -> None:
    """After a run: learn procedurally, capture episodes, reflect. Best-effort
    — memory failures must never break the run pipeline."""
    goal, step_descs = _plan_goal_and_steps(plan)
    try:
        result = await reflect_on_run(
            user_id=user_id, goal=goal, step_descriptions=step_descs,
            step_executions=step_executions, interventions=interventions,
            run_id=run_id, run_succeeded=run_succeeded,
            procedure_store=get_procedure_store(),
            episode_store=get_episode_store(),
        )
        lessons = result.get("lessons", [])
        if lessons:
            await get_lesson_store().add_many(lessons)
        log.info("memory: reflected on run %s — %s", run_id, result.get("summary", ""))
    except Exception as e:  # never let memory break a run
        log.warning("memory: reflection failed for run %s: %s", run_id, e)