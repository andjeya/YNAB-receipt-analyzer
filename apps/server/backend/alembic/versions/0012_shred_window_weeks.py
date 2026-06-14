"""Add shred_window_weeks setting to game_debug_seed

Revision ID: 0012_shred_window_weeks
Revises: 0011_game_week_fires
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_shred_window_weeks"
down_revision = "0011_game_week_fires"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    # Add shred_window_weeks to game_debug_seed (idempotent). Default 2 so the
    # existing single row backfills to a two-week shred window.
    if "game_debug_seed" in _table_names():
        if "shred_window_weeks" not in _column_names("game_debug_seed"):
            op.add_column(
                "game_debug_seed",
                sa.Column("shred_window_weeks", sa.Integer(), nullable=False, server_default="2"),
            )


def downgrade() -> None:
    if "game_debug_seed" in _table_names():
        if "shred_window_weeks" in _column_names("game_debug_seed"):
            op.drop_column("game_debug_seed", "shred_window_weeks")
