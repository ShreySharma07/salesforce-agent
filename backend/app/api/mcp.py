"""
MCP HTTP gateway. The sandbox calls back to this endpoint to invoke tools.

  GET  /mcp/servers                       — list available MCP servers
  GET  /mcp/{server}/tools                — list a server's tools
  POST /mcp/{server}/{tool}               — invoke a tool

The sandbox sends:
  POST /mcp/salesforce/create_lead
  Body: {"args": {...}, "run_id": "run_xxx"}

The endpoint:
  1. Look up the Run -> user
  2. If the server needs credentials, fetch from vault (auto-refresh if expired)
  3. Invoke MCPServer.call_tool(tool, args, credentials)
  4. Return the result

Security: every call MUST carry the run_id in the body and the per-run
token as `Authorization: Bearer <RUN_TOKEN>`. The token is hashed and compared
to the hash stored on the Run; the Run's automation then determines which
user's vault credentials are used. There is no anonymous / default-user
fallback — an unauthenticated caller can never reach a user's integrations.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.services.mcp import (
    MCPAuthError,
    MCPError,
    MCPInvalidArgs,
    MCPToolNotFound,
    get_mcp_server,
    list_mcp_servers,
)
from app.services.oauth.refresh import get_valid_oauth_credential
from app.services.run_repo import get_repository
from app.services.vault import (
    CredentialNotFound,
    VaultError,
    decrypt_payload,
    get_credential_row,
)


router = APIRouter(prefix="/mcp", tags=["mcp"])
log = logging.getLogger("mcp")


class ToolCallRequest(BaseModel):
    args: dict[str, Any] = {}
    run_id: str | None = None


# -----------------------------------------------------------------------
# Discovery endpoints
# -----------------------------------------------------------------------

@router.get("/servers")
async def list_servers() -> list[dict[str, Any]]:
    """Discovery: every registered MCP server with its credential provider and tool count."""
    return [
        {
            "name": s.name,
            "credential_provider": s.credential_provider,
            "tool_count": len(await s.list_tools()),
        }
        for s in list_mcp_servers()
    ]


@router.get("/{server}/tools")
async def list_tools(server: str) -> list[dict[str, Any]]:
    """Discovery: the tool schemas one MCP server exposes."""
    try:
        srv = get_mcp_server(server)
    except KeyError:
        raise HTTPException(404, f"unknown MCP server: {server}")
    tools = await srv.list_tools()
    return [t.model_dump() for t in tools]


# -----------------------------------------------------------------------
# Tool invocation (the meat)
# -----------------------------------------------------------------------

@router.post("/{server}/{tool}")
async def call_tool(
    server: str,
    tool: str,
    body: ToolCallRequest,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Invoke one MCP tool on behalf of a run.

    Validates the per-run bearer token (401 otherwise), resolves run -> user,
    fetches (and auto-refreshes) the user's credential from the vault, then
    dispatches to the MCP server.
    """
    # 1. Resolve the server
    try:
        srv = get_mcp_server(server)
    except KeyError:
        raise HTTPException(404, f"unknown MCP server: {server}")

    # 2. Validate the run token, resolve the owner, and confirm the approved
    #    plan actually authorizes THIS tool.
    user_id = await _resolve_user_for_run(body.run_id, authorization, server=server, tool=tool)

    # 3. Fetch credentials if the server needs them
    credentials: dict[str, Any] | None = None
    if srv.credential_provider:
        try:
            credentials = await _fetch_credential(
                session,
                user_id=user_id,
                provider=srv.credential_provider,
            )
        except CredentialNotFound as e:
            raise HTTPException(
                401,
                f"{srv.credential_provider} not connected for this user. "
                f"User must connect via /oauth/{srv.credential_provider}/connect first.",
            ) from e
        except VaultError as e:
            raise HTTPException(500, f"vault error: {e}") from e

    # 4. Invoke
    try:
        result = await srv.call_tool(tool, body.args, credentials)
    except MCPToolNotFound as e:
        raise HTTPException(404, str(e)) from e
    except MCPInvalidArgs as e:
        raise HTTPException(400, str(e)) from e
    except MCPAuthError as e:
        raise HTTPException(401, str(e)) from e
    except MCPError as e:
        raise HTTPException(500, f"MCP error: {e}") from e

    log.info("MCP call ok: %s/%s by user=%s run=%s",
             server, tool, user_id, body.run_id)
    return {"ok": True, "result": result}


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

async def _resolve_user_for_run(run_id: str | None, authorization: str | None,
                                *, server: str, tool: str) -> str:
    """Validate the per-run bearer token and return the owning user_id.

    Rejects (401) when: no run_id, unknown run, run already finished, run has
    no issued token, missing/malformed Authorization header, or hash mismatch.
    Uses a constant-time compare on the hashes.

    Rejects (403) when the approved plan never declared this tool. The token
    proves WHICH run is calling; the plan defines what that run is allowed to
    do, and those are different questions.
    """
    from app.services.run_auth import authenticate_run, authorized_mcp_tools

    run = await authenticate_run(run_id, authorization)

    allowed = authorized_mcp_tools(run)
    if allowed is None:
        log.warning("run %s has no plan snapshot — cannot scope %s/%s, allowing",
                    run.id, server, tool)
    elif (server.lower(), tool) not in allowed:
        log.warning("run %s attempted undeclared tool %s/%s (allowed: %s)",
                    run.id, server, tool, sorted(allowed) or "none")
        raise HTTPException(
            403,
            f"run {run.id} is not authorized to call {server}/{tool}: its "
            f"approved plan does not declare that tool",
        )

    repo = get_repository()
    automation = await repo.get_automation(run.automation_id)
    if automation is None or not automation.user_id:
        raise HTTPException(401, "run has no owning automation/user")
    return automation.user_id


async def _fetch_credential(
    session: AsyncSession,
    *,
    user_id: str,
    provider: str,
) -> dict[str, Any]:
    """Get a credential, auto-refreshing OAuth tokens if necessary."""
    row = await get_credential_row(session, user_id=user_id, provider=provider)
    if row is None:
        raise CredentialNotFound(f"no credential for {user_id}/{provider}")

    if row.kind == "oauth":
        # Auto-refreshes if expired
        return await get_valid_oauth_credential(session, user_id=user_id, provider=provider)
    # api_key or username_password — return as-is
    return decrypt_payload(row.secret_ciphertext)