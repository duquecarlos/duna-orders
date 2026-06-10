"""add conversation draft links

Revision ID: d6e7f8a9b0c1
Revises: c5d8e9f0a1b2
Create Date: 2026-06-10 00:04:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c5d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("conversation_id", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "uq_orders_conversation_id_not_null",
        "orders",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("conversation_id IS NOT NULL"),
        sqlite_where=sa.text("conversation_id IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_tenant_id_conversation_id",
        "orders",
        ["tenant_id", "conversation_id"],
        unique=False,
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("resulting_order_id", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_sessions", "resulting_order_id")
    op.drop_index("ix_orders_tenant_id_conversation_id", table_name="orders")
    op.drop_index(
        "uq_orders_conversation_id_not_null",
        table_name="orders",
        postgresql_where=sa.text("conversation_id IS NOT NULL"),
        sqlite_where=sa.text("conversation_id IS NOT NULL"),
    )
    op.drop_column("orders", "conversation_id")
