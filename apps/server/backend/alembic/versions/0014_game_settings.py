"""Create game_settings (admin config) and migrate behavioral params off game_debug_seed

Revision ID: 0014_game_settings
Revises: 0013_timeliness_thresholds
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_game_settings"
down_revision = "0013_timeliness_thresholds"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "game_settings" not in _table_names():
        op.create_table(
            "game_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("green_hours_threshold", sa.Float(), nullable=False, server_default="24.0"),
            sa.Column("brown_hours_threshold", sa.Float(), nullable=False, server_default="72.0"),
            sa.Column("shred_window_weeks", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    bind = op.get_bind()

    # Seed the singleton row, carrying over any values an admin had already set on
    # the (now-deprecated) game_debug_seed columns so behavior is preserved.
    if not bind.execute(sa.text("SELECT 1 FROM game_settings WHERE id = 1")).first():
        green, brown, shred = 24.0, 72.0, 2
        if "game_debug_seed" in _table_names():
            cols = _column_names("game_debug_seed")
            selectable = []
            if "green_hours_threshold" in cols:
                selectable.append("green_hours_threshold")
            if "brown_hours_threshold" in cols:
                selectable.append("brown_hours_threshold")
            if "shred_window_weeks" in cols:
                selectable.append("shred_window_weeks")
            if selectable:
                row = bind.execute(
                    sa.text(f"SELECT {', '.join(selectable)} FROM game_debug_seed WHERE id = 1")
                ).mappings().first()
                if row is not None:
                    green = row.get("green_hours_threshold", green)
                    brown = row.get("brown_hours_threshold", brown)
                    shred = row.get("shred_window_weeks", shred)
        bind.execute(
            sa.text(
                "INSERT INTO game_settings (id, green_hours_threshold, brown_hours_threshold, shred_window_weeks)"
                " VALUES (1, :green, :brown, :shred)"
            ),
            {"green": green, "brown": brown, "shred": shred},
        )

    # The legacy columns on game_debug_seed are intentionally left in place
    # (deprecated, no longer mapped or read) to avoid a SQLite table rebuild.


def downgrade() -> None:
    if "game_settings" in _table_names():
        op.drop_table("game_settings")
