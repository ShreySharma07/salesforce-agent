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

    Each UI-ish step gets its OWN episodic hint keyed to that step's description,
    so a failure/resolution recorded for step_013 reaches step_013 on the next
    run — not step_000 where it would be forgotten 12 steps later.

    Procedural memory (the whole-task recipe) is retrieved once and shared
    across all steps that receive a hint — it doesn't change per step. Episodic
    memory is recalled once per UI step using that step's description as the
    situation anchor. The latency cost is one lightweight SQL lookup per step at
    run-setup time (not in the hot ReAct loop), which is acceptable.

    Steps with no relevant memory are absent from the map => no prompt bloat.
    """
    goal, step_descs = _plan_goal_and_steps(plan)
    proc_store = get_procedure_store()
    epi_store = get_episode_store()

    # Procedural: one recipe for the whole task (if trusted). Retrieved once
    # and reused across steps — the recipe doesn't change step-by-step.
    proc = await retrieve_procedure(
        proc_store, user_id=user_id, goal=goal, step_descriptions=step_descs,
    )
    proc_text = build_priming_text(proc) if (proc and proc.is_trusted) else ""

    # Episodic: per-step recall keyed to each step's description so the hint
    # reaches the step that actually needs it.
    hints: dict[str, str] = {}
    for s in plan.steps:
        kind = getattr(s, "kind", None)
        kind_val = getattr(kind, "value", kind)
        if kind_val not in ("ui_action", "extract", "decision", "loop", "navigate"):
            continue

        step_eps = await recall(
            epi_store, user_id=user_id, goal=goal,
            step_descriptions=[s.description],   # scope to THIS step's situation
        )
        step_epi_text = build_episodic_priming(step_eps)

        combined = "\n\n".join(t for t in (proc_text, step_epi_text) if t)
        if combined:
            hints[s.id] = combined
            log.info(
                "memory: primed step %s ('%s') for user=%s (%d chars, %d episodes)",
                s.id, s.description[:60], user_id, len(combined), len(step_eps),
            )

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