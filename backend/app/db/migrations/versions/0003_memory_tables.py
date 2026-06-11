"""memory tables: procedures, episodes, lessons

Revision ID: 0003_memory_tables
Revises: 0002_run_mcp_token_hash
Create Date: 2026-06-07

Creates the three persistence tables for the agentic memory system:

  memory_procedures — distilled task recipes (procedural memory)
  memory_episodes   — notable past events (episodic memory)
  memory_lessons    — reflection output, kept for review/audit

Each table stores the full pydantic object as JSON in `payload` alongside
the columns actually used for lookup (user_id + the retrieval key), matching
how the rest of the schema stores rich objects.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_memory_tables"
down_revision = "0002_run_mcp_token_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_procedures",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("task_signature", sa.String(length=512), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_memory_procedures_user_id", "memory_procedures", ["user_id"]
    )
    op.create_index(
        "ix_memory_procedures_task_signature",
        "memory_procedures",
        ["task_signature"],
    )
    # The natural key: one procedure per (user, signature).
    op.create_index(
        "uq_memory_procedures_user_sig",
        "memory_procedures",
        ["user_id", "task_signature"],
        unique=True,
    )

    op.create_table(
        "memory_episodes",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("situation_key", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_memory_episodes_user_id", "memory_episodes", ["user_id"]
    )
    op.create_index(
        "ix_memory_episodes_situation_key",
        "memory_episodes",
        ["situation_key"],
    )
    # The lookup the store actually performs: (user, situation).
    op.create_index(
        "ix_memory_episodes_user_situation",
        "memory_episodes",
        ["user_id", "situation_key"],
    )

    op.create_table(
        "memory_lessons",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_memory_lessons_user_id", "memory_lessons", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_memory_lessons_user_id", table_name="memory_lessons")
    op.drop_table("memory_lessons")

    op.drop_index("ix_memory_episodes_user_situation", table_name="memory_episodes")
    op.drop_index("ix_memory_episodes_situation_key", table_name="memory_episodes")
    op.drop_index("ix_memory_episodes_user_id", table_name="memory_episodes")
    op.drop_table("memory_episodes")

    op.drop_index("uq_memory_procedures_user_sig", table_name="memory_procedures")
    op.drop_index("ix_memory_procedures_task_signature", table_name="memory_procedures")
    op.drop_index("ix_memory_procedures_user_id", table_name="memory_procedures")
    op.drop_table("memory_procedures")