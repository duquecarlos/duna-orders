"""add deferred inbound

Revision ID: d60b084798e0
Revises: 5eb2de4cca12
Create Date: 2026-06-12 16:36:08.490110

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d60b084798e0"
down_revision: str | None = "5eb2de4cca12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deferred_inbound",
        sa.Column("message_sid", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("customer_key", sa.String(length=80), nullable=False),
        sa.Column("from_number", sa.String(length=80), nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("message_sid", name=op.f("pk_deferred_inbound")),
    )
    op.create_index(
        "ix_deferred_inbound_pending_by_customer",
        "deferred_inbound",
        ["tenant_id", "customer_key", "received_at", "deferred_at", "message_sid"],
        unique=False,
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deferred_inbound_pending_by_customer",
        table_name="deferred_inbound",
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.drop_table("deferred_inbound")
