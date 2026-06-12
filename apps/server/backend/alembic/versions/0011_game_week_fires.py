"""Add game_week_fires table for week-scoped fire mechanics (Game v3)

Revision ID: 0011_game_week_fires
Revises: 0010_receipt_soft_delete
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_game_week_fires"
down_revision = "0010_receipt_soft_delete"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    existing_tables = _table_names()

    # 1. Create game_week_fires table (idempotent).
    if "game_week_fires" not in existing_tables:
        op.create_table(
            "game_week_fires",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("week_start_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("flames_active", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("burnt", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("last_flame_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("week_start_at", name="uq_game_week_fires_week_start_at"),
        )

    if "game_week_fires" in _table_names():
        existing_indexes = _index_names("game_week_fires")
        if "ix_game_week_fires_week_start_at" not in existing_indexes:
            op.create_index(
                "ix_game_week_fires_week_start_at",
                "game_week_fires",
                ["week_start_at"],
                unique=False,
            )

    # 2. Add current_week_flames to game_debug_seed (idempotent).
    if "game_debug_seed" in existing_tables:
        if "current_week_flames" not in _column_names("game_debug_seed"):
            op.add_column(
                "game_debug_seed",
                sa.Column("current_week_flames", sa.Integer(), nullable=False, server_default="0"),
            )

    # 3. Clamp game_correctness_state.water_units to new cap of 5 (idempotent: safe to run multiple times).
    if "game_correctness_state" in existing_tables:
        op.execute(
            "UPDATE game_correctness_state SET water_units = MIN(water_units, 5) WHERE water_units > 5"
        )


def downgrade() -> None:
    existing_tables = _table_names()

    if "game_week_fires" in existing_tables:
        existing_indexes = _index_names("game_week_fires")
        if "ix_game_week_fires_week_start_at" in existing_indexes:
            op.drop_index("ix_game_week_fires_week_start_at", table_name="game_week_fires")
        op.drop_table("game_week_fires")

    if "game_debug_seed" in existing_tables:
        if "current_week_flames" in _column_names("game_debug_seed"):
            op.drop_column("game_debug_seed", "current_week_flames")
