"""
Generic OAuth 2.0 Authorization Code flow.

Implementing this once means every provider (Salesforce, Google, Slack, etc.)
gets full OAuth for free — just register a new OAuthProvider config.

The flow:
  1. /oauth/{provider}/connect
     - generates state token + (if provider supports PKCE) code_verifier
     - stores in oauth_states table
     - redirects to provider's authorize URL
  2. User authenticates at provider, approves access
  3. Provider redirects to our /oauth/{provider}/callback with code + state
  4. We look up state row, validate, exchange code for tokens
  5. Encrypt tokens, store as a Credential
  6. Delete the oauth_state row (single-use)
  7. Redirect user to wherever they came from
"""
from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

@dataclass
class OAuthProvider:
    """All the bits we need to talk OAuth 2.0 to a specific provider."""

    name: str                              # canonical lowercase: "salesforce", "google"
    authorize_url: str                     # where to send the user
    token_url: str                         # where we exchange code for tokens
    scopes: list[str] = field(default_factory=list)
    # PKCE is mandatory for Google, optional for Salesforce, etc.
    use_pkce: bool = False
    # Some providers expect additional params on the authorize URL
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    # Provider-specific public metadata to extract from token response
    # and store on the Credential row. e.g. Salesforce returns instance_url.
    public_metadata_keys: list[str] = field(default_factory=list)
    # Fallback token lifetime (seconds) for providers that don't return
    # expires_in in the token response (e.g. Salesforce returns issued_at
    # instead). Used when expires_in is absent.
    token_lifetime_seconds: int | None = None


@dataclass
class TokenResponse:
    """Normalized shape of what we got back from token exchange."""
    access_token: str
    refresh_token: str | None
    expires_in: int | None                # seconds; None = doesn't expire
    token_type: str = "Bearer"
    scope: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def expires_at_ts(self, fallback_lifetime: int | None = None) -> int | None:
        """Unix timestamp when the token expires.

        Uses expires_in from the token response, falling back to
        fallback_lifetime (from OAuthProvider.token_lifetime_seconds) for
        providers like Salesforce that return issued_at instead of expires_in.
        Returns None only when both are absent (token treated as permanent).
        """
        lifetime = self.expires_in if self.expires_in is not None else fallback_lifetime
        if lifetime is None:
            return None
        return int(time.time()) + lifetime

    def to_secret_blob(self, provider: OAuthProvider) -> dict[str, Any]:
        """The shape stored in the encrypted credential."""
        blob = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at_ts(provider.token_lifetime_seconds),
            "token_type": self.token_type,
            "scope": self.scope,
        }
        # Preserve provider-specific fields the refresh logic might need
        for key in provider.public_metadata_keys:
            if key in self.raw:
                blob[key] = self.raw[key]
        return blob

    def to_public_metadata(self, provider: OAuthProvider) -> dict[str, Any]:
        """Non-secret data we expose to dashboards (e.g. Salesforce org URL)."""
        meta = {
            key: self.raw[key]
            for key in provider.public_metadata_keys
            if key in self.raw
        }
        if self.scope is not None:
            meta["scope"] = self.scope
        return meta


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Flow primitives
# ---------------------------------------------------------------------------

def build_authorize_url(
    provider: OAuthProvider,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    pkce_verifier: str | None = None,
) -> str:
    """Returns the URL we should redirect the user to."""
    from urllib.parse import urlencode

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if provider.scopes:
        params["scope"] = " ".join(provider.scopes)
    if provider.use_pkce and pkce_verifier:
        params["code_challenge"] = pkce_challenge(pkce_verifier)
        params["code_challenge_method"] = "S256"
    params.update(provider.extra_authorize_params)
    return f"{provider.authorize_url}?{urlencode(params)}"


async def exchange_code_for_tokens(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    pkce_verifier: str | None = None,
) -> TokenResponse:
    """POST to provider's token URL. Returns normalized tokens."""
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    if provider.use_pkce and pkce_verifier:
        data["code_verifier"] = pkce_verifier

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            provider.token_url,
            data=data,
            headers={"Accept": "application/json"},
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"{provider.name} token exchange failed "
                f"({r.status_code}): {r.text[:500]}"
            )
        body = r.json()

    return TokenResponse(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_in=body.get("expires_in"),
        token_type=body.get("token_type", "Bearer"),
        scope=body.get("scope"),
        raw=body,
    )


async def refresh_access_token(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> TokenResponse:
    """Get a new access_token using a saved refresh_token. Provider keeps the
    refresh_token unless explicitly rotated (Salesforce does, Google doesn't)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(provider.token_url, data=data,
                              headers={"Accept": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(
                f"{provider.name} refresh failed ({r.status_code}): {r.text[:500]}"
            )
        body = r.json()
    return TokenResponse(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", refresh_token),
        expires_in=body.get("expires_in"),
        token_type=body.get("token_type", "Bearer"),
        scope=body.get("scope"),
        raw=body,
    )