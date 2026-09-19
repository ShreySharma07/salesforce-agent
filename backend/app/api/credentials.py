"""
Credentials REST API.

  GET    /credentials                    — list credentials for the current user
                                           (returns metadata only, never secrets)
  POST   /credentials/{provider}/api_key — store/replace an API key for a provider
  DELETE /credentials/{provider}         — revoke a credential

OAuth credentials are managed via /oauth/{provider}/connect and /callback —
they're written by the OAuth flow, not by this endpoint.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.base import get_session
from app.schemas.auth import User
from app.services import vault


router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialSummary(BaseModel):
    """What we return to the dashboard. NO secret material."""
    provider: str
    kind: str
    created_at: datetime
    last_used_at: datetime | None = None
    public_metadata: dict = {}


class StoreApiKeyBody(BaseModel):
    api_key: str


@router.get("", response_model=list[CredentialSummary])
async def list_user_credentials(session: AsyncSession = Depends(get_session),
                                user: User = Depends(get_current_user)):
    """List credential metadata (never secrets) for the authenticated user."""
    rows = await vault.list_credentials(session, user_id=user.id)
    return [
        CredentialSummary(
            provider=r.provider,
            kind=r.kind,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            public_metadata=r.public_metadata or {},
        )
        for r in rows
    ]


@router.post("/{provider}/api_key", response_model=CredentialSummary)
async def store_api_key(
    provider: str,
    body: StoreApiKeyBody,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Store an API key for the authenticated user. Replaces any existing
    credential for this (user, provider) pair."""
    if not body.api_key.strip():
        raise HTTPException(400, "api_key must not be empty")
    row = await vault.store_credential(
        session,
        user_id=user.id,
        provider=provider.lower(),
        kind="api_key",
        secret={"api_key": body.api_key.strip()},
    )
    return CredentialSummary(
        provider=row.provider,
        kind=row.kind,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        public_metadata=row.public_metadata or {},
    )


@router.delete("/{provider}")
async def delete_credential(
    provider: str, session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Revoke a credential belonging to the authenticated user."""
    deleted = await vault.delete_credential(
        session, user_id=user.id, provider=provider.lower()
    )
    if not deleted:
        raise HTTPException(404, f"no credential for provider={provider}")
    return {"deleted": True}