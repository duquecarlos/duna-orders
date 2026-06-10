"""add conversation state

Revision ID: c5d8e9f0a1b2
Revises: a4b7c9d2e6f1
Create Date: 2026-06-10 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c5d8e9f0a1b2"
down_revision: str | None = "a4b7c9d2e6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("conversation_id", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("customer_phone", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id", name=op.f("pk_conversation_sessions")),
    )
    op.create_index(
        "ix_conversation_sessions_tenant_id_customer_phone",
        "conversation_sessions",
        ["tenant_id", "customer_phone"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_sessions_tenant_id_status",
        "conversation_sessions",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_conversation_sessions_one_open_per_customer",
        "conversation_sessions",
        ["tenant_id", "customer_phone"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "conversation_turns",
        sa.Column("turn_id", sa.String(length=80), nullable=False),
        sa.Column("conversation_id", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("message_sid", sa.String(length=80), nullable=False),
        sa.Column("from_number", sa.String(length=80), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversation_sessions.conversation_id"],
            name=op.f("fk_conversation_turns_conversation_id_conversation_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("turn_id", name=op.f("pk_conversation_turns")),
        sa.UniqueConstraint(
            "tenant_id",
            "message_sid",
            name="uq_conversation_turns_tenant_message_sid",
        ),
    )
    op.create_index(
        "ix_conversation_turns_conversation_sequence",
        "conversation_turns",
        ["conversation_id", "sequence_number"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_turns_tenant_id_conversation_id",
        "conversation_turns",
        ["tenant_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_turns_tenant_id_conversation_id",
        table_name="conversation_turns",
    )
    op.drop_index(
        "ix_conversation_turns_conversation_sequence",
        table_name="conversation_turns",
    )
    op.drop_table("conversation_turns")
    op.drop_index(
        "uq_conversation_sessions_one_open_per_customer",
        table_name="conversation_sessions",
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )
    op.drop_index(
        "ix_conversation_sessions_tenant_id_status",
        table_name="conversation_sessions",
    )
    op.drop_index(
        "ix_conversation_sessions_tenant_id_customer_phone",
        table_name="conversation_sessions",
    )
    op.drop_table("conversation_sessions")
