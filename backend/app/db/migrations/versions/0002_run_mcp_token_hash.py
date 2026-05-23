"""add mcp_token_hash to runs

Revision ID: 0002_run_mcp_token_hash
Revises: 0001_initial
Create Date: 2026-05-22 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_run_mcp_token_hash"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("mcp_token_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "mcp_token_hash")
