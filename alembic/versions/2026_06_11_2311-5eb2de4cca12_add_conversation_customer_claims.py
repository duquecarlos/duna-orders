"""add conversation customer claims

Revision ID: 5eb2de4cca12
Revises: 11605e30520d
Create Date: 2026-06-11 23:11:23.851579

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "5eb2de4cca12"
down_revision: str | None = "11605e30520d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_customer_claims",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("customer_key", sa.String(length=80), nullable=False),
        sa.Column("holder_id", sa.String(length=80), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "customer_key", name=op.f("pk_conversation_customer_claims")
        ),
    )


def downgrade() -> None:
    op.drop_table("conversation_customer_claims")
