"""
App-pack registry.

`pack_for_plan(plan)` picks the pack a run uses:
  1. `plan.app` when the plan names one;
  2. otherwise Salesforce when any navigate URL is on a Salesforce host or the
     plan opens the Salesforce app;
  3. otherwise the generic pack when the plan navigates only to other hosts;
  4. otherwise Salesforce — plans written before packs existed were all
     Salesforce plans, so they keep today's behaviour.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sandbox_agent.packs.base import AppPack
from sandbox_agent.packs.salesforce import SALESFORCE_PACK

# No app knowledge at all: generic rules only, no hooks, no primitives.
GENERIC_PACK = AppPack(name="generic", display_name="Generic web")

_PACKS: dict[str, AppPack] = {p.name: p for p in (SALESFORCE_PACK, GENERIC_PACK)}

DEFAULT_PACK = SALESFORCE_PACK


def get_pack(name: str | None) -> AppPack:
    """The named pack; unknown or empty names fall back to the default."""
    return _PACKS.get((name or "").lower(), DEFAULT_PACK)


def available_packs() -> list[str]:
    return sorted(_PACKS)


def pack_for_plan(plan: Any) -> AppPack:
    explicit = getattr(plan, "app", None)
    if explicit and explicit.lower() in _PACKS:
        return _PACKS[explicit.lower()]

    hosts: list[str] = []
    for step in getattr(plan, "steps", []) or []:
        details = getattr(step, "details", {}) or {}
        url = details.get("url")
        if url:
            host = (urlsplit(str(url)).hostname or "").lower()
            if host:
                hosts.append(host)
        mentions = " ".join(str(details.get(k, "")) for k in ("intent", "target_description", "provider"))
        if "open_app" in mentions and "salesforce" in mentions.lower():
            return SALESFORCE_PACK

    for pack in _PACKS.values():
        if any(pack.owns_host(h) for h in hosts):
            return pack
    if hosts:
        return GENERIC_PACK
    return DEFAULT_PACK


__all__ = [
    "AppPack", "DEFAULT_PACK", "GENERIC_PACK", "SALESFORCE_PACK",
    "available_packs", "get_pack", "pack_for_plan",
]
