"""capture raw body on processed messages

Revision ID: b7f4c8e2a901
Revises: 9c7e1f4a2b30
Create Date: 2026-06-07 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b7f4c8e2a901"
down_revision: str | None = "9c7e1f4a2b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processed_messages", sa.Column("raw_body", sa.Text(), nullable=True))
    op.drop_column("processed_messages", "body_preview")


def downgrade() -> None:
    op.add_column("processed_messages", sa.Column("body_preview", sa.Text(), nullable=True))
    op.drop_column("processed_messages", "raw_body")