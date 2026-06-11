"""Tests for the YNAB sync kill-switch and dry-run feature.

Covers:
1. Settings defaults are safe (ynab_sync_enabled=False, ynab_dry_run=True).
2. API gate blocks sync when disabled (409 ynab_sync_disabled).
3. Worker gate raises YNABSyncDisabledError when disabled.
4. Dry-run persists payload without calling YNAB client.
5. Dry-run drives receipt to needs_review and does not count as successful sync.
6. Dry-run payload is retrievable via the detail API.
7. Live path (enabled=True, dry_run=False) is unchanged.
8. Dry-run does not invoke gamification.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.receipts import _has_successful_sync, get_receipt_detail, sync_receipt
from app.config import Settings
from app.enums import ReceiptStatus, YNABSyncStatus
from app.models import Receipt, Validation, YNABSync
from app.schemas import SyncRequest
from app.services.ynab import (
    YNABSyncDisabledError,
    _latest_successful_sync_for_receipt,
    sync_receipt_to_ynab,
)
from receipt_shared.ynab_client import YNABClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RECEIPT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
EXPECTED_MILLIUNITS = -25000  # $25.00 outflow


def _seed_receipt_with_validation(db: Any, settings: Settings) -> tuple[Receipt, Validation]:
    """Insert a needs_review Receipt and a valid Validation matching the seeded cache."""
    receipt = Receipt(
        id=RECEIPT_ID,
        storage_key=f"receipts/{RECEIPT_ID}.jpg",
        original_filename="test.jpg",
        file_hash="killswitch-test-hash-" + RECEIPT_ID,
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
            "payee_name": "Test Payee",
            "account_id": "acct-1",
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": 25.0,
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
    db.refresh(validation)
    return receipt, validation


# ---------------------------------------------------------------------------
# Test 1: Settings defaults are safe
# ---------------------------------------------------------------------------


def test_settings_defaults_are_safe() -> None:
    """Default Settings must disable live writes and enable dry-run."""
    s = Settings(_env_file=None)
    assert s.ynab_sync_enabled is False
    assert s.ynab_dry_run is True


# ---------------------------------------------------------------------------
# Test 2: API gate blocks when sync disabled
# ---------------------------------------------------------------------------


def test_api_blocks_when_sync_disabled(db_with_cache: Any, test_settings: Settings) -> None:
    """sync_receipt raises HTTPException 409 ynab_sync_disabled; receipt not mutated to syncing."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    disabled_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_sync_enabled=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=disabled_settings,
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == "ynab_sync_disabled"

    # Receipt must NOT have been mutated to syncing
    from sqlalchemy import select as sa_select
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status != ReceiptStatus.SYNCING.value


# ---------------------------------------------------------------------------
# Test 3: Worker raises YNABSyncDisabledError when disabled
# ---------------------------------------------------------------------------


def test_worker_blocks_when_disabled_even_if_invoked(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync_receipt_to_ynab raises YNABSyncDisabledError; YNABSync row failed; client never called."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    mock_get_client = MagicMock()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", mock_get_client)

    disabled_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=False,
        ynab_dry_run=False,
    )

    with pytest.raises(YNABSyncDisabledError) as exc_info:
        sync_receipt_to_ynab(
            db=db_with_cache,
            settings=disabled_settings,
            receipt_id=RECEIPT_ID,
            force_create=False,
            allow_update_match=True,
        )

    assert "disabled" in str(exc_info.value).lower()
    mock_get_client.assert_not_called()

    # YNABSync row should be in failed state with error text about disabled
    from sqlalchemy import select as sa_select
    sync_row = db_with_cache.scalar(sa_select(YNABSync).where(YNABSync.receipt_id == RECEIPT_ID))
    assert sync_row is not None
    assert sync_row.status == YNABSyncStatus.FAILED.value
    assert "disabled" in (sync_row.error_text or "").lower()

    # Receipt should be in error_sync state
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.ERROR_SYNC.value


# ---------------------------------------------------------------------------
# Test 4: Dry-run persists payload without calling YNAB client
# ---------------------------------------------------------------------------


def test_dry_run_persists_payload_and_makes_no_client_calls(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With enabled=True, dry_run=True: payload persisted, client never instantiated."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    mock_get_client = MagicMock()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", mock_get_client)

    dry_run_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
        ynab_new_transaction_flag_color="blue",
    )

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=dry_run_settings,
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result["status"] == YNABSyncStatus.DRY_RUN.value
    assert result["dry_run"] is True
    assert result["transaction_id"] is None

    # get_ynab_client must never have been called
    mock_get_client.assert_not_called()

    # YNABSync row should have dry_run status and the full payload
    from sqlalchemy import select as sa_select
    sync_row = db_with_cache.scalar(sa_select(YNABSync).where(YNABSync.receipt_id == RECEIPT_ID))
    assert sync_row is not None
    assert sync_row.status == YNABSyncStatus.DRY_RUN.value
    assert sync_row.raw_request is not None
    txn = sync_row.raw_request["transaction"]
    assert txn["amount"] == EXPECTED_MILLIUNITS
    assert txn["approved"] is False
    assert txn.get("flag_color") == "blue"


# ---------------------------------------------------------------------------
# Test 5: Dry-run receipt status outcome
# ---------------------------------------------------------------------------


def test_dry_run_receipt_status_outcome(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run drives receipt to needs_review; not counted as a successful sync."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    monkeypatch.setattr("app.services.ynab.get_ynab_client", MagicMock())

    dry_run_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
    )

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=dry_run_settings,
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.NEEDS_REVIEW.value
    assert receipt.status_reason is not None
    assert receipt.status_reason.startswith("Dry run")
    assert receipt.sync_completed_at is not None

    # Must NOT count as a successful sync
    assert _has_successful_sync(db_with_cache, RECEIPT_ID) is False
    assert _latest_successful_sync_for_receipt(db_with_cache, RECEIPT_ID) is None


# ---------------------------------------------------------------------------
# Test 6: Dry-run payload retrievable via detail API
# ---------------------------------------------------------------------------


def test_dry_run_payload_retrievable_via_detail_api(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_receipt_detail returns latest_sync with status dry_run and matching amount."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    monkeypatch.setattr("app.services.ynab.get_ynab_client", MagicMock())

    dry_run_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
    )

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=dry_run_settings,
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    detail = get_receipt_detail(receipt_id=RECEIPT_ID, db=db_with_cache)
    assert detail.latest_sync is not None
    assert detail.latest_sync.status == YNABSyncStatus.DRY_RUN.value
    assert detail.latest_sync.raw_request is not None
    assert detail.latest_sync.raw_request["transaction"]["amount"] == EXPECTED_MILLIUNITS


# ---------------------------------------------------------------------------
# Test 7: Live path (enabled=True, dry_run=False) is unchanged
# ---------------------------------------------------------------------------


def test_enabled_live_path_unchanged(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With enabled=True, dry_run=False: live YNAB client is called; receipt synced."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    mock_client = MagicMock(spec=YNABClient)
    mock_client.list_transactions_since.return_value = []
    mock_client.create_transaction.return_value = {"id": "txn-live-1"}

    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda settings: mock_client)

    # Also monkeypatch gamification so we don't need GameState seeded
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    live_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=False,
    )

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=live_settings,
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result["status"] == YNABSyncStatus.CREATED.value
    assert result["transaction_id"] == "txn-live-1"
    assert result.get("dry_run") is None or result.get("dry_run") is False
    mock_client.create_transaction.assert_called_once()

    # YNABSync row should be "created"
    from sqlalchemy import select as sa_select
    sync_row = db_with_cache.scalar(sa_select(YNABSync).where(YNABSync.receipt_id == RECEIPT_ID))
    assert sync_row is not None
    assert sync_row.status == YNABSyncStatus.CREATED.value
    assert sync_row.created_transaction_id == "txn-live-1"

    # Receipt should be synced
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.SYNCED.value


# ---------------------------------------------------------------------------
# Test 8: Dry-run does not invoke gamification
# ---------------------------------------------------------------------------


def test_dry_run_does_not_run_gamification(
    db_with_cache: Any, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_sync_gamification must NOT be called during a dry-run."""
    _seed_receipt_with_validation(db_with_cache, test_settings)

    monkeypatch.setattr("app.services.ynab.get_ynab_client", MagicMock())

    mock_gamification = MagicMock()
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", mock_gamification)

    dry_run_settings = Settings(
        _env_file=None,
        ynab_budget_id=test_settings.ynab_budget_id,
        ynab_access_token="test-token",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
    )

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=dry_run_settings,
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    mock_gamification.assert_not_called()
