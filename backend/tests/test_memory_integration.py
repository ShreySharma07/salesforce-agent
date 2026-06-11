"""
Integration test: the full memory loop through the integration layer.

Proves the wiring is coherent: first run has no hint, reflection learns from
it, second similar run gets a primed hint. Uses in-memory stores via the
factory override (no DB, no LLM).

Run from backend/:  python -m tests.test_memory_integration
"""
from __future__ import annotations

import asyncio
import sys

from app.schemas.plan import Plan, Step, StepKind
from app.services.memory.store import InMemoryProcedureStore
from app.services.memory.episodic import InMemoryEpisodeStore
from app.services.memory.reflection import InMemoryLessonStore
from app.services.memory import factory
from app.services.memory.integration import prime_steps, reflect_after_run


def _plan():
    return Plan(
        id="plan_lead",
        goal="Create a new lead for Acme Corp",
        summary="create a lead",
        steps=[
            Step(id="step_001", kind=StepKind.UI_ACTION,
                 description="fill the new lead form and save"),
        ],
    )


def _se(step_id, status, trace):
    return {"step_id": step_id, "status": status, "trace": trace,
            "error": None, "pause_reason": None}


GOOD_TRACE = [
    {"iteration": 1, "thought": "click new lead", "action": "click",
     "action_args": {"ref": 9}, "observation": "clicked", "error": None},
    {"iteration": 2, "thought": "fill name", "action": "fill",
     "action_args": {"ref": 11, "text": "Acme"}, "observation": "filled", "error": None},
    {"iteration": 3, "thought": "done", "action": "done",
     "action_args": {}, "observation": "lead created", "error": None},
]


async def test_full_loop():
    # Override factory singletons with in-memory stores.
    factory.set_stores_for_testing(
        procedure=InMemoryProcedureStore(),
        episode=InMemoryEpisodeStore(),
        lesson=InMemoryLessonStore(),
    )

    plan = _plan()

    # --- Run 1: no prior memory -> no hint. ---
    hints1 = await prime_steps(user_id="user_local", plan=plan)
    assert hints1 == {}, f"first run should have no hints, got {hints1}"

    # Reflect on a successful run 1 (learns the procedure, but 1 success != trusted).
    await reflect_after_run(
        user_id="user_local", plan=plan,
        step_executions=[_se("step_001", "succeeded", GOOD_TRACE)],
        interventions=[], run_id="run_1", run_succeeded=True,
    )

    # After ONE success the procedure exists but isn't trusted yet, so priming
    # still withholds it (conservative). No hint.
    hints_after_1 = await prime_steps(user_id="user_local", plan=plan)
    assert hints_after_1 == {}, "one success should not yet prime (untrusted)"

    # --- Run 2: another success -> now trusted. ---
    await reflect_after_run(
        user_id="user_local", plan=plan,
        step_executions=[_se("step_001", "succeeded", GOOD_TRACE)],
        interventions=[], run_id="run_2", run_succeeded=True,
    )

    # --- Run 3: now memory is trusted -> prime returns a hint on step_001. ---
    hints3 = await prime_steps(user_id="user_local", plan=plan)
    assert "step_001" in hints3, f"trusted procedure should prime step_001, got {hints3}"
    hint = hints3["step_001"]
    assert "MEMORY" in hint and "click" in hint and "fill" in hint
    # Must not leak the filled value.
    assert "Acme" not in hint, "hint must not leak specific values"
    print(f"  full loop ok: untrusted->no hint, trusted->primed ({len(hint)} chars)")


async def test_episodic_caution_primes():
    factory.set_stores_for_testing(
        procedure=InMemoryProcedureStore(),
        episode=InMemoryEpisodeStore(),
        lesson=InMemoryLessonStore(),
    )
    plan = _plan()

    # A human-fix episode is actionable immediately -> should surface in priming
    # even without a trusted procedure.
    iv = [{"reason": "could not find the Save button on the lead form",
           "user_response": "scroll down, Save is below the fold",
           "options": None, "responded_at": None}]
    await reflect_after_run(
        user_id="user_local", plan=plan,
        step_executions=[_se("step_001", "paused", [])],
        interventions=iv, run_id="run_h", run_succeeded=False,
    )
    hints = await prime_steps(user_id="user_local", plan=plan)
    # The human-fix situation_key is built from the intervention reason, which
    # shares the goal context; it should be recalled for this task.
    assert hints, "human-fix episode should produce a priming hint"
    text = next(iter(hints.values()))
    assert "scroll down" in text.lower() or "save" in text.lower()
    print("  episodic human-fix surfaces as caution in priming")


async def test_failure_does_not_prime_garbage():
    factory.set_stores_for_testing(
        procedure=InMemoryProcedureStore(),
        episode=InMemoryEpisodeStore(),
        lesson=InMemoryLessonStore(),
    )
    plan = _plan()
    # A single failed run with a transient error and no resolution -> nothing
    # actionable -> no priming garbage on the next run.
    await reflect_after_run(
        user_id="user_local", plan=plan,
        step_executions=[_se("step_001", "failed",
                             [{"iteration": 1, "thought": "x", "action": "give_up",
                               "action_args": {"reason": "transient blip"},
                               "observation": "blip", "error": None}])],
        interventions=[], run_id="run_f", run_succeeded=False,
    )
    hints = await prime_steps(user_id="user_local", plan=plan)
    # give_up episode has no resolution and seen once -> not actionable -> no hint.
    assert hints == {}, f"a one-off failure should not prime, got {hints}"
    print("  one-off failure does not pollute priming")


def main():
    tests = [test_full_loop, test_episodic_caution_primes, test_failure_does_not_prime_garbage]
    passed = 0
    for t in tests:
        print(f"- {t.__name__}")
        asyncio.run(t()); passed += 1
    print(f"\nAll {passed} integration tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())