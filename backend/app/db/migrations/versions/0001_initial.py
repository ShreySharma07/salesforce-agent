"""initial schema - users, credentials, oauth_states, plans, automations, runs

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-12 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "credentials",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("public_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_credentials_user_id", "credentials", ["user_id"])
    op.create_index("ix_credentials_provider", "credentials", ["provider"])
    op.create_unique_constraint(
        "uq_credentials_user_provider", "credentials", ["user_id", "provider"]
    )

    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("pkce_verifier", sa.String(128), nullable=True),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("return_to", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_oauth_states_user_id", "oauth_states", ["user_id"])

    op.create_table(
        "plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_video_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_plans_user_id", "plans", ["user_id"])
    op.create_index("ix_plans_status", "plans", ["status"])

    op.create_table(
        "automations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_automations_user_id", "automations", ["user_id"])
    op.create_index("ix_automations_status", "automations", ["status"])

    op.create_table(
        "runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("automation_id", sa.String(64), sa.ForeignKey("automations.id"), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("triggered_by", sa.String(32), nullable=False),
        sa.Column("triggered_by_user", sa.String(64), nullable=True),
        sa.Column("sandbox_id", sa.String(64), nullable=True),
        sa.Column("live_view_url", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_runs_automation_id", "runs", ["automation_id"])
    op.create_index("ix_runs_status", "runs", ["status"])


def downgrade() -> None:
    op.drop_table("runs")
    op.drop_table("automations")
    op.drop_table("plans")
    op.drop_table("oauth_states")
    op.drop_table("credentials")
    op.drop_table("users")