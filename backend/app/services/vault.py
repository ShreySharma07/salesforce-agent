"""
Credential vault.

Wraps a Fernet symmetric key + the credentials table.
Every secret is encrypted at rest. Plaintext exists only:
  - In memory during a request that needs the credential
  - In env vars passed to a sandbox container at run time

Lifecycle:
  store(user_id, provider, kind, secret_dict)  → row inserted, ciphertext stored
  get(user_id, provider)                       → ciphertext fetched, decrypted, returned
  delete(user_id, provider)                    → row removed

The encryption key itself comes from env var VAULT_ENCRYPTION_KEY.
NEVER stored in the DB. NEVER logged.
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Credential


class VaultError(Exception):
    """Generic vault error. Specific subclasses below."""


class VaultMisconfigured(VaultError):
    """VAULT_ENCRYPTION_KEY missing or malformed."""


class CredentialNotFound(VaultError):
    pass


# ---------------------------------------------------------------------------
# Encryption primitive
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    """Build a Fernet from the configured key. Fernet itself is stateless;
    safe to construct per-call."""
    settings = get_settings()
    if not settings.vault_encryption_key:
        raise VaultMisconfigured(
            "VAULT_ENCRYPTION_KEY not set. Generate one with:\n"
            "  python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'\n"
            "Then add it to backend/.env"
        )
    try:
        return Fernet(settings.vault_encryption_key.encode())
    except (ValueError, Exception) as e:
        raise VaultMisconfigured(
            f"VAULT_ENCRYPTION_KEY is invalid (must be 32 url-safe base64 bytes): {e}"
        )


def encrypt_payload(payload: dict[str, Any]) -> bytes:
    """Serialize to JSON, encrypt, return bytes safe to store."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _get_fernet().encrypt(raw)


def decrypt_payload(ciphertext: bytes) -> dict[str, Any]:
    """Decrypt and deserialize. Raises VaultError on key mismatch / tampering."""
    try:
        raw = _get_fernet().decrypt(ciphertext)
    except InvalidToken as e:
        raise VaultError(
            "decryption failed — most likely VAULT_ENCRYPTION_KEY changed since "
            "this credential was stored, or the row was tampered with"
        ) from e
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Vault CRUD
# ---------------------------------------------------------------------------

async def store_credential(
    session: AsyncSession,
    *,
    user_id: str,
    provider: str,
    kind: str,
    secret: dict[str, Any],
    public_metadata: dict[str, Any] | None = None,
) -> Credential:
    """Insert a credential. If one already exists for (user, provider),
    overwrite it (upsert semantics)."""
    existing = await get_credential_row(session, user_id=user_id, provider=provider)
    ciphertext = encrypt_payload(secret)
    if existing is not None:
        await session.execute(
            update(Credential)
            .where(Credential.id == existing.id)
            .values(
                kind=kind,
                secret_ciphertext=ciphertext,
                public_metadata=public_metadata or {},
                updated_at=datetime.utcnow(),
            )
        )
        await session.flush()
        await session.refresh(existing)
        return existing

    cred = Credential(
        id=f"cred_{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        provider=provider.lower(),
        kind=kind,
        secret_ciphertext=ciphertext,
        public_metadata=public_metadata or {},
    )
    session.add(cred)
    await session.flush()
    return cred


async def get_credential_row(
    session: AsyncSession, *, user_id: str, provider: str
) -> Credential | None:
    """Returns the raw row (encrypted). For most uses, call get_credential_secret."""
    result = await session.execute(
        select(Credential).where(
            Credential.user_id == user_id,
            Credential.provider == provider.lower(),
        )
    )
    return result.scalar_one_or_none()


async def get_credential_secret(
    session: AsyncSession, *, user_id: str, provider: str
) -> dict[str, Any]:
    """Fetch + decrypt + mark last_used_at. Use this when about to USE
    the credential (e.g. inject into a run, refresh tokens, etc.)."""
    row = await get_credential_row(session, user_id=user_id, provider=provider)
    if row is None:
        raise CredentialNotFound(
            f"no credential for user={user_id!r} provider={provider!r}"
        )
    row.last_used_at = datetime.utcnow()
    await session.flush()
    return decrypt_payload(row.secret_ciphertext)


async def list_credentials(
    session: AsyncSession, *, user_id: str
) -> list[Credential]:
    """Returns rows without decrypting. For listing in a dashboard."""
    result = await session.execute(
        select(Credential).where(Credential.user_id == user_id).order_by(Credential.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_credential(
    session: AsyncSession, *, user_id: str, provider: str
) -> bool:
    row = await get_credential_row(session, user_id=user_id, provider=provider)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# Utility for OAuth state CSRF tokens
# ---------------------------------------------------------------------------

def random_state_token() -> str:
    """Cryptographically secure token for OAuth state parameter."""
    return secrets.token_urlsafe(32)


def random_pkce_verifier() -> str:
    """PKCE code_verifier - 43-128 chars of url-safe base64."""
    return secrets.token_urlsafe(64)[:96]