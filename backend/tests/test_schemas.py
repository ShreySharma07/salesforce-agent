"""Verify schemas roundtrip cleanly through JSON."""
from __future__ import annotations

from datetime import datetime

from app.schemas import (
    Automation,
    Credential,
    DecisionRule,
    LiveViewSettings,
    Plan,
    PlanStatus,
    Run,
    RunStatus,
    RunTrigger,
    Schedule,
    Step,
    StepKind,
)


def test_plan_roundtrip():
    plan = Plan(
        id="plan_001",
        goal="Test goal",
        steps=[
            Step(id="s1", kind=StepKind.NAVIGATE, description="Open SF",
                 details={"url": "https://login.salesforce.com"}),
            Step(id="s2", kind=StepKind.UI_ACTION, description="Click Leads tab",
                 details={"intent": "Click Leads tab",
                          "target_description": "the Leads tab in the navigation"}),
        ],
        decision_rules=[DecisionRule(rule="Only process @company.com emails")],
        required_credentials=[Credential(name="salesforce", scopes=["lead.read", "lead.write"])],
    )
    decoded = Plan.model_validate_json(plan.model_dump_json())
    assert decoded.id == "plan_001"
    assert decoded.steps[0].kind == StepKind.NAVIGATE
    assert decoded.steps[0].details["url"] == "https://login.salesforce.com"
    assert decoded.required_credentials[0].name == "salesforce"


def test_run_roundtrip():
    run = Run(
        id="run_001", automation_id="auto_001", plan_version=1,
        status=RunStatus.RUNNING, triggered_by=RunTrigger.MANUAL,
        started_at=datetime.utcnow(),
    )
    decoded = Run.model_validate_json(run.model_dump_json())
    assert decoded.status == RunStatus.RUNNING
    assert decoded.triggered_by == RunTrigger.MANUAL


def test_automation_roundtrip():
    auto = Automation(
        id="auto_001", user_id="user_priya", name="Morning Lead Triage",
        plan_id="plan_001",
        schedule=Schedule(cron="0 8 * * 1-5", timezone="America/Los_Angeles"),
        live_view=LiveViewSettings(default=False),
    )
    decoded = Automation.model_validate_json(auto.model_dump_json())
    assert decoded.name == "Morning Lead Triage"
    assert decoded.live_view.default is False
    assert decoded.schedule.cron == "0 8 * * 1-5"


def test_plan_default_status():
    plan = Plan(id="plan_002", goal="g")
    assert plan.status == PlanStatus.DRAFT
    assert plan.version == 1