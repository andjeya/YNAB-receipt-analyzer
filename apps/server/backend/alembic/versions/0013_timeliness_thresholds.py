"""Add green/brown timeliness thresholds to game_debug_seed

Revision ID: 0013_timeliness_thresholds
Revises: 0012_shred_window_weeks
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_timeliness_thresholds"
down_revision = "0012_shred_window_weeks"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    # Add the green/brown timeliness thresholds to game_debug_seed (idempotent).
    # Defaults match the historical config defaults so the existing row backfills
    # to the same behavior (green <=24h from purchase, brown >72h).
    if "game_debug_seed" in _table_names():
        cols = _column_names("game_debug_seed")
        if "green_hours_threshold" not in cols:
            op.add_column(
                "game_debug_seed",
                sa.Column("green_hours_threshold", sa.Float(), nullable=False, server_default="24.0"),
            )
        if "brown_hours_threshold" not in cols:
            op.add_column(
                "game_debug_seed",
                sa.Column("brown_hours_threshold", sa.Float(), nullable=False, server_default="72.0"),
            )


def downgrade() -> None:
    if "game_debug_seed" in _table_names():
        cols = _column_names("game_debug_seed")
        if "brown_hours_threshold" in cols:
            op.drop_column("game_debug_seed", "brown_hours_threshold")
        if "green_hours_threshold" in cols:
            op.drop_column("game_debug_seed", "green_hours_threshold")
