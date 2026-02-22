"""Add debug seed and game incident queue tables

Revision ID: 0004_debug_seed_and_incidents
Revises: 0003_correctness_economy
Create Date: 2026-02-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_debug_seed_and_incidents"
down_revision = "0003_correctness_economy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_debug_seed",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("water_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("water_earned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("water_spent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fire_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fire_added_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fire_extinguished_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("burn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_earned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_spent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_streak_group_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("break_reason", sa.String(length=32), nullable=True),
        sa.Column("correctness_event_floor_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_floor_unix_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "game_incidents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("incident_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_game_incidents_incident_type", "game_incidents", ["incident_type"], unique=False)
    op.create_index("ix_game_incidents_severity", "game_incidents", ["severity"], unique=False)
    op.create_index("ix_game_incidents_idempotency_key", "game_incidents", ["idempotency_key"], unique=True)
    op.create_index("ix_game_incidents_acknowledged_at", "game_incidents", ["acknowledged_at"], unique=False)
    op.create_index("ix_game_incidents_created_at", "game_incidents", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_game_incidents_created_at", table_name="game_incidents")
    op.drop_index("ix_game_incidents_acknowledged_at", table_name="game_incidents")
    op.drop_index("ix_game_incidents_idempotency_key", table_name="game_incidents")
    op.drop_index("ix_game_incidents_severity", table_name="game_incidents")
    op.drop_index("ix_game_incidents_incident_type", table_name="game_incidents")
    op.drop_table("game_incidents")
    op.drop_table("game_debug_seed")
