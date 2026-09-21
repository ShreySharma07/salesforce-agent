"""
The mock LLM's canned plan must pass the same guardrails a real plan does.

Mock mode is how the demo, the CI suite, and every contributor without an API
key exercise the product. When the fixture drifts from the plan contract the
whole journey dead-ends at approval with a 422, which is exactly what happened:
the fixture still navigated to `login.salesforce.com`, a pattern the prompt
forbids and the guardrails reject, so no mock-mode plan could be approved.
"""
from __future__ import annotations

import json

from app.agent.plan_generator import _build_plan_object
from app.core.guardrails import validate_plan
from app.core.llm.mock import _CANNED_TEXT_RESPONSES


def _mock_plan():
    data = json.loads(_CANNED_TEXT_RESPONSES["plan_synthesis"])
    return _build_plan_object(data, source_video_id="fixture")


def test_mock_plan_passes_the_guardrails():
    """If this fails, mock-mode demos cannot get past the approval step."""
    report = validate_plan(_mock_plan())
    assert report.ok, f"mock plan is not approvable: {report.errors}"


def test_mock_plan_never_logs_in_manually():
    """Authentication belongs to the open_app frontdoor, never to plan steps."""
    plan = _mock_plan()
    for step in plan.steps:
        url = str(step.details.get("url", "")).lower()
        assert "login" not in url, f"{step.id} navigates to a login page: {url}"
        assert "signin" not in url, f"{step.id} navigates to a sign-in page: {url}"


def test_mock_plan_starts_by_opening_the_app():
    """A fresh container has no session, so the first step must establish one."""
    first = _mock_plan().steps[0]
    assert first.details.get("intent") == "open_app", (
        "the first step should be open_app so the agent lands authenticated"
    )
