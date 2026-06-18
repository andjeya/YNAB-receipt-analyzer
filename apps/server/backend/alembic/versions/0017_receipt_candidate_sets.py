"""Create receipt_candidate_sets (category/split arrangement guesses)

Stores up to three complete, sum-to-total category/split arrangements offered to
the user when a receipt's categorization is uncertain. Versioned sibling of
validations/receipt_twins so the money payload is never widened. Candidates carry
only category/splits + allocation workspace; choosing one merges those onto the
current validation and re-validates.

Revision ID: 0017_receipt_candidate_sets
Revises: 0016_ynab_sync_structure_applied
Create Date: 2026-06-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_receipt_candidate_sets"
down_revision = "0016_ynab_sync_structure_applied"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade() -> None:
    if "receipt_candidate_sets" not in _table_names():
        op.create_table(
            "receipt_candidate_sets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "receipt_id",
                sa.String(length=36),
                sa.ForeignKey("receipts.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("twin_version", sa.Integer(), nullable=True),
            sa.Column("base_validation_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("candidates", sa.JSON(), nullable=False),
            sa.Column("chosen_index", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("receipt_id", "version", name="uq_receipt_candidate_set_receipt_version"),
        )


def downgrade() -> None:
    if "receipt_candidate_sets" in _table_names():
        op.drop_table("receipt_candidate_sets")
