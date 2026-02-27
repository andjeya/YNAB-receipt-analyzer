"""Add receipt twins and extraction attempt metadata

Revision ID: 0005_receipt_twins_and_extraction_attempt_metadata
Revises: 0004_debug_seed_and_incidents
Create Date: 2026-02-27 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_receipt_twins_and_extraction_attempt_metadata"
down_revision = "0004_debug_seed_and_incidents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("latest_twin_version", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "receipt_twins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("confirmed_sections", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_id", "version", name="uq_receipt_twin_receipt_version"),
    )
    op.create_index("ix_receipt_twins_receipt_id", "receipt_twins", ["receipt_id"], unique=False)

    op.add_column("extraction_runs", sa.Column("attempt_kind", sa.String(length=32), nullable=False, server_default="unified"))
    op.add_column(
        "extraction_runs",
        sa.Column("is_primary_result", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("extraction_runs", sa.Column("parent_run_id", sa.Integer(), nullable=True))

    op.create_index("ix_extraction_runs_attempt_kind", "extraction_runs", ["attempt_kind"], unique=False)
    op.create_index("ix_extraction_runs_is_primary_result", "extraction_runs", ["is_primary_result"], unique=False)
    op.create_index("ix_extraction_runs_parent_run_id", "extraction_runs", ["parent_run_id"], unique=False)

    op.execute("UPDATE extraction_runs SET attempt_kind = COALESCE(attempt_kind, 'unified')")
    op.execute("UPDATE extraction_runs SET is_primary_result = 0")
    op.execute(
        """
        UPDATE extraction_runs
        SET is_primary_result = 1
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    er.id AS id,
                    ROW_NUMBER() OVER (PARTITION BY er.receipt_id ORDER BY er.created_at DESC, er.id DESC) AS rownum
                FROM extraction_runs er
            ) ranked
            WHERE ranked.rownum = 1
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_runs_parent_run_id", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_is_primary_result", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_attempt_kind", table_name="extraction_runs")
    op.drop_column("extraction_runs", "parent_run_id")
    op.drop_column("extraction_runs", "is_primary_result")
    op.drop_column("extraction_runs", "attempt_kind")

    op.drop_index("ix_receipt_twins_receipt_id", table_name="receipt_twins")
    op.drop_table("receipt_twins")

    op.drop_column("receipts", "latest_twin_version")
