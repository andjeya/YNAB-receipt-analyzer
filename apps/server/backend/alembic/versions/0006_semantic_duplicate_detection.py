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


def upgrade() -> None:
    op.add_column("receipts", sa.Column("semantic_payee_key", sa.String(length=255), nullable=True))
    op.add_column("receipts", sa.Column("semantic_total_cents", sa.Integer(), nullable=True))
    op.add_column("receipts", sa.Column("semantic_transaction_date", sa.Date(), nullable=True))
    op.add_column("receipts", sa.Column("semantic_transaction_time", sa.String(length=5), nullable=True))
    op.add_column("receipts", sa.Column("semantic_signature", sa.String(length=64), nullable=True))
    op.add_column("receipts", sa.Column("duplicate_of_receipt_id", sa.String(length=36), nullable=True))
    op.add_column("receipts", sa.Column("duplicate_override_signature", sa.String(length=64), nullable=True))

    op.create_index("ix_receipts_semantic_payee_key", "receipts", ["semantic_payee_key"], unique=False)
    op.create_index("ix_receipts_semantic_total_cents", "receipts", ["semantic_total_cents"], unique=False)
    op.create_index("ix_receipts_semantic_transaction_date", "receipts", ["semantic_transaction_date"], unique=False)
    op.create_index("ix_receipts_semantic_transaction_time", "receipts", ["semantic_transaction_time"], unique=False)
    op.create_index("ix_receipts_semantic_signature", "receipts", ["semantic_signature"], unique=False)
    op.create_index("ix_receipts_duplicate_of_receipt_id", "receipts", ["duplicate_of_receipt_id"], unique=False)
    op.create_foreign_key(
        "fk_receipts_duplicate_of_receipt_id_receipts",
        "receipts",
        "receipts",
        ["duplicate_of_receipt_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_receipts_duplicate_of_receipt_id_receipts", "receipts", type_="foreignkey")
    op.drop_index("ix_receipts_duplicate_of_receipt_id", table_name="receipts")
    op.drop_index("ix_receipts_semantic_signature", table_name="receipts")
    op.drop_index("ix_receipts_semantic_transaction_time", table_name="receipts")
    op.drop_index("ix_receipts_semantic_transaction_date", table_name="receipts")
    op.drop_index("ix_receipts_semantic_total_cents", table_name="receipts")
    op.drop_index("ix_receipts_semantic_payee_key", table_name="receipts")

    op.drop_column("receipts", "duplicate_override_signature")
    op.drop_column("receipts", "duplicate_of_receipt_id")
    op.drop_column("receipts", "semantic_signature")
    op.drop_column("receipts", "semantic_transaction_time")
    op.drop_column("receipts", "semantic_transaction_date")
    op.drop_column("receipts", "semantic_total_cents")
    op.drop_column("receipts", "semantic_payee_key")
