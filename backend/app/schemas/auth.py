"""
Auth schemas — User identity and Session.

Multi-tenant model: each User is an island. Their plans, automations, runs,
credentials, and memory are theirs alone; the repository scoping guard
(services/scoping.py) enforces that no query crosses tenants.

Passwords are never stored in plaintext — only a salted hash (see
services/auth/passwords.py). The hash lives on the User row; this Pydantic
schema is the API contract and deliberately omits it from serialization.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class UserStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class User(BaseModel):
    id: str
    email: EmailStr
    display_name: str = ""
    status: UserStatus = UserStatus.ACTIVE
    organization_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # NOTE: password_hash is intentionally NOT on this schema. It lives only
    # on the ORM row and is handled by the auth service, never serialized to
    # API responses.


class Session(BaseModel):
    """A server-side session. The raw session token is sent to the client in
    an httpOnly cookie; only its SHA-256 hash is stored — same pattern as the
    per-run token, so a leaked DB never yields a usable session."""
    id: str
    user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    revoked: bool = False


# ---- API request/response bodies ----

class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = ""


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class UserPublic(BaseModel):
    """What /auth/me returns — never includes hash or session internals."""
    id: str
    email: EmailStr
    display_name: str = ""
    organization_id: str | None = None