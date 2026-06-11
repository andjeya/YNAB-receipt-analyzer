"""Tests for the server-side twin confirmation gate on POST /receipts/{id}/sync.

Covers:
1. Sync with unconfirmed twin → 400 twin_unconfirmed.
2. Sync with partially confirmed twin (only date_time) → 400 twin_unconfirmed.
3. Sync with fully confirmed twin → proceeds (no 400 from twin check).
4. Sync with no twin at all → proceeds (existing behavior preserved).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.receipts import sync_receipt
from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Receipt, ReceiptTwin, Validation
from app.schemas import SyncRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RECEIPT_ID = "twin-gate-aaaa-bbbb-cccc-dddddddddddd"


def _seed_receipt_with_validation(db: Any, settings: Settings) -> Receipt:
    """Insert a needs_review Receipt and a valid Validation matching the seeded cache."""
    receipt = Receipt(
        id=RECEIPT_ID,
        storage_key=f"receipts/{RECEIPT_ID}.jpg",
        original_filename="twin-gate-test.jpg",
        file_hash="twin-gate-hash-" + RECEIPT_ID,
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.flush()

    validation = Validation(
        receipt_id=receipt.id,
        version=1,
        source="user",
        payload={
            "payee_name": "Twin Gate Store",
            "account_id": "acct-1",
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": 10.0,
            "category_id": "cat-1",
            "splits": [],
        },
        allocation_workspace=None,
        is_valid=True,
        errors=[],
    )
    db.add(validation)
    receipt.latest_validation_version = 1
    db.commit()
    db.refresh(receipt)
    return receipt


def _add_twin(db: Any, *, date_time_confirmed: bool, total_confirmed: bool) -> ReceiptTwin:
    """Add a ReceiptTwin with the given confirmation state."""
    twin = ReceiptTwin(
        receipt_id=RECEIPT_ID,
        version=1,
        source="model",
        payload={
            "store_name": "Twin Gate Store",
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "total_amount": 10.0,
            "subtotal": 9.0,
            "tax_total": 1.0,
            "payment_method": "Credit",
            "line_items": [],
        },
        confirmed_sections={"date_time": date_time_confirmed, "total": total_confirmed},
    )
    db.add(twin)
    db.commit()
    db.refresh(twin)
    return twin


def _enabled_settings(base: Settings) -> Settings:
    """Settings with sync enabled (needed to get past the kill-switch check)."""
    return Settings(
        _env_file=None,
        ynab_budget_id=base.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
    )


# ---------------------------------------------------------------------------
# Test 1: Unconfirmed twin (both sections false) → 400 twin_unconfirmed
# ---------------------------------------------------------------------------


def test_sync_blocked_when_twin_fully_unconfirmed(db_with_cache: Any, test_settings: Settings) -> None:
    """sync_receipt raises HTTPException 400 twin_unconfirmed when twin exists but neither section confirmed."""
    _seed_receipt_with_validation(db_with_cache, test_settings)
    _add_twin(db_with_cache, date_time_confirmed=False, total_confirmed=False)

    with pytest.raises(HTTPException) as exc_info:
        sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=_enabled_settings(test_settings),
        )

    exc = exc_info.value
    assert exc.status_code == 400
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == "twin_unconfirmed"
    assert "Confirm Date" in exc.detail.get("message", "")


# ---------------------------------------------------------------------------
# Test 2: Partially confirmed twin (only date_time) → 400 twin_unconfirmed
# ---------------------------------------------------------------------------


def test_sync_blocked_when_twin_partially_confirmed(db_with_cache: Any, test_settings: Settings) -> None:
    """sync_receipt raises 400 twin_unconfirmed when only date_time is confirmed but not total."""
    _seed_receipt_with_validation(db_with_cache, test_settings)
    _add_twin(db_with_cache, date_time_confirmed=True, total_confirmed=False)

    with pytest.raises(HTTPException) as exc_info:
        sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=_enabled_settings(test_settings),
        )

    exc = exc_info.value
    assert exc.status_code == 400
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == "twin_unconfirmed"


# ---------------------------------------------------------------------------
# Test 3: Fully confirmed twin → proceeds past the twin gate
# ---------------------------------------------------------------------------


def test_sync_proceeds_when_twin_fully_confirmed(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync_receipt does NOT raise twin_unconfirmed when both sections are confirmed."""
    _seed_receipt_with_validation(db_with_cache, test_settings)
    _add_twin(db_with_cache, date_time_confirmed=True, total_confirmed=True)

    # Patch enqueue_sync_job so we don't need a real RQ connection
    from unittest.mock import patch

    with patch("app.api.receipts.enqueue_sync_job", return_value="job-confirmed-twin"):
        result = sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=_enabled_settings(test_settings),
        )

    assert result.job_id == "job-confirmed-twin"


# ---------------------------------------------------------------------------
# Test 4: No twin at all → proceeds (existing behavior preserved)
# ---------------------------------------------------------------------------


def test_sync_proceeds_when_no_twin_exists(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync_receipt does NOT block when the receipt has no twin at all."""
    _seed_receipt_with_validation(db_with_cache, test_settings)
    # No twin added

    from unittest.mock import patch

    with patch("app.api.receipts.enqueue_sync_job", return_value="job-no-twin"):
        result = sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=_enabled_settings(test_settings),
        )

    assert result.job_id == "job-no-twin"
