"""Alembic migration smoke tests (plan T1-16, T1-17).

T1-16  A fresh database upgrades cleanly to head: core tables + a stamped
       alembic_version exist.
T1-17  Re-running migrations against an already-migrated, populated database is a
       safe no-op that preserves existing rows (guards "existing DB upgrade to
       head preserves data" without hand-authoring every intermediate schema).

Both run against a throwaway temp sqlite file; `get_settings` is patched so the
real dev database is never touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Receipt

CORE_TABLES = {"receipts", "validations", "ynab_sync", "alembic_version"}


def _temp_settings(db_file: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{db_file}",
        object_store_root="./data",
        ingest_dir="./data/ingest",
    )


def _upgrade_to_head(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    # env.py calls app.config.get_settings() to resolve the URL — patch it.
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    from app.migrations import ensure_schema_current

    ensure_schema_current()


def _head_revision(engine: Any) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("select version_num from alembic_version")).scalar()


def test_fresh_migration_upgrades_to_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "fresh.db"
    settings = _temp_settings(db_file)

    _upgrade_to_head(monkeypatch, settings)

    engine = create_engine(settings.database_url, future=True)
    tables = set(inspect(engine).get_table_names())
    assert CORE_TABLES <= tables, f"missing tables: {CORE_TABLES - tables}"
    assert _head_revision(engine) is not None
    engine.dispose()


def test_rerun_migration_on_populated_db_preserves_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "existing.db"
    settings = _temp_settings(db_file)

    _upgrade_to_head(monkeypatch, settings)

    engine = create_engine(settings.database_url, future=True)
    revision_before = _head_revision(engine)

    # Insert a real row through the ORM on the migrated schema.
    with Session(engine) as session:
        session.add(
            Receipt(
                id="mig-keep-1",
                storage_key="receipts/mig-keep-1.jpg",
                original_filename="keep.jpg",
                file_hash="hash-mig-keep-1",
                file_ext=".jpg",
                mime_type="image/jpeg",
                file_size_bytes=1024,
                status=ReceiptStatus.NEEDS_REVIEW.value,
            )
        )
        session.commit()
    engine.dispose()

    # Re-run migrations against the populated DB — must be a safe no-op.
    _upgrade_to_head(monkeypatch, settings)

    engine = create_engine(settings.database_url, future=True)
    assert _head_revision(engine) == revision_before
    with Session(engine) as session:
        kept = session.get(Receipt, "mig-keep-1")
        assert kept is not None
        assert kept.file_hash == "hash-mig-keep-1"
    engine.dispose()
