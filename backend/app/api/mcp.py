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

Security: this endpoint is INTERNAL — sandbox calls it via host.docker.internal.
For local dev that's localhost. For production, this becomes a private VPC
endpoint that only sandboxes can reach. We do basic auth via the run_id +
sandbox_id verification (Phase 2a.2). For now: trust the run_id.
"""
from __future__ import annotations

import hashlib
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
    # 1. Resolve the server
    try:
        srv = get_mcp_server(server)
    except KeyError:
        raise HTTPException(404, f"unknown MCP server: {server}")

    # 2. Look up which user this run belongs to + validate run token
    user_id = await _resolve_user_for_run(body.run_id, authorization)

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

async def _resolve_user_for_run(run_id: str | None, authorization: str | None) -> str:
    """Look up which user owns this run and validate the per-run bearer token.

    Falls back to default single-user mode when no run_id is supplied
    (useful for direct testing). When a run IS found and it carries a
    stored token hash, the Authorization header must match.
    """
    from app.config import get_settings

    settings = get_settings()
    if not run_id:
        return settings.default_user_id

    repo = get_repository()
    run = await repo.get_run(run_id)
    if run is None:
        return settings.default_user_id

    # Validate bearer token if the run was issued one.
    if run.mcp_token_hash is not None:
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ")
        if token is None or hashlib.sha256(token.encode()).hexdigest() != run.mcp_token_hash:
            raise HTTPException(401, "invalid run token")

    automation = await repo.get_automation(run.automation_id)
    if automation is None:
        return settings.default_user_id
    return automation.user_id or settings.default_user_id


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