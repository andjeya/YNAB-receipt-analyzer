"""Add receipts.deleted_at for soft delete + undo

Revision ID: 0010_receipt_soft_delete
Revises: 0009_payee_category_memory
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_receipt_soft_delete"
down_revision = "0009_payee_category_memory"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "deleted_at" not in _column_names("receipts"):
        op.add_column("receipts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    if "ix_receipts_deleted_at" not in _index_names("receipts"):
        op.create_index("ix_receipts_deleted_at", "receipts", ["deleted_at"], unique=False)


def downgrade() -> None:
    if "ix_receipts_deleted_at" in _index_names("receipts"):
        op.drop_index("ix_receipts_deleted_at", table_name="receipts")
    if "deleted_at" in _column_names("receipts"):
        op.drop_column("receipts", "deleted_at")
