"""auth: sessions table, runs.user_id, seed default user

Revision ID: 0004_auth
Revises: 0003_memory_tables
Create Date: 2026-06-14

- Creates the `sessions` table (server-side auth sessions).
- Adds `user_id` to `runs` (was missing; scoped repo stamps it).
- Seeds a User row whose id == settings.default_user_id so all existing
  plans/automations/runs keep a valid owner (D5). Backfills existing plans
  and automations that have a NULL user_id to the default user.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_auth"
down_revision = "0003_memory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. sessions table
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("uq_sessions_token_hash", "sessions", ["token_hash"], unique=True)

    # 2. runs.user_id
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("user_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_runs_user_id", ["user_id"])

    # 3. Seed the default user + backfill ownership.
    conn = op.get_bind()
    # Pull the default user id from settings so it matches the app.
    try:
        from app.config import get_settings
        default_uid = get_settings().default_user_id
    except Exception:
        default_uid = "user_local"

    existing = conn.execute(
        sa.text("SELECT id FROM users WHERE id = :id"), {"id": default_uid}
    ).first()
    if existing is None:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, name, is_active, created_at) "
                "VALUES (:id, :email, :name, 1, CURRENT_TIMESTAMP)"
            ),
            {"id": default_uid, "email": "dev@local", "name": "Default User"},
        )

    # Backfill NULL owners on plans / automations / runs.
    conn.execute(sa.text("UPDATE plans SET user_id = :id WHERE user_id IS NULL"),
                 {"id": default_uid})
    conn.execute(sa.text("UPDATE automations SET user_id = :id WHERE user_id IS NULL"),
                 {"id": default_uid})
    # Runs: attribute existing runs to the owner of their automation.
    conn.execute(sa.text(
        "UPDATE runs SET user_id = ("
        "  SELECT a.user_id FROM automations a WHERE a.id = runs.automation_id"
        ") WHERE user_id IS NULL"
    ))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_index("ix_runs_user_id")
        batch.drop_column("user_id")
    op.drop_index("uq_sessions_token_hash", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")