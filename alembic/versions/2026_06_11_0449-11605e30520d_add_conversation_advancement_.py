"""add conversation advancement observability

Revision ID: 11605e30520d
Revises: d6e7f8a9b0c1
Create Date: 2026-06-11 04:49:56.171106

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "11605e30520d"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column("latest_advancement_outcome", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("latest_parse_error_category", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_sessions", "latest_parse_error_category")
    op.drop_column("conversation_sessions", "latest_advancement_outcome")
