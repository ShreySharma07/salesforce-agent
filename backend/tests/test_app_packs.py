"""
App packs and the plan capability check.

Sandbox side: Salesforce knowledge lives in a pack, selected per plan, and
the generic agent carries none of it. Backend side: a plan's requirements are
extracted and diffed against a (mocked) org, user permissions and the agent's
own tools.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.schemas.plan import Plan, Step, StepKind

SF7_PLAN = Path(__file__).resolve().parent.parent / "sf7_plan.json"


def _plan(*steps: Step, app: str | None = None) -> Plan:
    return Plan(id="p", goal="g", app=app, steps=list(steps))


def _nav(sid: str, url: str) -> Step:
    return Step(id=sid, kind=StepKind.NAVIGATE, description="go", details={"url": url})


# ---------------------------------------------------------------------------
# Pack selection (sandbox and backend must agree)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plan,expected", [
    (_plan(_nav("s1", "https://acme.lightning.force.com/lightning/o/Case/list")), "salesforce"),
    (_plan(Step(id="s1", kind=StepKind.UI_ACTION, description="open",
                details={"intent": "open_app", "target_description": "Salesforce Lightning"})), "salesforce"),
    (_plan(_nav("s1", "https://en.wikipedia.org/wiki/Salesforce")), "generic"),
    (_plan(_nav("s1", "https://en.wikipedia.org/"), app="salesforce"), "salesforce"),
    (_plan(Step(id="s1", kind=StepKind.WAIT, description="w", details={"seconds": 1})), "salesforce"),
])
def test_pack_selection_matches_on_both_sides(plan, expected):
    from app.packs import pack_for_plan as backend_pick
    from sandbox_agent.packs import pack_for_plan as sandbox_pick
    from sandbox_agent.schemas import Plan as SandboxPlan

    assert backend_pick(plan).name == expected
    assert sandbox_pick(SandboxPlan.model_validate(plan.model_dump(mode="json"))).name == expected


def test_backend_and_sandbox_primitives_stay_in_sync():
    from app.packs import SALESFORCE
    from sandbox_agent.packs import SALESFORCE_PACK

    assert set(SALESFORCE_PACK.sequence_primitives) == set(SALESFORCE.sequence_primitives)


def test_unknown_primitive_fails_the_sequence():
    from sandbox_agent.packs import GENERIC_PACK

    assert GENERIC_PACK.execute_sequence_sub_action(None, "click_pencil_icon", {}).startswith("FAILED")


# ---------------------------------------------------------------------------
# Prompt split
# ---------------------------------------------------------------------------

def test_salesforce_rules_live_only_in_the_salesforce_pack():
    from sandbox_agent import browser_mode as bm
    from sandbox_agent.packs import GENERIC_PACK, SALESFORCE_PACK

    sf, generic = bm.build_system_prompt(SALESFORCE_PACK), bm.build_system_prompt(GENERIC_PACK)
    for marker in ("ADVANCED SEARCH DOES NOT EXIST", "CONSOLE WORKSPACE TABS", "/lightning/o/<Object>/list"):
        assert marker in sf
        assert marker not in generic
    # Pack rules sit inside the Rules list, before the reasoning discipline.
    assert sf.index("CONSOLE WORKSPACE TABS") < sf.index("=== REASONING DISCIPLINE")
    assert len(generic) < len(sf)


def test_generic_pack_does_not_trust_salesforce_hosts():
    from sandbox_agent import browser_mode as bm
    from sandbox_agent.packs import GENERIC_PACK, SALESFORCE_PACK

    try:
        bm.set_active_pack(GENERIC_PACK)
        bm.set_navigation_allowlist(["https://en.wikipedia.org/wiki/X"])
        assert bm.navigation_blocked_reason("https://en.wikipedia.org/wiki/Y") is None
        assert bm.navigation_blocked_reason("https://acme.my.salesforce.com/") is not None
    finally:
        bm.set_active_pack(SALESFORCE_PACK)


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------

def test_requirements_extracted_from_the_recorded_plan():
    from app.packs.salesforce import extract_requirements

    plan = Plan.model_validate(json.loads(SF7_PLAN.read_text()))
    reqs = {r.label: r for r in extract_requirements(plan)}
    assert set(reqs) == {
        "Case", "Case list view 'Acme_Cases'", "Case.Contact Name", "Case.Status",
        "Case.Status = Escalated", "Task", "Task.Subject", "Task.Assigned To",
        "Task.Due Date", "Task.Comments", "Task.Status", "Task.Status = In Progress",
    }
    assert reqs["Case"].access == {"read", "update"}
    assert reqs["Task"].access == {"create"}
    assert reqs["Task.Subject"].access == {"create"}
    assert reqs["Case.Status"].access == {"update"}
    assert "step_010" in reqs["Case.Status = Escalated"].steps


@pytest.mark.parametrize("ui_label,api_name", [
    ("Assigned To", "OwnerId"),      # label "Assigned To ID"
    ("Due Date", "ActivityDate"),    # label "Due Date Only"
    ("Comments", "Description"),     # label "Comments"
    ("Subject", "Subject"),
])
def test_field_matching_bridges_ui_and_api_labels(ui_label, api_name):
    from app.packs.salesforce import match_field

    assert match_field(TASK_FIELDS, ui_label)["name"] == api_name


def test_contact_name_matches_the_contact_lookup():
    from app.packs.salesforce import match_field

    assert match_field(CASE_FIELDS, "Contact Name")["name"] == "ContactId"
    assert match_field(CASE_FIELDS, "Favourite Colour") is None


# ---------------------------------------------------------------------------
# Org probe against a mocked Salesforce
# ---------------------------------------------------------------------------

TASK_FIELDS = [
    {"name": "Subject", "label": "Subject", "type": "combobox", "createable": True, "updateable": True},
    {"name": "OwnerId", "label": "Assigned To ID", "type": "reference", "relationshipName": "Owner",
     "createable": True, "updateable": True},
    {"name": "ActivityDate", "label": "Due Date Only", "type": "date", "createable": True, "updateable": True},
    {"name": "Description", "label": "Comments", "type": "textarea", "createable": True, "updateable": True},
    {"name": "Status", "label": "Status", "type": "picklist", "createable": True, "updateable": True,
     "picklistValues": [{"value": "Not Started", "label": "Not Started", "active": True},
                        {"value": "In Progress", "label": "In Progress", "active": True}]},
]
CASE_FIELDS = [
    {"name": "ContactId", "label": "Contact ID", "type": "reference", "relationshipName": "Contact",
     "createable": True, "updateable": True},
    # This org's Status picklist has no "Escalated", and the user can't edit it.
    {"name": "Status", "label": "Status", "type": "picklist", "createable": True, "updateable": False,
     "picklistValues": [{"value": "New", "label": "New", "active": True},
                        {"value": "Closed", "label": "Closed", "active": True}]},
]


def _fake_org(task_createable: bool = True) -> httpx.MockTransport:
    base = "/services/data/v60.0"
    routes = {
        f"{base}/sobjects/": {"sobjects": [
            {"name": "Case", "label": "Case", "queryable": True, "createable": True, "updateable": True},
            {"name": "Task", "label": "Task", "queryable": True, "createable": task_createable, "updateable": True},
        ]},
        f"{base}/sobjects/Case/describe/": {"fields": CASE_FIELDS},
        f"{base}/sobjects/Task/describe/": {"fields": TASK_FIELDS},
        f"{base}/sobjects/Case/listviews": {"listviews": [
            {"developerName": "AllOpenCases", "label": "All Open Cases"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        body = routes.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json=[])

    return httpx.MockTransport(handler)


def _by_name(checks):
    return {(c.category, c.name): c for c in checks}


def test_probe_reports_have_and_missing():
    from app.packs.salesforce import OrgClient, extract_requirements, probe_org

    plan = Plan.model_validate(json.loads(SF7_PLAN.read_text()))

    async def run():
        async with OrgClient(access_token="tok", instance_url="https://acme.my.salesforce.com",
                             transport=_fake_org(task_createable=False)) as org:
            return await probe_org(extract_requirements(plan), org)

    checks = _by_name(asyncio.run(run()))
    S = lambda cat, name: checks[(cat, name)].status.value  # noqa: E731

    assert S("org", "Task.Status = In Progress") == "available"
    assert S("org", "Case.Status = Escalated") == "missing"
    assert "New, Closed" in checks[("org", "Case.Status = Escalated")].detail
    assert S("org", "Case list view 'Acme_Cases'") == "missing"
    assert S("org", "Task.Due Date") == "available"
    assert "ActivityDate" in checks[("org", "Task.Due Date")].detail
    assert S("permission", "create Task records") == "missing"
    assert S("permission", "update Case.Status") == "missing"
    assert S("permission", "update Case.ContactId") == "available"
    assert S("permission", "create Task.Subject") == "available"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from app.config import get_settings
    from app.db import models  # noqa: F401
    from app.db.base import Base, close_engine, get_engine
    from app.services.memory import sql_store  # noqa: F401

    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    get_settings.cache_clear()

    async def _create():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c

    async def _drop():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await close_engine()

    asyncio.run(_drop())


def _save_sf7_for_default_user():
    from app.config import get_settings
    from app.services.run_repo import get_repository

    plan = Plan.model_validate(json.loads(SF7_PLAN.read_text()))
    asyncio.run(get_repository().save_plan(plan, user_id=get_settings().default_user_id))
    return plan


def test_endpoint_without_salesforce_connected(client):
    plan = _save_sf7_for_default_user()
    r = client.get(f"/plans/{plan.id}/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app"] == "salesforce" and body["connected"] is False
    org_rows = [c for c in body["checks"] if c["category"] in ("org", "permission")]
    assert org_rows and all(c["status"] == "unknown" for c in org_rows)
    conn = [c for c in body["checks"] if c["kind"] == "connection"]
    assert conn and conn[0]["status"] == "missing"
    prims = {c["name"]: c["status"] for c in body["checks"] if c["kind"] == "primitive"}
    assert prims["Salesforce primitive 'select_dropdown_option'"] == "available"
    assert body["counts"]["unknown"] == len(org_rows)


def test_endpoint_with_salesforce_connected(client, monkeypatch):
    from app.config import get_settings
    from app.db.base import get_sessionmaker
    from app.services import capabilities, vault

    plan = _save_sf7_for_default_user()

    async def _connect():
        async with get_sessionmaker()() as s:
            await vault.store_credential(
                s, user_id=get_settings().default_user_id, provider="salesforce", kind="oauth",
                secret={"access_token": "tok", "refresh_token": "r", "expires_at": time.time() + 3600},
                public_metadata={"instance_url": "https://acme.my.salesforce.com"},
            )
            await s.commit()

    asyncio.run(_connect())

    real = capabilities.sf_pack.OrgClient

    class MockedOrg(real):
        def __init__(self, **kw):
            kw["transport"] = _fake_org()
            super().__init__(**kw)

    monkeypatch.setattr(capabilities.sf_pack, "OrgClient", MockedOrg)
    body = client.get(f"/plans/{plan.id}/capabilities").json()
    assert body["connected"] is True and body["org_url"] == "https://acme.my.salesforce.com"
    rows = {(c["category"], c["name"]): c["status"] for c in body["checks"]}
    assert rows[("org", "Case.Status = Escalated")] == "missing"
    assert rows[("permission", "create Task records")] == "available"
    assert rows[("agent", "salesforce account connected")] == "available"


def test_endpoint_hides_other_users_plans(client):
    from app.services.run_repo import get_repository

    plan = Plan.model_validate(json.loads(SF7_PLAN.read_text()))
    asyncio.run(get_repository().save_plan(plan, user_id="someone_else"))
    assert client.get(f"/plans/{plan.id}/capabilities").status_code == 404
