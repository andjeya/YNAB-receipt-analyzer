"""Add allocation workspace persistence to validations

Revision ID: 0007_validation_allocation_workspace
Revises: 0006_semantic_duplicate_detection
Create Date: 2026-03-03 01:55:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_validation_allocation_workspace"
down_revision = "0006_semantic_duplicate_detection"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "allocation_workspace" not in _column_names("validations"):
        op.add_column("validations", sa.Column("allocation_workspace", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "allocation_workspace" in _column_names("validations"):
        op.drop_column("validations", "allocation_workspace")
