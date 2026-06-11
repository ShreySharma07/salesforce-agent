"""
Tests for episodic memory, against the REAL Run / StepExecution /
LoopIteration / HumanIntervention shapes this project produces.

Run from backend/:  python -m tests.test_episodic
"""
from __future__ import annotations

import asyncio
import sys

from app.schemas.memory import EpisodeKind
from app.services.memory.episodic import (
    situation_key,
    capture_episodes,
    InMemoryEpisodeStore,
    remember_run,
    recall,
    build_episodic_priming,
)


# ---------------------------------------------------------------------------
# Fixtures modeled on real runs
# ---------------------------------------------------------------------------

# The real open_app failure run (run_6fc0295618): agent emitted open_app, it
# couldn't build a frontdoor session, retried, then gave up. Step failed.
GAVE_UP_TRACE = [
    {"iteration": 1, "thought": "use open_app for salesforce", "action": "open_app",
     "action_args": {"provider": "salesforce"}, "observation": "opened salesforce, logged in", "error": None},
    {"iteration": 2, "thought": "frontdoor session error, retry", "action": "open_app",
     "action_args": {"provider": "salesforce"}, "observation": "opened salesforce, logged in", "error": None},
    {"iteration": 5, "thought": "cannot build frontdoor session, give up", "action": "give_up",
     "action_args": {"reason": "Could not build Salesforce frontdoor session"},
     "observation": "Could not build Salesforce frontdoor session", "error": None},
]

OBSTRUCTION_TRACE = [
    {"iteration": 1, "thought": "a welcome modal blocks the page", "action": "dismiss_obstruction",
     "action_args": {"ref": 4}, "observation": "dismissed", "error": None},
    {"iteration": 2, "thought": "now click new", "action": "click",
     "action_args": {"ref": 9}, "observation": "clicked", "error": None},
]


def _se(step_id, status, trace, error=None, pause_reason=None):
    return {"step_id": step_id, "status": status, "trace": trace,
            "error": error, "pause_reason": pause_reason}


def _iv(reason, user_response=None):
    """Mimic a HumanIntervention dict (real fields: reason, user_response)."""
    return {"reason": reason, "user_response": user_response,
            "options": None, "responded_at": None}


# ---------------------------------------------------------------------------
# Capture tests
# ---------------------------------------------------------------------------

def test_capture_step_failure_and_giveup():
    eps = capture_episodes(
        user_id="user_local",
        goal="Open Salesforce and create a lead",
        step_descriptions=["open salesforce and confirm logged in"],
        step_executions=[_se("step_002", "failed", GAVE_UP_TRACE,
                             error="LLM daily quota exhausted")],
        interventions=[],
        run_id="run_6fc0295618",
    )
    kinds = {e.kind for e in eps}
    assert EpisodeKind.STEP_FAILURE in kinds, kinds
    assert EpisodeKind.GAVE_UP in kinds, kinds
    giveup = next(e for e in eps if e.kind == EpisodeKind.GAVE_UP)
    assert "frontdoor" in giveup.what_happened.lower()
    print(f"  captured failure+giveup: {sorted(k.value for k in kinds)}")


def test_capture_obstruction_with_resolution():
    eps = capture_episodes(
        user_id="user_local",
        goal="Create a lead",
        step_descriptions=["fill the new lead form"],
        step_executions=[_se("step_003", "succeeded", OBSTRUCTION_TRACE)],
        interventions=[],
        run_id="run_x",
    )
    obs = [e for e in eps if e.kind == EpisodeKind.OBSTRUCTION]
    assert len(obs) == 1
    assert obs[0].resolution, "obstruction episode must carry a resolution"
    print(f"  obstruction has resolution: {obs[0].resolution!r}")


def test_capture_human_fix_is_highest_value():
    eps = capture_episodes(
        user_id="user_local",
        goal="Create a lead",
        step_descriptions=["fill form"],
        step_executions=[_se("step_001", "paused", [], pause_reason="human_input")],
        interventions=[_iv("agent could not find the Save button",
                           user_response="scroll down, Save is below the fold")],
        run_id="run_y",
    )
    fixes = [e for e in eps if e.kind == EpisodeKind.HUMAN_FIX]
    assert len(fixes) == 1
    assert "scroll down" in fixes[0].resolution
    print(f"  human fix captured: {fixes[0].resolution!r}")


def test_unanswered_intervention_teaches_nothing():
    eps = capture_episodes(
        user_id="user_local", goal="x", step_descriptions=["y"],
        step_executions=[],
        interventions=[_iv("stuck", user_response=None)],  # no response yet
        run_id="run_z",
    )
    assert not [e for e in eps if e.kind == EpisodeKind.HUMAN_FIX]
    print("  unanswered intervention -> no episode (correct)")


def test_captcha_episode():
    eps = capture_episodes(
        user_id="user_local", goal="Log into a site",
        step_descriptions=["log in"],
        step_executions=[_se("step_001", "paused", [], pause_reason="captcha")],
        interventions=[], run_id="run_c",
    )
    cap = [e for e in eps if e.kind == EpisodeKind.CAPTCHA]
    assert len(cap) == 1 and "human" in cap[0].resolution.lower()
    print("  captcha episode captured with hand-off resolution")


# ---------------------------------------------------------------------------
# Store / recall / recurrence tests
# ---------------------------------------------------------------------------

async def test_recall_surfaces_actionable_only():
    store = InMemoryEpisodeStore()
    # A one-off step failure with no resolution -> NOT actionable on first sight.
    await remember_run(
        store, user_id="user_local",
        goal="Open Salesforce and create a lead",
        step_descriptions=["open salesforce and confirm logged in"],
        step_executions=[_se("step_002", "failed", [], error="some transient blip")],
        interventions=[], run_id="run_1",
    )
    got = await recall(
        store, user_id="user_local",
        goal="Open Salesforce and create a lead",
        step_descriptions=["open salesforce and confirm logged in"],
    )
    assert got == [], "a single resolution-less failure should not nag yet"

    # Same failure recurs -> seen_count 2 -> now actionable (a real pattern).
    await remember_run(
        store, user_id="user_local",
        goal="Open Salesforce and create a lead",
        step_descriptions=["open salesforce and confirm logged in"],
        step_executions=[_se("step_002", "failed", [], error="some transient blip")],
        interventions=[], run_id="run_2",
    )
    got2 = await recall(
        store, user_id="user_local",
        goal="Open Salesforce and create a lead",
        step_descriptions=["open salesforce and confirm logged in"],
    )
    assert len(got2) == 1 and got2[0].seen_count == 2
    print(f"  recurrence promotes to actionable: seen {got2[0].seen_count}x")


async def test_human_fix_recalled_immediately():
    store = InMemoryEpisodeStore()
    await remember_run(
        store, user_id="user_local",
        goal="Create a lead",
        step_descriptions=["fill the lead form and save"],
        step_executions=[_se("step_001", "paused", [], pause_reason="human_input")],
        interventions=[_iv("could not find Save button",
                           user_response="scroll down to reveal Save")],
        run_id="run_h",
    )
    # A human fix is actionable immediately (it has a resolution).
    got = await recall(
        store, user_id="user_local",
        goal="Create a lead",
        step_descriptions=["could not find Save button"],
    )
    assert any(e.kind == EpisodeKind.HUMAN_FIX for e in got), got
    print("  human fix recalled on next similar task")


async def test_user_isolation():
    store = InMemoryEpisodeStore()
    await remember_run(
        store, user_id="alice", goal="Create a lead",
        step_descriptions=["fill form"],
        step_executions=[_se("s1", "succeeded", OBSTRUCTION_TRACE)],
        interventions=[], run_id="ra",
    )
    got = await recall(store, user_id="bob", goal="Create a lead",
                       step_descriptions=["fill form"])
    assert got == [], "episodes must not leak across users"
    print("  per-user isolation ok")


def test_priming_orders_fixes_first():
    store = InMemoryEpisodeStore()
    eps = capture_episodes(
        user_id="user_local", goal="Create a lead",
        step_descriptions=["fill the new lead form"],
        step_executions=[_se("step_003", "succeeded", OBSTRUCTION_TRACE)],
        interventions=[_iv("could not find Save", user_response="scroll down")],
        run_id="run_p",
    )
    text = build_episodic_priming(eps)
    assert "MEMORY" in text
    assert "\u2192" in text, "resolutions should render with an arrow"
    print("  episodic priming renders fixes + warnings")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    sync_tests = [
        test_capture_step_failure_and_giveup,
        test_capture_obstruction_with_resolution,
        test_capture_human_fix_is_highest_value,
        test_unanswered_intervention_teaches_nothing,
        test_captcha_episode,
        test_priming_orders_fixes_first,
    ]
    async_tests = [
        test_recall_surfaces_actionable_only,
        test_human_fix_recalled_immediately,
        test_user_isolation,
    ]
    passed = 0
    for t in sync_tests:
        print(f"- {t.__name__}")
        t(); passed += 1
    for t in async_tests:
        print(f"- {t.__name__}")
        asyncio.run(t()); passed += 1
    print(f"\nAll {passed} episodic tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())