"""
Per-run token authentication, shared by every sandbox-facing endpoint
(/mcp, /sandbox/llm, /sandbox/frontdoor).

The backend mints a random RUN_TOKEN when it spawns a sandbox and stores only
its SHA-256 hash on the Run row. The sandbox sends the raw token back on every
call; we hash it and compare in constant time. A token is valid only for the
run it was minted for, and a run with no stored hash is never accepted.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException

from app.schemas.run import Run, RunStatus
from app.services.run_repo import get_repository

# A run token is only valid while its run is actually executing. Once the run
# reaches any terminal state the container is gone, so a token still being
# presented means it leaked (from a log, a crash dump, a stale process) and
# must be refused rather than honoured.
ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.PROVISIONING,
    RunStatus.RUNNING,
}


def hash_run_token(token: str) -> str:
    """SHA-256 hex digest of a raw run token (what the Run row stores)."""
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    """Extract the raw token from an `Authorization: Bearer <token>` header."""
    if authorization and authorization.startswith("Bearer "):
        tok = authorization.removeprefix("Bearer ").strip()
        return tok or None
    return None


async def authenticate_run(run_id: str | None, authorization: str | None) -> Run:
    """Return the Run if (run_id, bearer token) is a valid pair, else raise 401.

    Never falls back to a default user: sandbox-facing endpoints hand out
    credentials and LLM quota, so anonymous access is never acceptable.
    """
    if not run_id:
        raise HTTPException(401, "run_id required")
    token = bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "missing run token")
    run = await get_repository().get_run(run_id)
    if run is None or not run.mcp_token_hash:
        raise HTTPException(401, "invalid run token")
    if not hmac.compare_digest(hash_run_token(token), run.mcp_token_hash):
        raise HTTPException(401, "invalid run token")
    _require_active(run)
    return run


def _require_active(run: Run) -> None:
    """Refuse a token whose run has already finished, failed or been canceled."""
    if run.status not in ACTIVE_RUN_STATUSES:
        raise HTTPException(
            401,
            f"run {run.id} is {run.status.value}; its token is no longer valid",
        )


def authorized_mcp_tools(run: Run) -> set[tuple[str, str]] | None:
    """The (server, tool) pairs this run's approved plan actually declares.

    A run token should only buy what the approved plan asked for. Without this
    a leaked token could call ANY registered MCP tool as the run's owner, for
    example reading the whole Salesforce org from a plan that was approved
    only to update one field.

    Returns None when the run carries no plan snapshot, which only happens for
    runs created before snapshots existed; the caller then falls back to
    permitting the call and logs it.
    """
    if not run.plan_snapshot:
        return None
    allowed: set[tuple[str, str]] = set()
    for step in (run.plan_snapshot.get("steps") or []):
        if step.get("kind") != "mcp_call":
            continue
        details = step.get("details") or {}
        server, tool = details.get("server"), details.get("tool")
        if server and tool:
            allowed.add((str(server).lower(), str(tool)))
    return allowed


async def authenticate_run_by_token(run_token: str | None) -> Run:
    """Variant for endpoints that only receive the raw token (frontdoor
    redirects carry it as a query parameter). Looks the run up by hash."""
    if not run_token:
        raise HTTPException(401, "run_token required")
    run = await get_repository().get_run_by_token_hash(hash_run_token(run_token))
    if run is None:
        raise HTTPException(401, "invalid run token")
    _require_active(run)
    return run
