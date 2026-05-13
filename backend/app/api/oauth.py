"""
OAuth REST API.

  GET  /oauth/providers                  — list configured providers + connection status
  GET  /oauth/{provider}/connect         — start the flow; redirects to provider login
  GET  /oauth/{provider}/callback        — provider redirects back here with code
  POST /oauth/{provider}/disconnect      — revoke the stored credential

Configuration prerequisites for each provider:
  1. Register an OAuth app in the provider's developer console
  2. Set the redirect URI to: {PUBLIC_BACKEND_BASE_URL}/oauth/{provider}/callback
  3. Put client_id + client_secret in backend/.env (e.g. SALESFORCE_CLIENT_ID=...)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import get_session
from app.db.models import Credential, OAuthState
from app.services import vault
from app.services.oauth.base import build_authorize_url, exchange_code_for_tokens
from app.services.oauth.providers import (
    get_provider,
    get_provider_credentials,
    list_provider_names,
)


router = APIRouter(prefix="/oauth", tags=["oauth"])


class ProviderInfo(BaseModel):
    name: str
    configured: bool                 # client_id + secret are set
    connected: bool                  # current user has stored credentials
    instance_url: str | None = None  # for Salesforce, the org URL
    scope: str | None = None


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers(session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    user_id = settings.default_user_id
    out: list[ProviderInfo] = []
    for name in list_provider_names():
        client_id, client_secret = get_provider_credentials(name)
        configured = bool(client_id and client_secret)

        cred = await vault.get_credential_row(session, user_id=user_id, provider=name)
        connected = cred is not None and cred.kind == "oauth"

        pm = (cred.public_metadata or {}) if cred else {}
        out.append(ProviderInfo(
            name=name,
            configured=configured,
            connected=connected,
            instance_url=pm.get("instance_url"),
            scope=pm.get("scope"),
        ))
    return out


@router.get("/{provider}/connect")
async def connect(
    provider: str,
    return_to: str | None = Query(None, description="Where to redirect after OAuth completes"),
    session: AsyncSession = Depends(get_session),
):
    """Start OAuth flow. Returns 302 redirect to the provider's login page."""
    settings = get_settings()
    user_id = settings.default_user_id

    try:
        provider_cfg = get_provider(provider)
    except KeyError:
        raise HTTPException(404, f"unknown provider {provider!r}")

    client_id, client_secret = get_provider_credentials(provider)
    if not client_id or not client_secret:
        raise HTTPException(
            400,
            f"{provider} OAuth not configured. Set {provider.upper()}_CLIENT_ID and "
            f"{provider.upper()}_CLIENT_SECRET in backend/.env first."
        )

    state = vault.random_state_token()
    pkce_verifier = vault.random_pkce_verifier() if provider_cfg.use_pkce else None
    redirect_uri = settings.oauth_redirect_uri(provider)

    # Persist state row for callback validation
    row = OAuthState(
        state=state,
        user_id=user_id,
        provider=provider,
        pkce_verifier=pkce_verifier,
        redirect_uri=redirect_uri,
        return_to=return_to,
    )
    session.add(row)
    await session.flush()

    url = build_authorize_url(
        provider_cfg,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        pkce_verifier=pkce_verifier,
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/{provider}/callback", response_class=HTMLResponse)
async def callback(
    provider: str,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """OAuth provider redirects user here after auth. Exchange code for tokens
    and store as a Credential."""
    settings = get_settings()
    if error:
        return _result_page(
            f"{provider} authorization failed",
            f"{error}: {error_description or '(no description)'}",
            ok=False,
        )
    if not code or not state:
        raise HTTPException(400, "missing code or state in callback")

    # Look up and consume state row
    result = await session.execute(select(OAuthState).where(OAuthState.state == state))
    state_row = result.scalar_one_or_none()
    if state_row is None:
        raise HTTPException(400, "invalid or expired state token")
    if state_row.provider != provider:
        raise HTTPException(400, "provider mismatch on state token")

    # Sweep expired state rows opportunistically
    await session.execute(
        delete(OAuthState).where(OAuthState.created_at < datetime.utcnow() - timedelta(minutes=10))
    )

    provider_cfg = get_provider(provider)
    client_id, client_secret = get_provider_credentials(provider)
    if not client_id or not client_secret:
        raise HTTPException(500, f"{provider} credentials no longer configured")

    try:
        tokens = await exchange_code_for_tokens(
            provider_cfg,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=state_row.redirect_uri,
            code=code,
            pkce_verifier=state_row.pkce_verifier,
        )
    except Exception as e:
        return _result_page(
            f"{provider} token exchange failed",
            str(e),
            ok=False,
        )

    # Store credential
    await vault.store_credential(
        session,
        user_id=state_row.user_id,
        provider=provider,
        kind="oauth",
        secret=tokens.to_secret_blob(provider_cfg),
        public_metadata=tokens.to_public_metadata(provider_cfg),
    )

    # Delete state row (single-use)
    await session.delete(state_row)
    await session.flush()

    if state_row.return_to:
        return RedirectResponse(url=state_row.return_to, status_code=302)
    return _result_page(
        f"{provider.capitalize()} connected",
        f"You can close this tab. The agent platform now has access to {provider}.",
        ok=True,
    )


@router.post("/{provider}/disconnect")
async def disconnect(provider: str, session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    deleted = await vault.delete_credential(
        session, user_id=settings.default_user_id, provider=provider
    )
    if not deleted:
        raise HTTPException(404, f"no credential for provider={provider}")
    return {"disconnected": True}


# ---------------------------------------------------------------------------
# Tiny HTML response for the callback landing page (no frontend yet)
# ---------------------------------------------------------------------------

def _result_page(title: str, msg: str, *, ok: bool) -> HTMLResponse:
    color = "#22c55e" if ok else "#ef4444"
    icon = "✓" if ok else "✗"
    html = f"""<!doctype html>
<html><head><title>{title}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         justify-content: center; height: 100vh; margin: 0; background: #0f172a; color: #f1f5f9; }}
  .icon {{ font-size: 64px; color: {color}; }}
  h1 {{ font-weight: 500; }}
  p {{ color: #94a3b8; max-width: 480px; text-align: center; }}
</style></head>
<body>
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{msg}</p>
</body></html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)