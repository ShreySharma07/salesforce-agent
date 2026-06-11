"""
Tests for procedural memory, exercised against the REAL trace shapes this
project produces (taken from actual stored runs in the build log).

Run from backend/:  python -m tests.test_memory
(Plain asserts so it runs without pytest installed.)
"""
from __future__ import annotations

import asyncio
import sys

from app.services.memory.signature import normalize_goal, signature_for_plan
from app.services.memory.distiller import distill_run_to_procedure
from app.services.memory.store import (
    InMemoryProcedureStore,
    retrieve,
    learn_from_run,
    build_priming_text,
)


# ---------------------------------------------------------------------------
# Real trace fixtures — shapes copied from actual runs in the project.
# ---------------------------------------------------------------------------

# From the real open_app run (run_6fc0295618): the agent emitted open_app,
# landed in Salesforce. We model a SUCCESSFUL version of that step's trace.
OPEN_APP_TRACE = [
    {
        "iteration": 1,
        "thought": "The goal requires Salesforce. I will use open_app to land logged in.",
        "action": "open_app",
        "action_args": {"provider": "salesforce"},
        "observation": "opened salesforce, logged in",
        "error": None,
    },
    {
        "iteration": 2,
        "thought": "I am on the Salesforce home. I will open the App Launcher.",
        "action": "click",
        "action_args": {"ref": 12},
        "observation": "clicked element #12",
        "error": None,
    },
    {
        "iteration": 3,
        "thought": "A welcome modal is covering the page, dismiss it.",
        "action": "dismiss_obstruction",
        "action_args": {"ref": 4},
        "observation": "dismissed obstruction via element #4",
        "error": None,
    },
    {
        "iteration": 4,
        "thought": "Now I can see Leads. The goal is met.",
        "action": "done",
        "action_args": {"evidence": "Leads list visible"},
        "observation": "Leads list visible",
        "error": None,
    },
]

# A trace with noise: a failed attempt the agent recovered from, plus a
# reason-only turn. The distiller must strip these.
NOISY_TRACE = [
    {"iteration": 1, "thought": "think first", "action": "(reason)", "action_args": {}, "observation": "", "error": None},
    {"iteration": 2, "thought": "click new", "action": "click", "action_args": {"ref": 3}, "observation": "", "error": "TimeoutError: not found"},
    {"iteration": 3, "thought": "scroll to find the button", "action": "scroll", "action_args": {"direction": "down"}, "observation": "scrolled down", "error": None},
    {"iteration": 4, "thought": "now click new lead", "action": "click", "action_args": {"ref": 9}, "observation": "clicked element #9", "error": None},
    {"iteration": 5, "thought": "fill the name", "action": "fill", "action_args": {"ref": 11, "text": "Acme"}, "observation": "filled", "error": None},
    {"iteration": 6, "thought": "done", "action": "done", "action_args": {}, "observation": "saved", "error": None},
]


def _se(step_id, status, trace):
    """Mimic a StepExecution as a plain dict (distiller reads both)."""
    return {"step_id": step_id, "status": status, "trace": trace, "pause_reason": None}


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------

def test_signatures_generalize_over_values():
    a = normalize_goal("Create a new lead for Acme Corp")
    b = normalize_goal("Create a new lead for Globex Inc")
    assert a == b, f"expected same signature, got {a!r} vs {b!r}"
    assert "create" in a and "lead" in a
    print(f"  signature generalizes: {a!r}")


def test_signatures_distinguish_tasks():
    create = normalize_goal("Create a new lead")
    update = normalize_goal("Update an account billing address")
    assert create != update
    print(f"  distinct tasks differ: {create!r} != {update!r}")


def test_signature_ignores_ids_and_urls():
    a = normalize_goal("open account 001Hu0003bC3ioIAC at https://x.my.salesforce.com")
    assert "001" not in a and "http" not in a
    print(f"  ids/urls stripped: {a!r}")


# ---------------------------------------------------------------------------
# Distiller tests
# ---------------------------------------------------------------------------

def test_distill_strips_noise_and_terminals():
    proc = distill_run_to_procedure(
        user_id="user_local",
        goal="Create a new lead for Acme",
        step_descriptions=["fill the lead form", "save"],
        step_executions=[_se("step_001", "succeeded", NOISY_TRACE)],
        run_id="run_test1",
        run_succeeded=True,
    )
    assert proc is not None
    actions = [s.action for s in proc.steps]
    # The failed click (errored), the (reason) turn, and the terminal 'done'
    # must all be gone. The recovered scroll + click + fill remain.
    assert "done" not in actions
    assert actions == ["scroll", "click", "fill"], actions
    print(f"  distilled clean steps: {actions}")


def test_distill_captures_obstruction():
    proc = distill_run_to_procedure(
        user_id="user_local",
        goal="Open Salesforce and view Leads",
        step_descriptions=["open salesforce", "go to leads"],
        step_executions=[_se("step_002", "succeeded", OPEN_APP_TRACE)],
        run_id="run_test2",
        run_succeeded=True,
    )
    assert proc is not None
    actions = [s.action for s in proc.steps]
    assert actions == ["open_app", "click"], actions
    # dismiss_obstruction becomes a known_obstruction, not a replay step.
    assert len(proc.known_obstructions) == 1
    assert "modal" in proc.known_obstructions[0].description.lower()
    print(f"  obstruction captured: {proc.known_obstructions[0].description!r}")


def test_failed_run_teaches_nothing():
    proc = distill_run_to_procedure(
        user_id="user_local",
        goal="Create a lead",
        step_descriptions=["fill form"],
        step_executions=[_se("step_001", "failed", NOISY_TRACE)],
        run_id="run_test3",
        run_succeeded=False,
    )
    assert proc is None
    print("  failed run distills to None (correct)")


# ---------------------------------------------------------------------------
# Store + retrieval + reinforcement tests
# ---------------------------------------------------------------------------

async def test_learn_retrieve_and_reinforce():
    store = InMemoryProcedureStore()

    # First successful run -> a fresh procedure, not yet trusted (1 success).
    p1 = await learn_from_run(
        store, user_id="user_local",
        goal="Create a new lead for Acme",
        step_descriptions=["fill the lead form", "save"],
        step_executions=[_se("s1", "succeeded", NOISY_TRACE)],
        run_id="run_a", run_succeeded=True,
    )
    assert p1 is not None and p1.times_succeeded == 1
    assert not p1.is_trusted, "one success should not be trusted yet"

    # Retrieve with a DIFFERENT company name -> same procedure (generalized).
    got = await retrieve(
        store, user_id="user_local",
        goal="Create a new lead for Globex",
        step_descriptions=["fill the lead form", "save"],
    )
    assert got is not None and got.id == p1.id, "should retrieve same proc for value-variant goal"

    # Second success -> reinforced, now trusted.
    p2 = await learn_from_run(
        store, user_id="user_local",
        goal="Create a new lead for Initech",
        step_descriptions=["fill the lead form", "save"],
        step_executions=[_se("s1", "succeeded", NOISY_TRACE)],
        run_id="run_b", run_succeeded=True,
    )
    assert p2.id == p1.id, "should reinforce, not create a duplicate"
    assert p2.times_succeeded == 2 and p2.is_trusted, "two successes -> trusted"
    assert set(p2.source_run_ids) == {"run_a", "run_b"}

    # A later FAILED run -> attempt counter rises, success_rate drops.
    p3 = await learn_from_run(
        store, user_id="user_local",
        goal="Create a new lead for Umbrella",
        step_descriptions=["fill the lead form", "save"],
        step_executions=[_se("s1", "failed", NOISY_TRACE)],
        run_id="run_c", run_succeeded=False,
    )
    assert p3.times_attempted == 3 and p3.times_succeeded == 2
    assert abs(p3.success_rate - 2/3) < 1e-6
    print(f"  learn/retrieve/reinforce ok; final rate={p3.success_rate:.2f} trusted={p3.is_trusted}")


async def test_user_isolation():
    store = InMemoryProcedureStore()
    await learn_from_run(
        store, user_id="alice",
        goal="Create a lead", step_descriptions=["fill"],
        step_executions=[_se("s1", "succeeded", NOISY_TRACE)],
        run_id="ra", run_succeeded=True,
    )
    # Bob asking for the same task gets nothing — memory is per-user.
    got = await retrieve(store, user_id="bob", goal="Create a lead", step_descriptions=["fill"])
    assert got is None, "memory must not leak across users"
    print("  per-user isolation ok")


def test_priming_text_is_compact_and_safe():
    proc = distill_run_to_procedure(
        user_id="user_local",
        goal="Create a new lead for Acme",
        step_descriptions=["fill form"],
        step_executions=[_se("s1", "succeeded", NOISY_TRACE)],
        run_id="run_x", run_succeeded=True,
    )
    text = build_priming_text(proc)
    # Must NOT leak the actual filled value ("Acme") — only arg keys.
    assert "Acme" not in text, "priming text must not leak specific values"
    assert "MEMORY" in text and "scroll" in text and "fill" in text
    print("  priming text compact + no value leak")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    sync_tests = [
        test_signatures_generalize_over_values,
        test_signatures_distinguish_tasks,
        test_signature_ignores_ids_and_urls,
        test_distill_strips_noise_and_terminals,
        test_distill_captures_obstruction,
        test_failed_run_teaches_nothing,
        test_priming_text_is_compact_and_safe,
    ]
    async_tests = [
        test_learn_retrieve_and_reinforce,
        test_user_isolation,
    ]
    passed = 0
    for t in sync_tests:
        print(f"- {t.__name__}")
        t()
        passed += 1
    for t in async_tests:
        print(f"- {t.__name__}")
        asyncio.run(t())
        passed += 1
    print(f"\nAll {passed} memory tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())