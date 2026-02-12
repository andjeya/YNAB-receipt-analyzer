"""Gamification core schema

Revision ID: 0002_gamification_init
Revises: 0001_mvp_init
Create Date: 2026-02-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_gamification_init"
down_revision = "0001_mvp_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_receipt_states",
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("age_hours_at_validation", sa.Float(), nullable=False),
        sa.Column("streak_group_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("shredded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("receipt_id"),
    )
    op.create_index("ix_game_receipt_states_state", "game_receipt_states", ["state"], unique=False)
    op.create_index("ix_game_receipt_states_validated_at", "game_receipt_states", ["validated_at"], unique=False)

    op.create_table(
        "game_streaks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_green_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("break_reason", sa.String(length=32), nullable=True),
        sa.Column("active_streak_group_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "game_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("earned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "game_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_game_events_event_type", "game_events", ["event_type"], unique=False)
    op.create_index("ix_game_events_idempotency_key", "game_events", ["idempotency_key"], unique=True)
    op.create_index("ix_game_events_receipt_id", "game_events", ["receipt_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_game_events_receipt_id", table_name="game_events")
    op.drop_index("ix_game_events_idempotency_key", table_name="game_events")
    op.drop_index("ix_game_events_event_type", table_name="game_events")
    op.drop_table("game_events")

    op.drop_table("game_tokens")
    op.drop_table("game_streaks")

    op.drop_index("ix_game_receipt_states_validated_at", table_name="game_receipt_states")
    op.drop_index("ix_game_receipt_states_state", table_name="game_receipt_states")
    op.drop_table("game_receipt_states")
