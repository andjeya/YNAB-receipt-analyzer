"""Correctness economy and reconciliation schema

Revision ID: 0003_correctness_economy
Revises: 0002_gamification_init
Create Date: 2026-02-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_correctness_economy"
down_revision = "0002_gamification_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_correctness_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("water_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("water_earned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("water_spent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fire_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fire_added_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fire_extinguished_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("burn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_burned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "receipt_corrections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("ynab_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("synced_category_id", sa.String(length=64), nullable=True),
        sa.Column("corrected_category_id", sa.String(length=64), nullable=True),
        sa.Column("synced_splits_json", sa.JSON(), nullable=True),
        sa.Column("corrected_splits_json", sa.JSON(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resynced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resync_penalty_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_receipt_corrections_receipt_id", "receipt_corrections", ["receipt_id"], unique=False)
    op.create_index("ix_receipt_corrections_detected_at", "receipt_corrections", ["detected_at"], unique=False)
    op.create_index("ix_receipt_corrections_expires_at", "receipt_corrections", ["expires_at"], unique=False)

    op.create_table(
        "ynab_reconciliation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("scanned_receipts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detected_mistakes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_penalties", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ynab_reconciliation_runs_started_at", "ynab_reconciliation_runs", ["started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ynab_reconciliation_runs_started_at", table_name="ynab_reconciliation_runs")
    op.drop_table("ynab_reconciliation_runs")

    op.drop_index("ix_receipt_corrections_expires_at", table_name="receipt_corrections")
    op.drop_index("ix_receipt_corrections_detected_at", table_name="receipt_corrections")
    op.drop_index("ix_receipt_corrections_receipt_id", table_name="receipt_corrections")
    op.drop_table("receipt_corrections")

    op.drop_table("game_correctness_state")
