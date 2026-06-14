"""Add user_name to game_settings (admin-configurable display name)

Revision ID: 0015_game_settings_user_name
Revises: 0014_game_settings
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_game_settings_user_name"
down_revision = "0014_game_settings"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "game_settings" in _table_names() and "user_name" not in _column_names("game_settings"):
        op.add_column("game_settings", sa.Column("user_name", sa.String(length=100), nullable=True))


def downgrade() -> None:
    if "game_settings" in _table_names() and "user_name" in _column_names("game_settings"):
        op.drop_column("game_settings", "user_name")
