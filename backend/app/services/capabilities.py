"""
Plan capability check: "for this org I have these, I lack those".

Builds a CapabilityReport for one plan in three groups:
  org         objects / fields / picklist values / list views the plan touches
  permission  whether the connected user may read / create / edit them
  agent       step types, pack primitives, MCP tools and logins the agent needs

Org and permission rows come from the pack's live probe with the user's own
token; if the app isn't connected (or the probe fails) they come back
`unknown` with the reason rather than failing the whole report.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.packs import BackendPack, pack_for_plan
from app.packs import salesforce as sf_pack
from app.schemas.capability import CapabilityCheck, CapabilityReport, CapabilityStatus
from app.schemas.plan import Plan, StepKind
from app.services.oauth.refresh import get_valid_oauth_credential
from app.services.vault import CredentialNotFound, VaultError, get_credential_row

log = logging.getLogger(__name__)

# Every step kind the sandbox executor implements (sandbox_agent/executor.py).
SUPPORTED_STEP_KINDS = frozenset(StepKind)


def _agent(kind: str, name: str, ok: bool, detail: str, steps: list[str]) -> CapabilityCheck:
    return CapabilityCheck(
        category="agent", kind=kind, name=name,
        status=CapabilityStatus.AVAILABLE if ok else CapabilityStatus.MISSING,
        detail=detail, steps=sorted(set(steps)),
    )


async def _connected_providers(session: AsyncSession, user_id: str, providers: set[str]) -> dict[str, bool]:
    out = {}
    for provider in providers:
        row = await get_credential_row(session, user_id=user_id, provider=provider)
        out[provider] = row is not None
    return out


async def agent_checks(plan: Plan, pack: BackendPack, session: AsyncSession, user_id: str) -> list[CapabilityCheck]:
    """What the agent must provide: step types, pack primitives, tools, logins."""
    from app.services.mcp import get_mcp_server

    kinds: dict[str, list[str]] = {}
    primitives: dict[str, list[str]] = {}
    mcp_calls: dict[tuple[str, str], list[str]] = {}
    open_app: dict[str, list[str]] = {}
    for step in plan.steps:
        kinds.setdefault(step.kind.value, []).append(step.id)
        d = step.details or {}
        if step.kind == StepKind.SEQUENCE:
            for sub in d.get("steps", []) or []:
                primitives.setdefault(sub.get("kind", "?"), []).append(step.id)
        if step.kind == StepKind.MCP_CALL:
            mcp_calls.setdefault((str(d.get("server", "")), str(d.get("tool", ""))), []).append(step.id)
        if step.kind == StepKind.UI_ACTION and str(d.get("intent", "")).lower() == "open_app":
            provider = str(d.get("provider") or pack.credential_provider or "")
            open_app.setdefault(provider, []).append(step.id)

    checks: list[CapabilityCheck] = []
    for kind, steps in sorted(kinds.items()):
        ok = StepKind(kind) in SUPPORTED_STEP_KINDS
        checks.append(_agent("step_kind", f"step type '{kind}'", ok,
                             "handled by the sandbox executor" if ok else "no executor for this step type", steps))
    for prim, steps in sorted(primitives.items()):
        ok = prim in pack.sequence_primitives
        checks.append(_agent("primitive", f"{pack.display_name} primitive '{prim}'", ok,
                             f"provided by the {pack.display_name} pack" if ok
                             else f"the {pack.display_name} pack has no '{prim}' (has: {', '.join(sorted(pack.sequence_primitives)) or 'none'})",
                             steps))

    needed_providers = {p for p in open_app if p}
    servers = {}
    for (server, tool), steps in sorted(mcp_calls.items()):
        try:
            srv = get_mcp_server(server)
        except KeyError:
            checks.append(_agent("mcp_tool", f"tool {server}/{tool}", False, f"no MCP server '{server}'", steps))
            continue
        tools = {t.name for t in await srv.list_tools()}
        ok = tool in tools
        checks.append(_agent("mcp_tool", f"tool {server}/{tool}", ok,
                             f"offered by MCP server '{server}'" if ok
                             else f"'{server}' has no tool '{tool}' (has: {', '.join(sorted(tools))})", steps))
        if srv.credential_provider:
            needed_providers.add(srv.credential_provider)
            servers.setdefault(srv.credential_provider, []).extend(steps)

    connected = await _connected_providers(session, user_id, needed_providers)
    for provider in sorted(needed_providers):
        steps = open_app.get(provider, []) + servers.get(provider, [])
        ok = connected[provider]
        checks.append(_agent("connection", f"{provider} account connected", ok,
                             "connected; the sandbox logs in via a one-time frontdoor URL / tool gateway" if ok
                             else f"connect {provider} on the dashboard (OAuth) before running", steps))
    return checks


async def _salesforce_org(session: AsyncSession, user_id: str) -> tuple[str, str] | None:
    """(access_token, instance_url) for the user's Salesforce, refreshed; None if not connected."""
    try:
        secret = await get_valid_oauth_credential(session, user_id=user_id, provider="salesforce")
    except CredentialNotFound:
        return None
    row = await get_credential_row(session, user_id=user_id, provider="salesforce")
    instance_url = (row.public_metadata or {}).get("instance_url") if row else None
    token = secret.get("access_token")
    if not token or not instance_url:
        return None
    return token, instance_url


async def check_plan_capabilities(
    plan: Plan,
    *,
    session: AsyncSession,
    user_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CapabilityReport:
    pack = pack_for_plan(plan)
    report = CapabilityReport(plan_id=plan.id, plan_version=plan.version, app=pack.name,
                              connected=pack.credential_provider is None)
    report.checks.extend(await agent_checks(plan, pack, session, user_id))

    if pack.name != sf_pack.NAME:
        return report  # generic pack: nothing org-specific to verify

    requirements = sf_pack.extract_requirements(plan)
    reason = "Salesforce is not connected for this user; connect it to verify"
    try:
        creds = await _salesforce_org(session, user_id)
    except (VaultError, httpx.HTTPError) as e:
        # e.g. undecryptable row or a refresh the provider rejected
        log.warning("capabilities: could not load Salesforce credential for %s: %s", user_id, e)
        creds, reason = None, "the stored Salesforce credential could not be used; reconnect Salesforce"
    if creds is None:
        report.checks.extend(sf_pack.unverified_checks(requirements, reason))
        return report

    token, instance_url = creds
    report.connected = True
    report.org_url = instance_url
    try:
        async with sf_pack.OrgClient(access_token=token, instance_url=instance_url, transport=transport) as org:
            report.checks.extend(await sf_pack.probe_org(requirements, org))
    except httpx.HTTPStatusError as e:
        report.checks.extend(sf_pack.unverified_checks(
            requirements, f"Salesforce API returned {e.response.status_code} while describing the org"))
    except httpx.HTTPError as e:
        report.checks.extend(sf_pack.unverified_checks(
            requirements, f"could not reach Salesforce ({type(e).__name__})"))
    return report
