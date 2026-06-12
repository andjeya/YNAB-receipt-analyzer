"""Add payee_category_memory table for learned payee-to-category mapping

Revision ID: 0009_payee_category_memory
Revises: 0008_card_account_mappings
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_payee_category_memory"
down_revision = "0008_card_account_mappings"
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
    if "payee_category_memory" not in existing_tables:
        op.create_table(
            "payee_category_memory",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("budget_id", sa.String(length=64), nullable=False),
            sa.Column("payee_key", sa.String(length=255), nullable=False),
            sa.Column("category_id", sa.String(length=64), nullable=True),
            sa.Column("template_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("budget_id", "payee_key", name="uq_payee_category_memory_key"),
        )

    existing_indexes = _index_names("payee_category_memory") if "payee_category_memory" in _table_names() else set()
    if "ix_payee_category_memory_budget_id" not in existing_indexes:
        op.create_index("ix_payee_category_memory_budget_id", "payee_category_memory", ["budget_id"], unique=False)


def downgrade() -> None:
    existing_tables = _table_names()
    if "payee_category_memory" in existing_tables:
        existing_indexes = _index_names("payee_category_memory")
        if "ix_payee_category_memory_budget_id" in existing_indexes:
            op.drop_index("ix_payee_category_memory_budget_id", table_name="payee_category_memory")
        op.drop_table("payee_category_memory")
