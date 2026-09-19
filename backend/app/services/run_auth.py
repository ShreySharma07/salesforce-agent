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

from app.schemas.run import Run
from app.services.run_repo import get_repository


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
    return run


async def authenticate_run_by_token(run_token: str | None) -> Run:
    """Variant for endpoints that only receive the raw token (frontdoor
    redirects carry it as a query parameter). Looks the run up by hash."""
    if not run_token:
        raise HTTPException(401, "run_token required")
    run = await get_repository().get_run_by_token_hash(hash_run_token(run_token))
    if run is None:
        raise HTTPException(401, "invalid run token")
    return run
