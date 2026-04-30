"""Verify schemas roundtrip cleanly through JSON. The schemas are
contracts between many components; if they break, everything breaks."""
from __future__ import annotations

from datetime import datetime

from app.schemas import (
    Automation,
    Credential,
    DecisionRule,
    LiveViewSettings,
    NavigateDetails,
    Plan,
    PlanStatus,
    Run,
    RunStatus,
    RunTrigger,
    Schedule,
    Step,
    StepKind,
    UIActionDetails,
)


def test_plan_roundtrip():
    plan = Plan(
        id="plan_001",
        goal="Test goal",
        steps=[
            Step(
                id="step_001",
                kind=StepKind.NAVIGATE,
                description="Open Salesforce",
                details=NavigateDetails(url="https://login.salesforce.com").model_dump(),
            ),
            Step(
                id="step_002",
                kind=StepKind.UI_ACTION,
                description="Click the Leads tab",
                details=UIActionDetails(
                    intent="Click Leads tab",
                    target_description="the Leads tab in the navigation",
                ).model_dump(),
            ),
        ],
        decision_rules=[
            DecisionRule(rule="Only process emails from @company.com"),
        ],
        required_credentials=[
            Credential(name="salesforce", scopes=["lead.read", "lead.write"]),
        ],
    )
    encoded = plan.model_dump_json()
    decoded = Plan.model_validate_json(encoded)
    assert decoded.id == "plan_001"
    assert decoded.steps[0].kind == StepKind.NAVIGATE
    assert decoded.required_credentials[0].name == "salesforce"


def test_run_roundtrip():
    run = Run(
        id="run_001",
        automation_id="auto_001",
        plan_version=1,
        status=RunStatus.RUNNING,
        triggered_by=RunTrigger.MANUAL,
        started_at=datetime.utcnow(),
    )
    encoded = run.model_dump_json()
    decoded = Run.model_validate_json(encoded)
    assert decoded.status == RunStatus.RUNNING
    assert decoded.triggered_by == RunTrigger.MANUAL


def test_automation_roundtrip():
    auto = Automation(
        id="auto_001",
        user_id="user_priya",
        name="Morning Lead Triage",
        plan_id="plan_001",
        schedule=Schedule(cron="0 8 * * 1-5", timezone="America/Los_Angeles"),
        live_view=LiveViewSettings(default=False),
    )
    encoded = auto.model_dump_json()
    decoded = Automation.model_validate_json(encoded)
    assert decoded.name == "Morning Lead Triage"
    assert decoded.live_view.default is False
    assert decoded.schedule.cron == "0 8 * * 1-5"


def test_plan_default_status():
    plan = Plan(id="plan_002", goal="g")
    assert plan.status == PlanStatus.DRAFT
    assert plan.version == 1