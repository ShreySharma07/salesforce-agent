"""
Sandbox FrontDoor endpoint.

The sandbox browser navigates here to get a logged-in Salesforce session
WITHOUT the sandbox ever holding the Salesforce token.

  GET /sandbox/frontdoor/{provider}?run_token=<RUN_TOKEN>&ret_url=<optional>

Flow:
  1. validate run_token against the Run's stored hash
  2. resolve run -> user
  3. fetch the user's {provider} OAuth token from the vault (refresh if expired)
  4. call singleaccess to mint a one-time frontdoor URL
  5. 302-redirect the sandbox browser to that URL

The raw access token never leaves the backend. The sandbox only ever
follows a single-use redirect.

NOTE (verify against your real modules tomorrow):
  - get_valid_oauth_credential() is assumed to return a dict containing
    "access_token" and "instance_url". If your refresh module returns a
    different shape, adjust the extraction in _get_sf_token().
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import get_session
from app.services.frontdoor import FrontDoorError, build_salesforce_frontdoor
from app.services.oauth.refresh import get_valid_oauth_credential
from app.services.run_repo import get_repository
from app.services.vault import CredentialNotFound


router = APIRouter(prefix="/sandbox/frontdoor", tags=["sandbox-frontdoor"])
log = logging.getLogger("sandbox-frontdoor")


async def _resolve_run_user(run_token: str | None) -> str:
    """Validate the run token and return the owning user_id.

    Unlike /mcp (which can fall back to a default user for direct testing),
    FrontDoor always requires a valid token — it hands out a logged-in
    session, so it must never be reachable without one.
    """
    settings = get_settings()
    if not run_token:
        raise HTTPException(401, "run_token required")

    repo = get_repository()
    token_hash = hashlib.sha256(run_token.encode()).hexdigest()

    # Find the run whose stored hash matches. If your repo has a direct
    # lookup-by-token-hash, use it; otherwise this scans recent runs.
    run = await repo.get_run_by_token_hash(token_hash)  # see Claude Code note
    if run is None:
        raise HTTPException(401, "invalid run token")

    automation = await repo.get_automation(run.automation_id)
    if automation is None:
        raise HTTPException(401, "run has no automation")
    return automation.user_id or settings.default_user_id


async def _get_sf_token(session: AsyncSession, user_id: str) -> tuple[str, str]:
    """Return (access_token, instance_url). access_token comes from the
    (refreshed) secret; instance_url comes from the row's public_metadata."""
    from app.services.vault import get_credential_row

    try:
        secret = await get_valid_oauth_credential(
            session, user_id=user_id, provider="salesforce"
        )
    except CredentialNotFound as e:
        raise HTTPException(
            400, "Salesforce not connected. Connect via /oauth/salesforce/connect first."
        ) from e

    access_token = secret.get("access_token")

    # instance_url is stored as public_metadata on the credential row,
    # not inside the encrypted secret blob.
    row = await get_credential_row(session, user_id=user_id, provider="salesforce")
    instance_url = (row.public_metadata or {}).get("instance_url") if row else None

    if not access_token or not instance_url:
        raise HTTPException(
            500, "Salesforce credential missing access_token or instance_url"
        )
    return access_token, instance_url


@router.get("/{provider}")
async def frontdoor(
    provider: str,
    run_token: str | None = Query(None),
    ret_url: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    if provider.lower() != "salesforce":
        raise HTTPException(404, f"frontdoor not supported for provider {provider!r}")

    user_id = await _resolve_run_user(run_token)
    access_token, instance_url = await _get_sf_token(session, user_id)

    try:
        result = await build_salesforce_frontdoor(
            access_token=access_token,
            instance_url=instance_url,
            ret_url=ret_url,
        )
    except FrontDoorError as e:
        # Don't leak token/url material into the error.
        log.warning("frontdoor build failed for user=%s: %s", user_id, e)
        raise HTTPException(502, "could not build Salesforce frontdoor session")

    # 302 the sandbox browser straight into the logged-in session.
    return RedirectResponse(url=result.frontdoor_url, status_code=302)