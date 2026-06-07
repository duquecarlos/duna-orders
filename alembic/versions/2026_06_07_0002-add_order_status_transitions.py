"""add order status transitions

Revision ID: d2f7b8a4c901
Revises: b7f4c8e2a901
Create Date: 2026-06-07 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d2f7b8a4c901"
down_revision: str | None = "b7f4c8e2a901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_status_transitions",
        sa.Column("transition_id", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("order_id", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.order_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("transition_id"),
    )
    op.create_index(
        "ix_order_status_transitions_tenant_id_order_id",
        "order_status_transitions",
        ["tenant_id", "order_id"],
        unique=False,
    )
    op.create_index(
        "ix_order_status_transitions_tenant_id_occurred_at",
        "order_status_transitions",
        ["tenant_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_order_status_transitions_tenant_id_occurred_at",
        table_name="order_status_transitions",
    )
    op.drop_index(
        "ix_order_status_transitions_tenant_id_order_id",
        table_name="order_status_transitions",
    )
    op.drop_table("order_status_transitions")