"""
Auth service — registration, login, and server-side sessions (D1 + D2).

Sessions are server-side and revocable (chosen over JWT so a leaked or
compromised session can be killed immediately — important when the product
holds users' Salesforce credentials). The client receives only an opaque
random token in an httpOnly cookie; the DB stores only its SHA-256 hash, so
a leaked database does not yield usable sessions.

The store is abstracted (UserStore / SessionStore protocols) with an
in-memory implementation for tests; a SQL implementation mirrors it in
production (same pattern as the memory stores).
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Protocol

from app.schemas.auth import Session, User, UserStatus
from app.services.auth.passwords import hash_password, verify_password


SESSION_TTL = timedelta(days=14)


class AuthError(Exception):
    pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- store protocols ----

class UserStore(Protocol):
    async def get_by_email(self, email: str) -> tuple[User, str] | None: ...   # (user, password_hash)
    async def get_by_id(self, user_id: str) -> User | None: ...
    async def create(self, user: User, password_hash: str) -> None: ...


class SessionStore(Protocol):
    async def add(self, session: Session) -> None: ...
    async def get_by_token_hash(self, token_hash: str) -> Session | None: ...
    async def revoke(self, session_id: str) -> None: ...


# ---- in-memory impls (tests / dev) ----

class InMemoryUserStore:
    def __init__(self) -> None:
        self._by_email: dict[str, tuple[User, str]] = {}
        self._by_id: dict[str, User] = {}

    async def get_by_email(self, email: str):
        return self._by_email.get(email.lower())

    async def get_by_id(self, user_id: str):
        return self._by_id.get(user_id)

    async def create(self, user: User, password_hash: str) -> None:
        self._by_email[user.email.lower()] = (user, password_hash)
        self._by_id[user.id] = user


class InMemorySessionStore:
    def __init__(self) -> None:
        self._by_hash: dict[str, Session] = {}

    async def add(self, session: Session) -> None:
        self._by_hash[session.token_hash] = session

    async def get_by_token_hash(self, token_hash: str):
        return self._by_hash.get(token_hash)

    async def revoke(self, session_id: str) -> None:
        for s in self._by_hash.values():
            if s.id == session_id:
                s.revoked = True


# ---- operations ----

async def register(users: UserStore, *, email: str, password: str,
                   display_name: str = "") -> User:
    if await users.get_by_email(email):
        raise AuthError("email already registered")
    user = User(id=f"user_{uuid.uuid4().hex[:12]}", email=email,
                display_name=display_name, status=UserStatus.ACTIVE)
    await users.create(user, hash_password(password))
    return user


async def login(users: UserStore, sessions: SessionStore, *,
                email: str, password: str) -> tuple[User, str]:
    """Returns (user, raw_session_token). The raw token goes in the cookie;
    only its hash is stored. Uniform failure message — never reveal whether
    it was the email or the password that was wrong."""
    rec = await users.get_by_email(email)
    if rec is None:
        # Still run a hash to reduce timing oracle on user existence.
        verify_password(password, "pbkdf2$1$AAAA$AAAA")
        raise AuthError("invalid email or password")
    user, pw_hash = rec
    if user.status != UserStatus.ACTIVE:
        raise AuthError("account is not active")
    if not verify_password(password, pw_hash):
        raise AuthError("invalid email or password")

    raw = secrets.token_urlsafe(32)
    session = Session(
        id=f"sess_{uuid.uuid4().hex[:12]}", user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.utcnow() + SESSION_TTL,
    )
    await sessions.add(session)
    return user, raw


async def resolve_session(users: UserStore, sessions: SessionStore,
                          raw_token: str) -> User | None:
    """Validate a session cookie token -> the User, or None if invalid /
    expired / revoked."""
    if not raw_token:
        return None
    s = await sessions.get_by_token_hash(_hash_token(raw_token))
    if s is None or s.revoked:
        return None
    if s.expires_at < datetime.utcnow():
        return None
    return await users.get_by_id(s.user_id)


async def logout(sessions: SessionStore, raw_token: str) -> None:
    s = await sessions.get_by_token_hash(_hash_token(raw_token))
    if s is not None:
        await sessions.revoke(s.id)