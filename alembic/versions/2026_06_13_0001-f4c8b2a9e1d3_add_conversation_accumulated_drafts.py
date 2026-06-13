"""add conversation accumulated drafts

Revision ID: f4c8b2a9e1d3
Revises: d60b084798e0
Create Date: 2026-06-13 00:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f4c8b2a9e1d3"
down_revision: str | None = "d60b084798e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_accumulated_drafts",
        sa.Column("conversation_id", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("accumulated_json", sa.Text(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation_sessions.conversation_id"],
            name=op.f(
                "fk_conversation_accumulated_drafts_conversation_id_conversation_sessions"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "conversation_id",
            name=op.f("pk_conversation_accumulated_drafts"),
        ),
    )
    op.create_index(
        "ix_conversation_accumulated_drafts_tenant_id_conversation_id",
        "conversation_accumulated_drafts",
        ["tenant_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_accumulated_drafts_tenant_id_conversation_id",
        table_name="conversation_accumulated_drafts",
    )
    op.drop_table("conversation_accumulated_drafts")
