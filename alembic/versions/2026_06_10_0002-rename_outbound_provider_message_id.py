"""rename outbound provider message id

Revision ID: a4b7c9d2e6f1
Revises: f3b2c1d4e5a6
Create Date: 2026-06-10 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a4b7c9d2e6f1"
down_revision: str | None = "f3b2c1d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "outbound_messages",
        "provider_message_sid",
        new_column_name="provider_message_id",
        existing_type=sa.String(length=80),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "outbound_messages",
        "provider_message_id",
        new_column_name="provider_message_sid",
        existing_type=sa.String(length=80),
        existing_nullable=True,
    )
