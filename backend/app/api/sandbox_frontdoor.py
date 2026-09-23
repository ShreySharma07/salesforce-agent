"""
Sandbox FrontDoor endpoint.

The sandbox calls this to get a logged-in Salesforce session for its
browser WITHOUT the sandbox ever holding the Salesforce token.

  POST /sandbox/frontdoor/{provider}
  Authorization: Bearer <RUN_TOKEN>
  Body: {"run_id": "run_xxx", "ret_url": "/lightning/page/home"}   (ret_url optional)
  -> {"url": "<one-time frontdoor URL>"}

Flow:
  1. validate (run_id, bearer token) against the Run's stored hash
  2. resolve run -> user
  3. fetch the user's {provider} OAuth token from the vault (refresh if expired)
  4. call singleaccess to mint a one-time frontdoor URL
  5. return it; the sandbox browser navigates to it

The run token travels only in the Authorization header — never in a URL,
where it would land in access logs, browser history and Referer headers.
The raw access token never leaves the backend; the sandbox only ever holds
a single-use, short-lived login URL.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import get_session
from app.services.frontdoor import FrontDoorError, build_salesforce_frontdoor
from app.services.oauth.refresh import get_valid_oauth_credential
from app.services.run_repo import get_repository
from app.services.vault import CredentialNotFound


router = APIRouter(prefix="/sandbox/frontdoor", tags=["sandbox-frontdoor"])
log = logging.getLogger("sandbox-frontdoor")


DEFAULT_RET_URL = "/lightning/page/home"


class FrontDoorRequest(BaseModel):
    run_id: str | None = None
    ret_url: str | None = None


class FrontDoorResponse(BaseModel):
    url: str


def _safe_ret_url(ret_url: str | None) -> str:
    """Only a path inside the org is a valid landing page."""
    if not ret_url:
        return DEFAULT_RET_URL
    if not ret_url.startswith("/") or ret_url.startswith("//") or "\\" in ret_url:
        raise HTTPException(400, "ret_url must be a path inside the Salesforce org")
    return ret_url


async def _resolve_run_user(run_id: str | None, authorization: str | None) -> str:
    """Validate the run's bearer token (401 otherwise) and return the owning
    user_id. FrontDoor hands out a logged-in session, so it never falls back
    to a default user."""
    from app.services.run_auth import authenticate_run

    run = await authenticate_run(run_id, authorization)
    automation = await get_repository().get_automation(run.automation_id)
    if automation is None or not automation.user_id:
        raise HTTPException(401, "run has no owning automation/user")
    return automation.user_id


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


@router.post("/{provider}", response_model=FrontDoorResponse)
async def frontdoor(
    provider: str,
    body: FrontDoorRequest,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """Mint a one-time Salesforce login URL for the sandbox browser.
    Requires the run's bearer token; the access token never leaves the backend."""
    if provider.lower() != "salesforce":
        raise HTTPException(404, f"frontdoor not supported for provider {provider!r}")
    ret_url = _safe_ret_url(body.ret_url)
    user_id = await _resolve_run_user(body.run_id, authorization)
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
    return FrontDoorResponse(url=result.frontdoor_url)
