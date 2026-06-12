"""Tests for per-receipt sync_ready flag on GET /receipts (list endpoint).

Each gate is tested individually (false → sync_ready=False) and in combination
(all true → sync_ready=True).  Also covers:
- sync disabled globally → all False
- no twin at all → gate passes (sync_ready=True when other gates pass)
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.receipts import _batch_sync_ready, list_receipts
from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Receipt, ReceiptTwin, Validation


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

ACCT_ID = "acct-1"
VALID_PAYLOAD = {
    "payee_name": "Test Store",
    "account_id": ACCT_ID,
    "transaction_date": "2026-06-01",
    "transaction_time": None,
    "memo": "",
    "total_amount": 10.0,
    "category_id": "cat-1",
    "splits": [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_receipt(db: Any, receipt_id: str, status: str = ReceiptStatus.NEEDS_REVIEW.value,
                  duplicate_of: str | None = None) -> Receipt:
    receipt = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename=f"{receipt_id}.jpg",
        file_hash=f"hash-{receipt_id}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=512,
        status=status,
        duplicate_of_receipt_id=duplicate_of,
    )
    db.add(receipt)
    db.flush()
    return receipt


def _add_validation(db: Any, receipt_id: str, payload: dict | None = None,
                    is_valid: bool = True, version: int = 1) -> Validation:
    v = Validation(
        receipt_id=receipt_id,
        version=version,
        source="user",
        payload=payload if payload is not None else VALID_PAYLOAD,
        allocation_workspace=None,
        is_valid=is_valid,
        errors=[],
    )
    db.add(v)
    db.flush()
    return v


def _add_twin(db: Any, receipt_id: str, *,
              date_time_confirmed: bool = True,
              total_confirmed: bool = True) -> ReceiptTwin:
    twin = ReceiptTwin(
        receipt_id=receipt_id,
        version=1,
        source="model",
        payload={"store_name": "Test Store", "total_amount": 10.0, "line_items": []},
        confirmed_sections={"date_time": date_time_confirmed, "total": total_confirmed},
    )
    db.add(twin)
    db.flush()
    return twin


def _enabled_settings(base: Settings) -> Settings:
    return Settings(
        _env_file=None,
        ynab_budget_id=base.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
    )


def _disabled_settings(base: Settings) -> Settings:
    return Settings(
        _env_file=None,
        ynab_budget_id=base.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=False,
        ynab_dry_run=True,
    )


# ---------------------------------------------------------------------------
# Test: all gates True → sync_ready=True
# ---------------------------------------------------------------------------


def test_sync_ready_all_gates_pass(db_with_cache: Any, test_settings: Settings) -> None:
    """All gates pass → sync_ready True."""
    rid = "sr-all-true-aaaa-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=True, total_confirmed=True)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is True


# ---------------------------------------------------------------------------
# Test: sync disabled → all False
# ---------------------------------------------------------------------------


def test_sync_ready_sync_disabled(db_with_cache: Any, test_settings: Settings) -> None:
    """sync_enabled=False → all False regardless of receipt state."""
    rid = "sr-disabled-aaaa-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=True, total_confirmed=True)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=False)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: wrong status gate
# ---------------------------------------------------------------------------


def test_sync_ready_wrong_status(db_with_cache: Any, test_settings: Settings) -> None:
    """Receipt not in NEEDS_REVIEW → sync_ready False."""
    rid = "sr-wrong-status-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid, status=ReceiptStatus.SYNCED.value)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=True, total_confirmed=True)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: duplicate_of_receipt_id set → False
# ---------------------------------------------------------------------------


def test_sync_ready_is_duplicate(db_with_cache: Any, test_settings: Settings) -> None:
    """Receipt is a duplicate → sync_ready False."""
    other_id = "sr-other-receipt-aaaa-bbbb-cccc-dddd"
    _make_receipt(db_with_cache, other_id)

    rid = "sr-duplicate-receipt-bbbb-cccc-dddd"
    receipt = _make_receipt(db_with_cache, rid, duplicate_of=other_id)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=True, total_confirmed=True)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: no validation → False
# ---------------------------------------------------------------------------


def test_sync_ready_no_validation(db_with_cache: Any, test_settings: Settings) -> None:
    """No validation row → sync_ready False."""
    rid = "sr-no-validation-aaaa-bbbb-cccc-dddd"
    receipt = _make_receipt(db_with_cache, rid)
    # No validation added
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: validation is_valid=False → False
# ---------------------------------------------------------------------------


def test_sync_ready_validation_invalid(db_with_cache: Any, test_settings: Settings) -> None:
    """Validation is_valid=False → sync_ready False."""
    rid = "sr-invalid-val-aaaa-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid, is_valid=False)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: unknown account sentinel → False
# ---------------------------------------------------------------------------


def test_sync_ready_unknown_account(db_with_cache: Any, test_settings: Settings) -> None:
    """account_id == UNKNOWN_ACCOUNT_ID → sync_ready False."""
    rid = "sr-unknown-acct-aaaa-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    payload = {**VALID_PAYLOAD, "account_id": "__unknown__"}
    _add_validation(db_with_cache, rid, payload=payload)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: blank account_id → False
# ---------------------------------------------------------------------------


def test_sync_ready_blank_account(db_with_cache: Any, test_settings: Settings) -> None:
    """Blank account_id → sync_ready False."""
    rid = "sr-blank-acct-aaaa-bbbb-cccc-dddddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    payload = {**VALID_PAYLOAD, "account_id": ""}
    _add_validation(db_with_cache, rid, payload=payload)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: twin exists but unconfirmed (both False) → False
# ---------------------------------------------------------------------------


def test_sync_ready_twin_unconfirmed(db_with_cache: Any, test_settings: Settings) -> None:
    """Twin exists with both sections unconfirmed → sync_ready False."""
    rid = "sr-twin-unconfirmed-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=False, total_confirmed=False)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: twin partially confirmed (only date_time) → False
# ---------------------------------------------------------------------------


def test_sync_ready_twin_partially_confirmed(db_with_cache: Any, test_settings: Settings) -> None:
    """Twin with only date_time confirmed → sync_ready False."""
    rid = "sr-twin-partial-aaaa-bbbb-cccc-dddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=True, total_confirmed=False)
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is False


# ---------------------------------------------------------------------------
# Test: no twin → gate passes → sync_ready=True
# ---------------------------------------------------------------------------


def test_sync_ready_no_twin(db_with_cache: Any, test_settings: Settings) -> None:
    """No twin at all → twin gate passes → sync_ready True."""
    rid = "sr-no-twin-aaaa-bbbb-cccc-dddddddddddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    # No twin added
    db_with_cache.commit()

    result = _batch_sync_ready(db_with_cache, [receipt], sync_enabled=True)
    assert result[rid] is True


# ---------------------------------------------------------------------------
# Test: via list endpoint response
# ---------------------------------------------------------------------------


def test_list_endpoint_includes_sync_ready(db_with_cache: Any, test_settings: Settings) -> None:
    """list_receipts endpoint returns sync_ready=True for a qualifying receipt."""
    rid = "sr-list-ep-aaaa-bbbb-cccc-dddddddddddd"
    _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    _add_twin(db_with_cache, rid, date_time_confirmed=True, total_confirmed=True)
    db_with_cache.commit()

    settings = _enabled_settings(test_settings)
    summaries = list_receipts(status=None, sort="newest", limit=200, db=db_with_cache, settings=settings)
    match = next((s for s in summaries if s.id == rid), None)
    assert match is not None
    assert match.sync_ready is True


def test_list_endpoint_sync_ready_false_when_disabled(db_with_cache: Any, test_settings: Settings) -> None:
    """list_receipts returns sync_ready=False when sync is globally disabled."""
    rid = "sr-list-dis-aaaa-bbbb-cccc-dddddddddddd"
    _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, rid)
    db_with_cache.commit()

    settings = _disabled_settings(test_settings)
    summaries = list_receipts(status=None, sort="newest", limit=200, db=db_with_cache, settings=settings)
    match = next((s for s in summaries if s.id == rid), None)
    assert match is not None
    assert match.sync_ready is False
