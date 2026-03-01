"""Initial MVP schema

Revision ID: 0001_mvp_init
Revises: 
Create Date: 2026-02-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_mvp_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_ext", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("latest_validation_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("display_payee_name", sa.String(length=255), nullable=True),
        sa.Column("display_total_milliunits", sa.Integer(), nullable=True),
        sa.Column("display_receipt_date", sa.Date(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extraction_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_hash"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_receipts_file_hash", "receipts", ["file_hash"], unique=True)
    op.create_index("ix_receipts_status", "receipts", ["status"], unique=False)

    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=False),
        sa.Column("parsed_json", sa.JSON(), nullable=True),
        sa.Column("schema_valid", sa.Boolean(), nullable=False),
        sa.Column("schema_errors", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extraction_runs_receipt_id", "extraction_runs", ["receipt_id"], unique=False)

    op.create_table(
        "validations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_id", "version", name="uq_validation_receipt_version"),
    )
    op.create_index("ix_validations_receipt_id", "validations", ["receipt_id"], unique=False)

    op.create_table(
        "ynab_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("budget_id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("group_name", sa.String(length=255), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("budget_id", "entity_type", "entity_id", name="uq_ynab_cache_entity"),
    )
    op.create_index("ix_ynab_cache_budget_id", "ynab_cache", ["budget_id"], unique=False)
    op.create_index("ix_ynab_cache_entity_type", "ynab_cache", ["entity_type"], unique=False)

    op.create_table(
        "ynab_sync",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("validation_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("match_mode", sa.String(length=32), nullable=False),
        sa.Column("matched_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("created_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("raw_request", sa.JSON(), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["validation_id"], ["validations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_ynab_sync_idempotency_key", "ynab_sync", ["idempotency_key"], unique=True)
    op.create_index("ix_ynab_sync_receipt_id", "ynab_sync", ["receipt_id"], unique=False)
    op.create_index("ix_ynab_sync_status", "ynab_sync", ["status"], unique=False)
    op.create_index("ix_ynab_sync_validation_id", "ynab_sync", ["validation_id"], unique=False)

    op.create_table(
        "timing_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=36), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("metric_value_ms", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_timing_metrics_metric_name", "timing_metrics", ["metric_name"], unique=False)
    op.create_index("ix_timing_metrics_receipt_id", "timing_metrics", ["receipt_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_timing_metrics_receipt_id", table_name="timing_metrics")
    op.drop_index("ix_timing_metrics_metric_name", table_name="timing_metrics")
    op.drop_table("timing_metrics")

    op.drop_index("ix_ynab_sync_validation_id", table_name="ynab_sync")
    op.drop_index("ix_ynab_sync_status", table_name="ynab_sync")
    op.drop_index("ix_ynab_sync_receipt_id", table_name="ynab_sync")
    op.drop_index("ix_ynab_sync_idempotency_key", table_name="ynab_sync")
    op.drop_table("ynab_sync")

    op.drop_index("ix_ynab_cache_entity_type", table_name="ynab_cache")
    op.drop_index("ix_ynab_cache_budget_id", table_name="ynab_cache")
    op.drop_table("ynab_cache")

    op.drop_index("ix_validations_receipt_id", table_name="validations")
    op.drop_table("validations")

    op.drop_index("ix_extraction_runs_receipt_id", table_name="extraction_runs")
    op.drop_table("extraction_runs")

    op.drop_index("ix_receipts_status", table_name="receipts")
    op.drop_index("ix_receipts_file_hash", table_name="receipts")
    op.drop_table("receipts")
