"""add processed messages table

Revision ID: 9c7e1f4a2b30
Revises: aec69eff0019
Create Date: 2026-06-06 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c7e1f4a2b30"
down_revision: Union[str, Sequence[str], None] = "aec69eff0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("message_sid", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_number", sa.String(length=80), nullable=True),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("resulting_order_id", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("message_sid", name=op.f("pk_processed_messages")),
    )
    op.create_index(
        "ix_processed_messages_tenant_id_received_at",
        "processed_messages",
        ["tenant_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_processed_messages_tenant_id_resulting_order_id",
        "processed_messages",
        ["tenant_id", "resulting_order_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processed_messages_tenant_id_resulting_order_id",
        table_name="processed_messages",
    )
    op.drop_index(
        "ix_processed_messages_tenant_id_received_at",
        table_name="processed_messages",
    )
    op.drop_table("processed_messages")