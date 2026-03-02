"""Add semantic duplicate detection fields to receipts

Revision ID: 0006_semantic_duplicate_detection
Revises: 0005_receipt_twins_and_extraction_attempt_metadata
Create Date: 2026-03-01 21:18:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_semantic_duplicate_detection"
down_revision = "0005_receipt_twins_and_extraction_attempt_metadata"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _foreign_key_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {fk["name"] for fk in inspector.get_foreign_keys(table_name) if fk.get("name")}


def upgrade() -> None:
    existing_columns = _column_names("receipts")
    for name, column_type in (
        ("semantic_payee_key", sa.String(length=255)),
        ("semantic_total_cents", sa.Integer()),
        ("semantic_transaction_date", sa.Date()),
        ("semantic_transaction_time", sa.String(length=5)),
        ("semantic_signature", sa.String(length=64)),
        ("duplicate_of_receipt_id", sa.String(length=36)),
        ("duplicate_override_signature", sa.String(length=64)),
    ):
        if name not in existing_columns:
            op.add_column("receipts", sa.Column(name, column_type, nullable=True))

    existing_indexes = _index_names("receipts")
    for index_name, column_name in (
        ("ix_receipts_semantic_payee_key", "semantic_payee_key"),
        ("ix_receipts_semantic_total_cents", "semantic_total_cents"),
        ("ix_receipts_semantic_transaction_date", "semantic_transaction_date"),
        ("ix_receipts_semantic_transaction_time", "semantic_transaction_time"),
        ("ix_receipts_semantic_signature", "semantic_signature"),
        ("ix_receipts_duplicate_of_receipt_id", "duplicate_of_receipt_id"),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, "receipts", [column_name], unique=False)

    # SQLite cannot ALTER TABLE to add a new FK constraint in-place.
    bind = op.get_bind()
    fk_name = "fk_receipts_duplicate_of_receipt_id_receipts"
    if bind.dialect.name != "sqlite" and fk_name not in _foreign_key_names("receipts"):
        op.create_foreign_key(
            fk_name,
            "receipts",
            "receipts",
            ["duplicate_of_receipt_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    fk_name = "fk_receipts_duplicate_of_receipt_id_receipts"
    if bind.dialect.name != "sqlite" and fk_name in _foreign_key_names("receipts"):
        op.drop_constraint(fk_name, "receipts", type_="foreignkey")

    existing_indexes = _index_names("receipts")
    for index_name in (
        "ix_receipts_duplicate_of_receipt_id",
        "ix_receipts_semantic_signature",
        "ix_receipts_semantic_transaction_time",
        "ix_receipts_semantic_transaction_date",
        "ix_receipts_semantic_total_cents",
        "ix_receipts_semantic_payee_key",
    ):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="receipts")

    existing_columns = _column_names("receipts")
    for column_name in (
        "duplicate_override_signature",
        "duplicate_of_receipt_id",
        "semantic_signature",
        "semantic_transaction_time",
        "semantic_transaction_date",
        "semantic_total_cents",
        "semantic_payee_key",
    ):
        if column_name in existing_columns:
            op.drop_column("receipts", column_name)
