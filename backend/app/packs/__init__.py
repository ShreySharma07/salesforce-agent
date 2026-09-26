"""
Backend view of app packs.

The sandbox owns how a pack drives the UI (sandbox_agent/packs/); the backend
owns what a pack needs from the user's account: which credential it logs in
with, which deterministic primitives the sandbox side offers, and how to
check a plan's requirements against the live org before approval.

Pack selection mirrors sandbox_agent.packs.pack_for_plan so both sides agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.packs import salesforce


@dataclass(frozen=True)
class BackendPack:
    name: str
    display_name: str
    host_suffixes: tuple[str, ...] = ()
    sequence_primitives: frozenset[str] = frozenset()
    credential_provider: str | None = None   # vault provider the org probe and open_app use

    def owns_host(self, host: str) -> bool:
        return any(host.lower().endswith(s) for s in self.host_suffixes)


SALESFORCE = BackendPack(
    name=salesforce.NAME,
    display_name=salesforce.DISPLAY_NAME,
    host_suffixes=salesforce.HOST_SUFFIXES,
    sequence_primitives=salesforce.SEQUENCE_PRIMITIVES,
    credential_provider="salesforce",
)
GENERIC = BackendPack(name="generic", display_name="Generic web")

PACKS: dict[str, BackendPack] = {p.name: p for p in (SALESFORCE, GENERIC)}
DEFAULT = SALESFORCE


def pack_for_plan(plan: Any) -> BackendPack:
    """Same rules as the sandbox: explicit `app`, else Salesforce hosts or an
    open_app into Salesforce, else generic for plans on other hosts only,
    else Salesforce (pre-pack plans)."""
    explicit = (getattr(plan, "app", None) or "").lower()
    if explicit in PACKS:
        return PACKS[explicit]
    hosts: list[str] = []
    for step in getattr(plan, "steps", []) or []:
        details = step.details or {}
        if details.get("url"):
            host = (urlsplit(str(details["url"])).hostname or "").lower()
            if host:
                hosts.append(host)
        mentions = " ".join(str(details.get(k, "")) for k in ("intent", "target_description", "provider"))
        if "open_app" in mentions and "salesforce" in mentions.lower():
            return SALESFORCE
    for pack in PACKS.values():
        if any(pack.owns_host(h) for h in hosts):
            return pack
    return GENERIC if hosts else DEFAULT
