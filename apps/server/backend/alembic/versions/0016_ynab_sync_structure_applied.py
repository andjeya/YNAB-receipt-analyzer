"""Add structure_applied to ynab_sync (distinguishes structure-ignored updates)

A matched_updated sync whose split/category structure YNAB ignored leaves the
receipt at NEEDS_REVIEW; such rows do not represent the state YNAB holds and must
not be a "Restore synced" source. Existing rows default to True (best-effort
backfill — historical structure-ignored rows are rare and their receipts are
already flagged for manual review).

Revision ID: 0016_ynab_sync_structure_applied
Revises: 0015_game_settings_user_name
Create Date: 2026-06-16 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_ynab_sync_structure_applied"
down_revision = "0015_game_settings_user_name"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "ynab_sync" in _table_names() and "structure_applied" not in _column_names("ynab_sync"):
        op.add_column(
            "ynab_sync",
            sa.Column("structure_applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    if "ynab_sync" in _table_names() and "structure_applied" in _column_names("ynab_sync"):
        op.drop_column("ynab_sync", "structure_applied")
