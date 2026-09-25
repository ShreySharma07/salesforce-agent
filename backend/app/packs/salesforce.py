"""
Salesforce pack (backend side): what a plan needs from an org, and whether
the connected org and user actually have it.

Two halves:

  extract_requirements(plan)  -- deterministic, no network. Reads the plan's
      Lightning URLs (/lightning/o/<Object>/list?filterName=<View>), SEQUENCE
      sub-actions (field labels, picklist values) and ui_action target
      descriptions ("Status picklist in the New Task modal") and returns the
      objects, fields, picklist values and list views the plan touches, each
      with the access it needs (read / create / update).

  probe_org(requirements, ...) -- REST describe calls with the user's own
      token. Describe results are evaluated as the running user, so
      createable/updateable flags and field visibility ARE the user's
      permissions (profile, permission sets, field-level security).

Label matching is heuristic: the plan speaks in UI labels ("Assigned To",
"Contact Name", "Due Date") while describe returns API labels ("Assigned To
ID", "Contact ID", "Due Date Only"). Each match reports the API field it
resolved to so a wrong match is visible, and nothing unmatched is guessed.

The sandbox-side pack lives in sandbox_agent/packs/salesforce/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app.schemas.capability import CapabilityCheck, CapabilityStatus
from app.schemas.plan import Plan, StepKind

NAME = "salesforce"
DISPLAY_NAME = "Salesforce"
API_VERSION = "v60.0"

HOST_SUFFIXES = (".salesforce.com", ".force.com", ".salesforce-setup.com", ".visualforce.com")

# Deterministic SEQUENCE sub-actions the sandbox pack implements. Mirrors
# sandbox_agent/packs/salesforce SEQUENCE_PRIMITIVES (a test keeps them equal).
SEQUENCE_PRIMITIVES = frozenset({
    "click_pencil_icon", "fill_field", "click_dropdown_result",
    "select_dropdown_option", "click_save_footer",
})

_URL_OBJECT = re.compile(r"/lightning/(o|r)/([A-Za-z][A-Za-z0-9_]*)/(list|new|home|view)?")
_NEW_OBJECT = re.compile(r"\bNew ([A-Z][A-Za-z0-9_]*)\b")
_PAGE_OBJECT = re.compile(r"\b(?:the )?([A-Z][A-Za-z0-9_]*) (?:page|record|Details)\b")
_FIELD_DESC = re.compile(
    r"^(?P<label>[A-Za-z][\w /&'-]*?)\s+"
    r"(?P<type>lookup field|lookup|picklist|text area|textarea|checkbox|field|input)\b",
    re.IGNORECASE,
)
# Words that look like an object in a description but are UI chrome.
_NOT_OBJECTS = {"Related", "Save", "Cancel", "Edit", "Details", "Activity", "Open", "The", "First"}


@dataclass
class Requirement:
    kind: str                      # object | field | picklist_value | list_view
    object: str
    field: str = ""                # UI label for field / picklist_value
    value: str = ""                # picklist value or list view name
    access: set[str] = dc_field(default_factory=set)   # read | create | update
    steps: set[str] = dc_field(default_factory=set)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.object.lower(), self.field.lower(), self.value.lower())

    @property
    def label(self) -> str:
        if self.kind == "object":
            return self.object
        if self.kind == "field":
            return f"{self.object}.{self.field}"
        if self.kind == "picklist_value":
            return f"{self.object}.{self.field} = {self.value}"
        return f"{self.object} list view '{self.value}'"


def _is_literal(value: Any) -> bool:
    """A concrete value we can check (not a ${variable} or a relative date)."""
    text = str(value or "").strip()
    return bool(text) and "${" not in text and "today" not in text.lower()


class _Collector:
    def __init__(self) -> None:
        self._by_key: dict[tuple, Requirement] = {}

    def add(self, kind: str, obj: str, step_id: str, *, field_label: str = "",
            value: str = "", access: str | None = None) -> None:
        req = Requirement(kind=kind, object=obj, field=field_label.strip(), value=value.strip())
        req = self._by_key.setdefault(req.key, req)
        req.steps.add(step_id)
        if access:
            req.access.add(access)
        if kind != "object":  # every field/value/view implies its object
            self.add("object", obj, step_id, access=access)

    def result(self) -> list[Requirement]:
        return list(self._by_key.values())


def extract_requirements(plan: Plan) -> list[Requirement]:
    """Objects, fields, picklist values and list views the plan touches."""
    out = _Collector()
    current_object: str | None = None      # record page / list the agent is on
    for step in plan.steps:
        d = step.details or {}

        url = str(d.get("url", "") or "")
        if url:
            m = _URL_OBJECT.search(urlsplit(url).path)
            if m:
                current_object = m.group(2)
                action = m.group(3)
                out.add("object", current_object, step.id,
                        access="create" if action == "new" else "read")
                for view in parse_qs(urlsplit(url).query).get("filterName", []):
                    out.add("list_view", current_object, step.id, value=view, access="read")

        desc = str(d.get("target_description", "") or "")
        page_obj = _PAGE_OBJECT.search(desc)
        if page_obj and page_obj.group(1) not in _NOT_OBJECTS:
            current_object = page_obj.group(1)

        if step.kind == StepKind.UI_ACTION:
            new_obj = _NEW_OBJECT.search(desc)
            intent = str(d.get("intent", "")).lower()
            if new_obj and new_obj.group(1) not in _NOT_OBJECTS:
                out.add("object", new_obj.group(1), step.id, access="create")
            fm = _FIELD_DESC.match(desc)
            if intent in ("fill", "select", "type") and fm:
                obj = new_obj.group(1) if new_obj else current_object
                if obj:
                    access = "create" if new_obj else "update"
                    out.add("field", obj, step.id, field_label=fm.group("label"), access=access)
                    if fm.group("type").lower() == "picklist" and _is_literal(d.get("value")):
                        out.add("picklist_value", obj, step.id, field_label=fm.group("label"),
                                value=str(d["value"]), access=access)

        if step.kind == StepKind.SEQUENCE and current_object:
            for sub in d.get("steps", []) or []:
                kind = sub.get("kind", "")
                label = sub.get("label") or (sub.get("target") if kind == "click_pencil_icon" else "")
                if label:
                    out.add("field", current_object, step.id, field_label=label, access="update")
                if kind == "select_dropdown_option" and label and _is_literal(sub.get("value")):
                    out.add("picklist_value", current_object, step.id, field_label=label,
                            value=str(sub["value"]), access="update")
    return out.result()


# ---------------------------------------------------------------------------
# Org probe
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _strip_suffix(text: str, *suffixes: str) -> str:
    for sfx in suffixes:
        if text.endswith(" " + sfx):
            return text[: -len(sfx) - 1]
    return text


def match_field(fields: list[dict], ui_label: str) -> dict | None:
    """Resolve a UI label to a describe() field; None when nothing plausible."""
    want = _norm(ui_label)
    want_base = _strip_suffix(want, "name")          # "contact name" -> "contact"
    exact, prefix = None, None
    for f in fields:
        label = _strip_suffix(_norm(f.get("label", "")), "id")
        names = {label, _norm(f.get("name", "")), _norm(f.get("relationshipName") or "")}
        if want in names or (f.get("type") == "reference" and want_base in names):
            exact = exact or f
        elif label.startswith(want + " "):           # "due date" -> "due date only"
            prefix = prefix or f
    return exact or prefix


class OrgClient:
    """Tiny REST client over the user's token; transport is injectable for tests."""

    def __init__(self, *, access_token: str, instance_url: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=instance_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20, transport=transport,
        )

    async def __aenter__(self) -> "OrgClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def get(self, path: str) -> Any:
        r = await self._client.get(f"/services/data/{API_VERSION}{path}")
        r.raise_for_status()
        return r.json()


def _check(category: str, kind: str, req: Requirement, status: CapabilityStatus,
           detail: str, name: str | None = None) -> CapabilityCheck:
    return CapabilityCheck(category=category, kind=kind, name=name or req.label,
                           status=status, detail=detail, steps=sorted(req.steps))


_OBJECT_FLAG = {"read": "queryable", "create": "createable", "update": "updateable"}
_FIELD_FLAG = {"create": "createable", "update": "updateable"}


def unverified_checks(requirements: list[Requirement], reason: str) -> list[CapabilityCheck]:
    """Org and permission rows marked unknown (not connected / API failure)."""
    out: list[CapabilityCheck] = []
    for req in requirements:
        out.append(_check("org", req.kind, req, CapabilityStatus.UNKNOWN, reason))
        if req.kind in ("object", "field"):
            for access in sorted(req.access & set(_OBJECT_FLAG if req.kind == "object" else _FIELD_FLAG)):
                out.append(_check("permission", f"{req.kind}_access", req, CapabilityStatus.UNKNOWN,
                                  reason, name=f"{access} {req.label}"))
    return out


async def probe_org(requirements: list[Requirement], org: OrgClient) -> list[CapabilityCheck]:
    """Check each requirement against the org as the connected user sees it."""
    checks: list[CapabilityCheck] = []
    global_describe = await org.get("/sobjects/")
    sobjects = {s["name"].lower(): s for s in global_describe.get("sobjects", [])}
    sobjects_by_label = {s.get("label", "").lower(): s for s in global_describe.get("sobjects", [])}
    describes: dict[str, dict] = {}
    listviews: dict[str, list[dict]] = {}

    def resolve(obj: str) -> dict | None:
        return sobjects.get(obj.lower()) or sobjects_by_label.get(obj.lower())

    async def describe(api_name: str) -> dict:
        if api_name not in describes:
            describes[api_name] = await org.get(f"/sobjects/{api_name}/describe/")
        return describes[api_name]

    for req in sorted(requirements, key=lambda r: ("object", "field", "picklist_value", "list_view").index(r.kind)):
        sobj = resolve(req.object)
        if sobj is None:
            checks.append(_check("org", req.kind, req, CapabilityStatus.MISSING,
                                 f"object '{req.object}' not found in this org (or not visible to this user)"))
            continue
        api = sobj["name"]

        if req.kind == "object":
            checks.append(_check("org", "object", req, CapabilityStatus.AVAILABLE, f"API name {api}"))
            for access in sorted(req.access):
                flag = _OBJECT_FLAG[access]
                ok = bool(sobj.get(flag))
                checks.append(_check("permission", "object_access", req,
                                     CapabilityStatus.AVAILABLE if ok else CapabilityStatus.MISSING,
                                     f"{flag}={str(ok).lower()} for the connected user",
                                     name=f"{access} {api} records"))

        elif req.kind in ("field", "picklist_value"):
            fields = (await describe(api)).get("fields", [])
            f = match_field(fields, req.field)
            if f is None:
                checks.append(_check("org", req.kind, req, CapabilityStatus.MISSING,
                                     f"no field labelled '{req.field}' on {api} (or hidden from this user by field-level security)"))
                continue
            where = f"matched {api}.{f['name']} (label '{f.get('label', '')}')"
            if req.kind == "field":
                checks.append(_check("org", "field", req, CapabilityStatus.AVAILABLE, where))
                for access in sorted(req.access & set(_FIELD_FLAG)):
                    flag = _FIELD_FLAG[access]
                    ok = bool(f.get(flag))
                    checks.append(_check("permission", "field_access", req,
                                         CapabilityStatus.AVAILABLE if ok else CapabilityStatus.MISSING,
                                         f"{flag}={str(ok).lower()} for the connected user; {where}",
                                         name=f"{access} {api}.{f['name']}"))
            else:
                values = [v for v in f.get("picklistValues", []) if v.get("active", True)]
                hit = any(_norm(v.get("value", "")) == _norm(req.value)
                          or _norm(v.get("label", "")) == _norm(req.value) for v in values)
                shown = ", ".join(v.get("label") or v.get("value", "") for v in values[:12])
                checks.append(_check("org", "picklist_value", req,
                                     CapabilityStatus.AVAILABLE if hit else CapabilityStatus.MISSING,
                                     f"{where}; active values: {shown or '(none)'}"))

        elif req.kind == "list_view":
            if api not in listviews:
                listviews[api] = (await org.get(f"/sobjects/{api}/listviews")).get("listviews", [])
            want = _norm(req.value)
            hit = next((v for v in listviews[api]
                        if _norm(v.get("developerName", "")) == want or _norm(v.get("label", "")) == want), None)
            checks.append(_check("org", "list_view", req,
                                 CapabilityStatus.AVAILABLE if hit else CapabilityStatus.MISSING,
                                 f"list view {hit['developerName']} ('{hit.get('label', '')}')" if hit
                                 else f"no list view '{req.value}' on {api} visible to this user"))
    return checks
