"""
A `sequence` step is only verified when it actually saved.

Real plans split an inline Salesforce edit in two: a `sequence` opens the
pencil and sets the field, then a SEPARATE `ui_action` clicks Save. The
sequence's success_condition describes the SAVED, read-only state, so it
cannot be true while the field is still in edit mode.

Verifying it there anyway failed every such step and paused the run, which
looked exactly like the agent quitting right after opening the record. This
is the sf7_plan shape, where step_008 sets Contact Name and step_009 saves it.
"""
from __future__ import annotations

import pytest

from sandbox_agent import browser_mode, executor
from sandbox_agent.schemas import Plan as SbPlan, RunRequest, Step as SbStep, StepKind as SbKind


@pytest.fixture
def condition_never_satisfied(monkeypatch):
    """The checker understands the phrasing and reports NOT satisfied."""
    calls: list[str] = []

    def _check(page, condition):
        calls.append(condition)
        return False

    monkeypatch.setattr(browser_mode, "check_sequence_condition", _check)
    monkeypatch.setattr(browser_mode, "execute_sequence_sub_action",
                        lambda page, kind, sub: f"{kind} ok")
    return calls


def _sequence(sub_kinds, condition):
    return SbStep(
        id="step_008", kind=SbKind.SEQUENCE,
        description="Set Contact Name via inline pencil edit",
        details={"steps": [{"kind": k} for k in sub_kinds]},
        success_condition=condition,
    )


CONDITION = "The Contact Name field shows 'Rachel Torres' as a linked-record pill in read-only view"


def test_sequence_without_save_is_not_failed_by_verification(condition_never_satisfied):
    """step_008 sets the field; step_009 saves it. It must not fail here."""
    step = _sequence(["click_pencil_icon", "fill_field", "click_dropdown_result"], CONDITION)
    req = RunRequest(plan=SbPlan(id="p", goal="g", steps=[step]))

    result = executor._run_step(None, None, step, req, variables={})

    assert result.status == "succeeded", (
        "a sequence that does not save must not be failed by a post-check of "
        "the saved state"
    )
    assert "unverified" in result.detail
    # The pre-check still ran (idempotency); the post-check did not.
    assert len(condition_never_satisfied) == 1


def test_sequence_that_saves_is_still_verified(condition_never_satisfied):
    """When the sequence commits the value, an unmet condition IS a failure."""
    step = _sequence(
        ["click_pencil_icon", "fill_field", "click_dropdown_result", "click_save_footer"],
        CONDITION,
    )
    req = RunRequest(plan=SbPlan(id="p", goal="g", steps=[step]))

    result = executor._run_step(None, None, step, req, variables={})

    assert result.status == "failed"
    assert "NOT satisfied after saving" in result.detail
    # Checked twice: once before acting, once after saving.
    assert len(condition_never_satisfied) == 2


def test_already_satisfied_sequence_self_skips(monkeypatch):
    """Idempotency is unaffected: an already-set field is skipped."""
    monkeypatch.setattr(browser_mode, "check_sequence_condition", lambda page, c: True)
    monkeypatch.setattr(browser_mode, "execute_sequence_sub_action",
                        lambda page, kind, sub: pytest.fail("must not act when already satisfied"))

    step = _sequence(["click_pencil_icon", "fill_field"], CONDITION)
    req = RunRequest(plan=SbPlan(id="p", goal="g", steps=[step]))

    result = executor._run_step(None, None, step, req, variables={})
    assert result.status == "succeeded"
    assert "[idempotent]" in result.detail


def test_unknown_condition_phrasing_never_fails_a_step(monkeypatch):
    """A phrasing the checker cannot evaluate must not invent a failure."""
    monkeypatch.setattr(browser_mode, "check_sequence_condition", lambda page, c: None)
    monkeypatch.setattr(browser_mode, "execute_sequence_sub_action",
                        lambda page, kind, sub: f"{kind} ok")

    step = _sequence(["click_pencil_icon", "fill_field", "click_save_footer"],
                     "Some phrasing the deterministic checker does not understand")
    req = RunRequest(plan=SbPlan(id="p", goal="g", steps=[step]))

    result = executor._run_step(None, None, step, req, variables={})
    assert result.status == "succeeded"
