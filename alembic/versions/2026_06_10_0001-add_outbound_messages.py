"""add outbound messages

Revision ID: f3b2c1d4e5a6
Revises: d2f7b8a4c901
Create Date: 2026-06-10 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f3b2c1d4e5a6"
down_revision: str | None = "d2f7b8a4c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column("outbound_message_id", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("order_id", sa.String(length=80), nullable=False),
        sa.Column("acknowledgement_type", sa.String(length=40), nullable=False),
        sa.Column("to_number", sa.String(length=80), nullable=False),
        sa.Column("from_number", sa.String(length=80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_message_sid", sa.String(length=80), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("outbound_message_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "acknowledgement_type",
            name="uq_outbound_messages_tenant_order_ack_type",
        ),
    )
    op.create_index(
        "ix_outbound_messages_tenant_id_order_id",
        "outbound_messages",
        ["tenant_id", "order_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbound_messages_tenant_id_status",
        "outbound_messages",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_outbound_messages_tenant_id_created_at",
        "outbound_messages",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbound_messages_tenant_id_created_at",
        table_name="outbound_messages",
    )
    op.drop_index(
        "ix_outbound_messages_tenant_id_status",
        table_name="outbound_messages",
    )
    op.drop_index(
        "ix_outbound_messages_tenant_id_order_id",
        table_name="outbound_messages",
    )
    op.drop_table("outbound_messages")
