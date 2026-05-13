"""
Auto-refresh expired OAuth tokens.

The pattern any caller uses:

    secret = await get_valid_oauth_credential(session, user_id="user_local",
                                              provider="salesforce")
    # secret["access_token"] is guaranteed non-expired
    # (refreshed transparently if it was about to expire)

Why a buffer: we refresh 60s before expiry so a request that takes 30s to
execute doesn't have its token expire mid-call.
"""
from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.oauth.base import refresh_access_token
from app.services.oauth.providers import get_provider, get_provider_credentials
from app.services.vault import (
    CredentialNotFound,
    VaultError,
    decrypt_payload,
    encrypt_payload,
    get_credential_row,
)


REFRESH_BUFFER_SECONDS = 60


async def get_valid_oauth_credential(
    session: AsyncSession,
    *,
    user_id: str,
    provider: str,
) -> dict:
    """Returns the decrypted credential dict, refreshing the access_token
    if it's expired or near expiry. Raises CredentialNotFound if no
    credential exists for (user, provider)."""
    row = await get_credential_row(session, user_id=user_id, provider=provider)
    if row is None:
        raise CredentialNotFound(f"no oauth credential for {user_id}/{provider}")
    if row.kind != "oauth":
        raise VaultError(
            f"credential for {user_id}/{provider} is kind={row.kind!r}, "
            "not 'oauth'. Refresh only applies to OAuth credentials."
        )

    secret = decrypt_payload(row.secret_ciphertext)
    expires_at = secret.get("expires_at")

    if expires_at is None:
        # Never-expiring token (rare). Return as-is.
        return secret

    if time.time() < expires_at - REFRESH_BUFFER_SECONDS:
        # Still fresh enough.
        return secret

    # Time to refresh.
    refresh_tok = secret.get("refresh_token")
    if not refresh_tok:
        raise VaultError(
            f"{provider} access_token expired but no refresh_token stored. "
            "User needs to re-connect."
        )

    provider_cfg = get_provider(provider)
    client_id, client_secret = get_provider_credentials(provider)
    if not client_id or not client_secret:
        raise VaultError(
            f"{provider} client_id/secret not configured in backend .env"
        )

    new_tokens = await refresh_access_token(
        provider_cfg,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_tok,
    )

    # Update the stored credential. Note: some providers (Google) won't issue
    # a new refresh_token; we keep the old one. Salesforce sometimes rotates.
    updated = secret.copy()
    updated["access_token"] = new_tokens.access_token
    updated["refresh_token"] = new_tokens.refresh_token or refresh_tok
    updated["expires_at"] = new_tokens.expires_at_ts()
    updated["token_type"] = new_tokens.token_type
    if new_tokens.scope:
        updated["scope"] = new_tokens.scope

    row.secret_ciphertext = encrypt_payload(updated)
    await session.flush()
    return updated