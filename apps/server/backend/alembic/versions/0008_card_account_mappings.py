"""Add card_account_mappings table for learned card-to-account mapping

Revision ID: 0008_card_account_mappings
Revises: 0007_validation_allocation_workspace
Create Date: 2026-06-11 16:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_card_account_mappings"
down_revision = "0007_validation_allocation_workspace"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    existing_tables = _table_names()
    if "card_account_mappings" not in existing_tables:
        op.create_table(
            "card_account_mappings",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("budget_id", sa.String(length=64), nullable=False),
            sa.Column("card_last_four", sa.String(length=4), nullable=False),
            sa.Column("account_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("budget_id", "card_last_four", name="uq_card_account_mapping_key"),
        )

    existing_indexes = _index_names("card_account_mappings") if "card_account_mappings" in _table_names() else set()
    if "ix_card_account_mappings_budget_id" not in existing_indexes:
        op.create_index("ix_card_account_mappings_budget_id", "card_account_mappings", ["budget_id"], unique=False)


def downgrade() -> None:
    existing_tables = _table_names()
    if "card_account_mappings" in existing_tables:
        existing_indexes = _index_names("card_account_mappings")
        if "ix_card_account_mappings_budget_id" in existing_indexes:
            op.drop_index("ix_card_account_mappings_budget_id", table_name="card_account_mappings")
        op.drop_table("card_account_mappings")
