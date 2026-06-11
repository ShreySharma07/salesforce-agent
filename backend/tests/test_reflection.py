"""
Tests for reflection — the layer that ties procedural + episodic together
and emits actionable lessons.

The thing these tests guard against: vacuous reflection. Every lesson must be
concrete and routed to a real effect. A run with nothing to learn must
produce ZERO lessons, not a "be more careful" note.

Run from backend/:  python -m tests.test_reflection
"""
from __future__ import annotations

import asyncio
import sys

from app.schemas.memory import LessonKind
from app.services.memory.store import InMemoryProcedureStore
from app.services.memory.episodic import InMemoryEpisodeStore
from app.services.memory.reflection import reflect_on_run, InMemoryLessonStore


# --- fixtures (real trace shapes) ---

GOOD_TRACE = [
    {"iteration": 1, "thought": "click new lead", "action": "click",
     "action_args": {"ref": 9}, "observation": "clicked", "error": None},
    {"iteration": 2, "thought": "fill name", "action": "fill",
     "action_args": {"ref": 11, "text": "Acme"}, "observation": "filled", "error": None},
    {"iteration": 3, "thought": "done", "action": "done",
     "action_args": {"evidence": "lead created"}, "observation": "lead created", "error": None},
]

# Same task, but bloated with many recovered steps -> inefficiency signal.
SLOW_TRACE = [
    {"iteration": i, "thought": f"try {i}", "action": "scroll",
     "action_args": {"direction": "down"}, "observation": "scrolled", "error": None}
    for i in range(1, 9)
] + GOOD_TRACE

GIVEUP_TRACE = [
    {"iteration": 1, "thought": "cannot find the form", "action": "give_up",
     "action_args": {"reason": "Lead form never appeared"},
     "observation": "Lead form never appeared", "error": None},
]


def _se(step_id, status, trace, error=None, pause_reason=None):
    return {"step_id": step_id, "status": status, "trace": trace,
            "error": error, "pause_reason": pause_reason}


async def _reflect(ps, es, *, goal, steps, sexec, ok, run_id, iv=None):
    return await reflect_on_run(
        user_id="user_local", goal=goal, step_descriptions=steps,
        step_executions=sexec, interventions=iv or [], run_id=run_id,
        run_succeeded=ok, procedure_store=ps, episode_store=es,
    )


# ---------------------------------------------------------------------------

async def test_first_success_is_new_capability():
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    r = await _reflect(
        ps, es, goal="Create a new lead for Acme",
        steps=["fill the lead form and save"],
        sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="run_1",
    )
    kinds = [l.kind for l in r["lessons"]]
    assert LessonKind.NEW_CAPABILITY in kinds, kinds
    assert r["procedure"] is not None
    print(f"  first success -> NEW_CAPABILITY; summary lesson count={len(r['lessons'])}")


async def test_second_success_reinforces():
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    await _reflect(ps, es, goal="Create a new lead for Acme",
                   steps=["fill the lead form and save"],
                   sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="run_1")
    r2 = await _reflect(ps, es, goal="Create a new lead for Globex",
                        steps=["fill the lead form and save"],
                        sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="run_2")
    kinds = [l.kind for l in r2["lessons"]]
    assert LessonKind.REINFORCED_PROCEDURE in kinds, kinds
    assert r2["procedure"].times_succeeded == 2
    print("  second success (different value) -> REINFORCED_PROCEDURE")


async def test_inefficiency_flagged():
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    # Establish a lean procedure (3 steps).
    await _reflect(ps, es, goal="Create a new lead for Acme",
                   steps=["fill the lead form and save"],
                   sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="run_1")
    # Now a success that took ~11 iterations vs ~3 -> inefficiency.
    r = await _reflect(ps, es, goal="Create a new lead for Globex",
                       steps=["fill the lead form and save"],
                       sexec=[_se("s1", "succeeded", SLOW_TRACE)], ok=True, run_id="run_2")
    kinds = [l.kind for l in r["lessons"]]
    assert LessonKind.INEFFICIENCY in kinds, kinds
    print("  bloated success -> INEFFICIENCY flagged")


async def test_failure_with_prior_marks_stale():
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    # Build a trusted procedure first (2 successes).
    await _reflect(ps, es, goal="Create a new lead for A", steps=["fill the lead form"],
                   sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="r1")
    await _reflect(ps, es, goal="Create a new lead for B", steps=["fill the lead form"],
                   sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="r2")
    # Now it fails -> PROCEDURE_MAY_BE_STALE + down-weight.
    r = await _reflect(ps, es, goal="Create a new lead for C", steps=["fill the lead form"],
                       sexec=[_se("s1", "failed", GIVEUP_TRACE, error="form gone")],
                       ok=False, run_id="r3")
    kinds = [l.kind for l in r["lessons"]]
    assert LessonKind.PROCEDURE_MAY_BE_STALE in kinds, kinds
    assert r["procedure"].times_attempted == 3 and r["procedure"].times_succeeded == 2
    print(f"  failure with prior -> PROCEDURE_MAY_BE_STALE; rate now {r['procedure'].success_rate:.0%}")


async def test_repeated_failure_detected():
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    # First failure records an episode.
    await _reflect(ps, es, goal="Create a lead", steps=["fill the lead form"],
                   sexec=[_se("s1", "failed", GIVEUP_TRACE, error="form gone")],
                   ok=False, run_id="r1")
    # Same failure again -> repeated-failure lesson.
    r = await _reflect(ps, es, goal="Create a lead", steps=["fill the lead form"],
                       sexec=[_se("s1", "failed", GIVEUP_TRACE, error="form gone")],
                       ok=False, run_id="r2")
    kinds = [l.kind for l in r["lessons"]]
    assert LessonKind.REPEATED_FAILURE in kinds, kinds
    print("  same failure twice -> REPEATED_FAILURE (try a different approach)")


async def test_no_vacuous_lessons():
    """The critical guard: a run with nothing notable produces NO lessons
    beyond the legitimate first-success capability one. A pure no-op run
    (succeeded, no trace, no prior) yields zero lessons."""
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    # A run that succeeded but had no productive UI actions and no prior
    # memory -> nothing to learn -> zero lessons.
    r = await _reflect(ps, es, goal="Just wait", steps=["wait a moment"],
                       sexec=[_se("s1", "succeeded", [])], ok=True, run_id="r1")
    assert r["lessons"] == [], f"expected no vacuous lessons, got {r['lessons']}"
    assert "no new actionable lessons" in r["summary"]
    print("  no-op run -> ZERO lessons (no theatre)")


async def test_lessons_are_routed():
    """Every lesson must say what it did (routed_to) — reflection's effect is
    auditable, not just narrated."""
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    r = await _reflect(ps, es, goal="Create a new lead for Acme",
                       steps=["fill the lead form and save"],
                       sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="r1")
    for l in r["lessons"]:
        assert l.routed_to, f"lesson {l.kind} has no routed_to"
    print("  every lesson is routed to a concrete effect")


async def test_lesson_store_retains():
    ps, es = InMemoryProcedureStore(), InMemoryEpisodeStore()
    ls = InMemoryLessonStore()
    r = await _reflect(ps, es, goal="Create a new lead for Acme",
                       steps=["fill the lead form and save"],
                       sexec=[_se("s1", "succeeded", GOOD_TRACE)], ok=True, run_id="r1")
    await ls.add_many(r["lessons"])
    kept = await ls.list_for_user("user_local")
    assert len(kept) == len(r["lessons"]) and len(kept) > 0
    print(f"  lesson store retains {len(kept)} lesson(s) for review")


def main():
    tests = [
        test_first_success_is_new_capability,
        test_second_success_reinforces,
        test_inefficiency_flagged,
        test_failure_with_prior_marks_stale,
        test_repeated_failure_detected,
        test_no_vacuous_lessons,
        test_lessons_are_routed,
        test_lesson_store_retains,
    ]
    passed = 0
    for t in tests:
        print(f"- {t.__name__}")
        asyncio.run(t()); passed += 1
    print(f"\nAll {passed} reflection tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())