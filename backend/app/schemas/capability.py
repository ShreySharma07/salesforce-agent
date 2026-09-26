"""
Capability report: what a plan needs vs what this org, user and agent have.

Produced by GET /plans/{id}/capabilities before approval, so a user sees
"this org has X, lacks Y" instead of discovering it mid-run.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class CapabilityStatus(str, Enum):
    AVAILABLE = "available"   # verified present / allowed
    MISSING = "missing"       # verified absent or not allowed
    UNKNOWN = "unknown"       # could not verify (not connected, API error, ...)


# org        = metadata the plan touches (objects, fields, picklist values, list views)
# permission = what the connected user may do with it (create / edit / read)
# agent      = what the agent itself provides (step types, pack primitives, tools, logins)
CapabilityCategory = Literal["org", "permission", "agent"]


class CapabilityCheck(BaseModel):
    category: CapabilityCategory
    kind: str = Field(..., description="object | field | picklist_value | list_view | object_access | field_access | step_kind | primitive | mcp_tool | connection")
    name: str = Field(..., description="Human-readable requirement, e.g. 'Task.Status = In Progress'")
    status: CapabilityStatus
    detail: str = ""
    steps: list[str] = Field(default_factory=list, description="Plan step ids that need this")


class CapabilityReport(BaseModel):
    plan_id: str
    plan_version: int
    app: str
    connected: bool = Field(..., description="Whether the app's account is connected for this user")
    org_url: str | None = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    checks: list[CapabilityCheck] = Field(default_factory=list)

    @computed_field  # serialised, so the dashboard gets the totals directly
    @property
    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in CapabilityStatus}
        for c in self.checks:
            out[c.status.value] += 1
        return out
