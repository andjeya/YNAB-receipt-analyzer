"""Unique-constraint regression tests (plan T1-18).

These constraints are the database-level backstop against duplicate financial
state under races (two scanners, two sync workers, two reconciliation runs).
Each test inserts a conflicting pair and asserts the DB rejects it. A fresh
in-memory session per test avoids operating on a post-IntegrityError session.

Covers: receipts.file_hash, validations(receipt_id, version),
ynab_cache(budget_id, entity_type, entity_id), ynab_sync.idempotency_key,
card_account_mappings(budget_id, card_last_four),
payee_category_memory(budget_id, payee_key).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import YNABCacheEntityType, YNABSyncStatus
from app.models import (
    CardAccountMapping,
    PayeeCategoryMemory,
    Receipt,
    Validation,
    YNABCache,
    YNABSync,
)

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _receipt(rid: str, file_hash: str) -> Receipt:
    return Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="x.jpg",
        file_hash=file_hash,
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1,
        status="needs_review",
    )


def _expect_integrity_error(db: Any) -> None:
    with pytest.raises(IntegrityError):
        db.flush()


def test_receipt_file_hash_is_unique(db_session: Any) -> None:
    db_session.add(_receipt("r1", "dup-hash"))
    db_session.flush()
    db_session.add(_receipt("r2", "dup-hash"))
    _expect_integrity_error(db_session)


def test_validation_receipt_version_is_unique(db_session: Any) -> None:
    db_session.add(_receipt("rv", "hash-rv"))
    db_session.flush()
    common = dict(receipt_id="rv", version=1, source="user", payload={}, is_valid=True, errors=[])
    db_session.add(Validation(**common))
    db_session.flush()
    db_session.add(Validation(**common))
    _expect_integrity_error(db_session)


def test_ynab_cache_entity_is_unique(db_session: Any) -> None:
    def _row() -> YNABCache:
        return YNABCache(
            budget_id="b1",
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id="cat-1",
            name="Groceries",
            raw_json={"id": "cat-1"},
        )

    db_session.add(_row())
    db_session.flush()
    db_session.add(_row())
    _expect_integrity_error(db_session)


def test_ynab_sync_idempotency_key_is_unique(db_session: Any) -> None:
    def _row() -> YNABSync:
        return YNABSync(
            receipt_id="rsync",
            idempotency_key="dup-ikey",
            status=YNABSyncStatus.RUNNING.value,
            match_mode="match_or_create",
            started_at=NOW,
        )

    db_session.add(_row())
    db_session.flush()
    db_session.add(_row())
    _expect_integrity_error(db_session)


def test_card_account_mapping_is_unique(db_session: Any) -> None:
    def _row() -> CardAccountMapping:
        return CardAccountMapping(budget_id="b1", card_last_four="1234", account_id="acct-1")

    db_session.add(_row())
    db_session.flush()
    db_session.add(_row())
    _expect_integrity_error(db_session)


def test_payee_category_memory_is_unique(db_session: Any) -> None:
    def _row() -> PayeeCategoryMemory:
        return PayeeCategoryMemory(budget_id="b1", payee_key="costco", category_id="cat-1")

    db_session.add(_row())
    db_session.flush()
    db_session.add(_row())
    _expect_integrity_error(db_session)
