"""
ORM models. The schema of the entire system.

Six tables:
  users             — accounts (single user for now, structure ready for many)
  credentials       — encrypted secrets (OAuth tokens, API keys, passwords)
  oauth_states      — short-lived state tokens for CSRF protection during OAuth flow
  plans             — generated plans (formerly JSON files)
  automations       — saved/named plans
  runs              — execution records

Design choices:
  - Pydantic schemas in app/schemas/*.py remain the API contract.
    ORM models here are the persistence layer. Convert between them at the
    repository boundary. This keeps Pydantic validation strict and decoupled
    from the DB.
  - JSON column for "complex" fields (Plan.steps, Run.step_executions). 
    Cleaner than 10 normalized tables for v1; Postgres has JSONB indexes if 
    needed later.
  - UUIDs as strings (not native UUID type) — works on SQLite and Postgres
    identically, no driver-specific weirdness.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Hashed password for the future login UI. NULL = no password set yet.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    credentials: Mapped[list["Credential"]] = relationship(back_populates="user")
    automations: Mapped[list["Automation"]] = relationship(back_populates="user")


# ---------------------------------------------------------------------------
# Credential vault
# ---------------------------------------------------------------------------

class Credential(Base):
    """A credential is per (user, provider) — Bob's Salesforce, Bob's Gmail,
    Bob's Anthropic API key, each is one row.

    `secret_ciphertext` is Fernet-encrypted bytes. Never stored or logged
    in plaintext. Decryption happens only when the credential is actively
    being passed to a sandbox run or used to refresh tokens.

    For OAuth credentials, `secret_ciphertext` is a JSON blob:
        {"access_token": "...", "refresh_token": "...",
         "expires_at": "2026-05-12T00:00:00Z", "instance_url": "..."}
    For API keys, it's just the raw key wrapped: {"api_key": "..."}.
    """
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

    # E.g. "salesforce", "google", "anthropic", "slack". Free-form lowercase.
    provider: Mapped[str] = mapped_column(String(64), index=True)
    # "oauth" | "api_key" | "username_password"
    kind: Mapped[str] = mapped_column(String(32))

    secret_ciphertext: Mapped[bytes] = mapped_column()
    # Convenience metadata that doesn't need encryption (we still log it):
    #   for oauth: instance_url (Salesforce specifically), scope, expires_at
    #   for api_key: provider hint (e.g. "gemini" model name)
    public_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="credentials")


# ---------------------------------------------------------------------------
# OAuth flow state (CSRF + redirect roundtrip data)
# ---------------------------------------------------------------------------

class OAuthState(Base):
    """Short-lived state tokens for the OAuth Authorization Code flow.

    When user clicks "Connect Salesforce":
      - we generate a random `state` token
      - store it here with user_id, provider, and any return URL we want
        the user to land on after callback completes
      - redirect to Salesforce with state in the URL
    When Salesforce calls back to /oauth/{provider}/callback?code=...&state=...:
      - we look up this row by state
      - validates the request came from us
      - delete the row (single-use)
    Rows expire 10 minutes after creation. A periodic sweep removes stale ones.
    """
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    # PKCE: stored verifier; the challenge was sent to the provider
    pkce_verifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    redirect_uri: Mapped[str] = mapped_column(String(512))
    return_to: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# Plans / Automations / Runs (formerly JSON files)
# ---------------------------------------------------------------------------

class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # The Pydantic plan, serialized as JSON. We rebuild the Pydantic
    # object on read; we never query inside this column.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_video_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Automation(Base):
    __tablename__ = "automations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)  # full Pydantic Automation
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    successful_runs: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="automations")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    triggered_by: Mapped[str] = mapped_column(String(32))
    triggered_by_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sandbox_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    live_view_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    mcp_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Full Pydantic Run, serialized. Includes step_executions, cost, etc.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
 
class Session(Base):
    """Server-side auth session. Only the SHA-256 hash of the session token
    is stored; the raw token lives only in the user's httpOnly cookie."""
    __tablename__ = "sessions"
    
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)