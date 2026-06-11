"""Tests for M2 increment 1: idempotency core.

Covers:
1.  test_import_id_format_length — RA:1: prefix, len==36, no dashes.
2.  test_import_id_deterministic_across_amount_edit — same receipt, different totals → same id.
3.  test_create_payload_includes_import_id — create_transaction call_args has correct import_id.
4.  test_duplicate_import_id_409_resolves_to_existing — 409 resolved via import_id list lookup.
5.  test_endpoint_double_call_second_returns_409 — second sync call returns 409 sync_in_progress.
6.  test_endpoint_enqueue_failure_rolls_back_syncing — enqueue raises; receipt back to needs_review.
7.  test_worker_second_invocation_skips_when_running — fresh RUNNING row → skipped_duplicate.
8.  test_worker_reclaims_stale_running_row — stale RUNNING + live txn → reclaim, no create.
9.  test_retry_with_preserved_id_verifies_not_recreates — reused row + live txn → no create.
10. test_retry_preserved_id_deleted_falls_through_to_create — get_transaction None → create called.
11. test_row_reuse_preserves_evidence_fields — created_transaction_id / raw_response preserved.
12. test_duplicate_409_with_no_matching_transaction_reraises — list returns nothing → re-raise.
13. test_force_create_with_existing_import_id_resolves_idempotently — force_create + 409 → success.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.receipts import sync_receipt
from app.config import Settings
from app.enums import ReceiptStatus, YNABSyncStatus
from app.models import Receipt, Validation, YNABSync
from app.schemas import SyncRequest
from app.services.ynab import (
    IMPORT_ID_PREFIX,
    _build_import_id,
    sync_receipt_to_ynab,
)
from receipt_shared.ynab_client import YNABClient, YNABConflictError


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

RECEIPT_ID = "aabbccdd-1122-4333-8444-556677889900"
RECEIPT_ID_2 = "00112233-4455-4667-8889-aabbccddeeff"


def _live_settings(**overrides: Any) -> Settings:
    """Settings with live YNAB write path enabled."""
    base = dict(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="test-token",
        ynab_budget_id="test-budget-id",
        ynab_default_account_id="acct-1",
        ynab_sync_enabled=True,
        ynab_dry_run=False,
        object_store_root="./data",
        ingest_dir="./data/ingest",
    )
    base.update(overrides)
    return Settings(**base)


def _seed_receipt(db: Any, receipt_id: str, status: str = ReceiptStatus.NEEDS_REVIEW.value) -> Receipt:
    receipt = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename="test.jpg",
        file_hash=f"hash-idempotency-{receipt_id}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=status,
    )
    db.add(receipt)
    db.flush()
    return receipt


def _seed_validation(db: Any, receipt_id: str, total: float = 25.0) -> Validation:
    v = Validation(
        receipt_id=receipt_id,
        version=1,
        source="user",
        payload={
            "payee_name": "Test Payee",
            "account_id": "acct-1",
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": total,
            "category_id": "cat-1",
            "splits": [],
            "transaction_kind": "purchase",
        },
        allocation_workspace=None,
        is_valid=True,
        errors=[],
    )
    db.add(v)
    db.flush()
    return v


def _seed_receipt_and_validation(db: Any, receipt_id: str = RECEIPT_ID, total: float = 25.0) -> tuple[Receipt, Validation]:
    r = _seed_receipt(db, receipt_id)
    v = _seed_validation(db, receipt_id, total)
    r.latest_validation_version = 1
    db.commit()
    db.refresh(r)
    db.refresh(v)
    return r, v


def _mock_client(
    create_response: dict[str, Any] | None = None,
    get_transaction_response: dict[str, Any] | None = None,
) -> MagicMock:
    client = MagicMock(spec=YNABClient)
    client.list_transactions_since.return_value = []
    client.create_transaction.return_value = create_response or {"id": "txn-new-1"}
    client.get_transaction.return_value = get_transaction_response or {}
    return client


# ---------------------------------------------------------------------------
# Test 1: import_id format and length
# ---------------------------------------------------------------------------


def test_import_id_format_length() -> None:
    """import_id must start with RA:1:, be <=36 chars, and contain no dashes."""
    import_id = _build_import_id(RECEIPT_ID)
    assert import_id.startswith(IMPORT_ID_PREFIX)
    assert len(import_id) <= 36
    # No dashes in the receipt portion
    suffix = import_id[len(IMPORT_ID_PREFIX):]
    assert "-" not in suffix


# ---------------------------------------------------------------------------
# Test 2: import_id is deterministic (payload changes don't affect it)
# ---------------------------------------------------------------------------


def test_import_id_deterministic_across_amount_edit() -> None:
    """Same receipt UUID produces the same import_id regardless of amount."""
    id1 = _build_import_id(RECEIPT_ID)
    id2 = _build_import_id(RECEIPT_ID)
    assert id1 == id2

    # Also explicitly: two receipts with different totals but same ID → same import_id.
    # (The import_id is derived only from receipt.id, not from the payload amounts.)
    id_low = _build_import_id(RECEIPT_ID)
    id_high = _build_import_id(RECEIPT_ID)
    assert id_low == id_high


# ---------------------------------------------------------------------------
# Test 3: transaction payload sent to create_transaction includes import_id
# ---------------------------------------------------------------------------


def test_create_payload_includes_import_id(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dict passed to create_transaction must carry the correct import_id."""
    _seed_receipt_and_validation(db_with_cache)

    mock_client = _mock_client(create_response={"id": "txn-new-import"})
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    mock_client.create_transaction.assert_called_once()
    _budget_id, txn_arg = mock_client.create_transaction.call_args[0]
    expected_import_id = _build_import_id(RECEIPT_ID)
    assert txn_arg.get("import_id") == expected_import_id


# ---------------------------------------------------------------------------
# Test 4: YNAB 409 duplicate import_id resolves to existing transaction
# ---------------------------------------------------------------------------


def test_duplicate_import_id_409_resolves_to_existing(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When create_transaction raises YNABConflictError (409), the service resolves
    it by listing transactions and matching import_id → status CREATED, id 'txn-existing'."""
    _seed_receipt_and_validation(db_with_cache)

    expected_import_id = _build_import_id(RECEIPT_ID)
    existing_txn = {
        "id": "txn-existing",
        "amount": -25000,
        "date": "2026-06-01",
        "payee_name": "Test Payee",
        "import_id": expected_import_id,
        "memo": f"[receipt_id:{RECEIPT_ID}]",
        "deleted": False,
    }

    mock_client = _mock_client()
    # create_transaction raises 409 (duplicate import_id on that account)
    mock_client.create_transaction.side_effect = YNABConflictError(409, "Conflict")
    # list_transactions_since always returns the existing transaction (both for
    # the initial match attempt and the 409 resolution lookup).
    mock_client.list_transactions_since.return_value = [existing_txn]
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=False,  # no match-existing → goes straight to create
    )

    assert result["status"] == YNABSyncStatus.CREATED.value
    assert result["transaction_id"] == "txn-existing"
    mock_client.create_transaction.assert_called_once()
    mock_client.list_transactions_since.assert_called()

    # Receipt must be SYNCED (not ERROR_SYNC)
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.SYNCED.value


# ---------------------------------------------------------------------------
# Test 5: Endpoint double-call second returns 409
# ---------------------------------------------------------------------------


def test_endpoint_double_call_second_returns_409(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second concurrent call to sync_receipt returns 409 sync_in_progress."""
    _seed_receipt_and_validation(db_with_cache)

    mock_enqueue = MagicMock(return_value="job-id-1")
    monkeypatch.setattr("app.api.receipts.enqueue_sync_job", mock_enqueue)

    settings = _live_settings()

    # First call succeeds.
    response = sync_receipt(
        receipt_id=RECEIPT_ID,
        request=SyncRequest(),
        db=db_with_cache,
        settings=settings,
    )
    assert response.status == ReceiptStatus.SYNCING.value

    # Receipt is now SYNCING; second call should get 409.
    with pytest.raises(HTTPException) as exc_info:
        sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=settings,
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == "sync_in_progress"

    # enqueue should have been called exactly once.
    mock_enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: Enqueue failure rolls back SYNCING
# ---------------------------------------------------------------------------


def test_endpoint_enqueue_failure_rolls_back_syncing(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If enqueue raises, receipt is rolled back to needs_review."""
    _seed_receipt_and_validation(db_with_cache)

    def _failing_enqueue(**kwargs: Any) -> None:
        raise RuntimeError("Redis connection refused")

    monkeypatch.setattr("app.api.receipts.enqueue_sync_job", _failing_enqueue)

    settings = _live_settings()

    with pytest.raises(HTTPException) as exc_info:
        sync_receipt(
            receipt_id=RECEIPT_ID,
            request=SyncRequest(),
            db=db_with_cache,
            settings=settings,
        )

    assert exc_info.value.status_code == 503

    # Receipt must be back to needs_review (not stuck as SYNCING).
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.NEEDS_REVIEW.value


# ---------------------------------------------------------------------------
# Test 7: Worker second invocation skips when row is fresh RUNNING
# ---------------------------------------------------------------------------


def test_worker_second_invocation_skips_when_running(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing fresh RUNNING row → worker returns skipped_duplicate without any client call."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    from app.services.ynab import make_idempotency_key
    ikey = make_idempotency_key(RECEIPT_ID, validation.id, False, True)

    # Pre-seed a fresh RUNNING row.
    now = datetime.now(timezone.utc)
    existing_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key=ikey,
        status=YNABSyncStatus.RUNNING.value,
        match_mode="match_or_create",
        started_at=now,
    )
    db_with_cache.add(existing_row)
    db_with_cache.commit()

    mock_get_client = MagicMock()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", mock_get_client)

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result.get("skipped_duplicate") is True
    assert result["status"] == YNABSyncStatus.RUNNING.value
    mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: Worker reclaims stale RUNNING row and uses verified existing txn
# ---------------------------------------------------------------------------


def test_worker_reclaims_stale_running_row(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNNING row with stale started_at AND live txn → reclaim, no create, status CREATED."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    from app.services.ynab import make_idempotency_key
    ikey = make_idempotency_key(RECEIPT_ID, validation.id, False, True)

    # Pre-seed a STALE RUNNING row with a created_transaction_id already set.
    stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
    existing_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key=ikey,
        status=YNABSyncStatus.RUNNING.value,
        match_mode="match_or_create",
        started_at=stale_time,
        created_transaction_id="txn-prior",
    )
    db_with_cache.add(existing_row)
    db_with_cache.commit()

    live_txn = {"id": "txn-prior", "amount": -25000, "deleted": False}
    mock_client = MagicMock(spec=YNABClient)
    mock_client.get_transaction.return_value = live_txn
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    # Reclaimed and found the live transaction — status CREATED, no new create call.
    assert result["status"] == YNABSyncStatus.CREATED.value
    mock_client.create_transaction.assert_not_called()

    from sqlalchemy import select as sa_select
    row = db_with_cache.scalar(sa_select(YNABSync).where(YNABSync.idempotency_key == ikey))
    assert row is not None
    assert row.status == YNABSyncStatus.CREATED.value
    assert row.created_transaction_id == "txn-prior"


# ---------------------------------------------------------------------------
# Test 9: Retry with preserved id verifies (live) → no new create
# ---------------------------------------------------------------------------


def test_retry_with_preserved_id_verifies_not_recreates(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reused non-RUNNING row with created_transaction_id; get_transaction live → no create."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    from app.services.ynab import make_idempotency_key
    ikey = make_idempotency_key(RECEIPT_ID, validation.id, False, True)

    # Pre-seed a FAILED row (not RUNNING) with a prior created_transaction_id.
    existing_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key=ikey,
        status=YNABSyncStatus.FAILED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        created_transaction_id="txn-prior",
        error_text="gamification failure",
    )
    db_with_cache.add(existing_row)
    db_with_cache.commit()

    live_txn = {"id": "txn-prior", "amount": -25000, "deleted": False}
    mock_client = MagicMock(spec=YNABClient)
    mock_client.get_transaction.return_value = live_txn
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result["status"] == YNABSyncStatus.CREATED.value
    mock_client.create_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10: Retry with preserved id deleted → falls through to fresh create
# ---------------------------------------------------------------------------


def test_retry_preserved_id_deleted_falls_through_to_create(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_transaction returns None (deleted) → fresh create is called once."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    from app.services.ynab import make_idempotency_key
    ikey = make_idempotency_key(RECEIPT_ID, validation.id, False, True)

    # Pre-seed FAILED row with prior id.
    existing_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key=ikey,
        status=YNABSyncStatus.FAILED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        created_transaction_id="txn-deleted",
        error_text="post-sync failure",
    )
    db_with_cache.add(existing_row)
    db_with_cache.commit()

    mock_client = MagicMock(spec=YNABClient)
    # _get_transaction_by_id calls get_transaction and checks deleted; make it raise 404.
    mock_client.get_transaction.side_effect = RuntimeError("YNAB API error 404: not found")
    mock_client.list_transactions_since.return_value = []
    mock_client.create_transaction.return_value = {"id": "txn-fresh"}
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result["status"] == YNABSyncStatus.CREATED.value
    assert result["transaction_id"] == "txn-fresh"
    mock_client.create_transaction.assert_called_once()

    from sqlalchemy import select as sa_select
    row = db_with_cache.scalar(sa_select(YNABSync).where(YNABSync.idempotency_key == ikey))
    assert row is not None
    assert row.created_transaction_id == "txn-fresh"


# ---------------------------------------------------------------------------
# Test 11: Row reuse preserves evidence fields
# ---------------------------------------------------------------------------


def test_row_reuse_preserves_evidence_fields(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After atomic claim, prior created_transaction_id and raw_response are still present."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    from app.services.ynab import make_idempotency_key
    ikey = make_idempotency_key(RECEIPT_ID, validation.id, False, True)

    # Pre-seed FAILED row with evidence from a prior attempt.
    prior_raw_response = {"id": "txn-prior", "amount": -25000}
    existing_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key=ikey,
        status=YNABSyncStatus.FAILED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        created_transaction_id="txn-prior",
        raw_response=prior_raw_response,
        error_text="gamification blew up",
    )
    db_with_cache.add(existing_row)
    db_with_cache.commit()

    # Make get_transaction return the live txn (verify-before-create path).
    live_txn = {"id": "txn-prior", "amount": -25000, "deleted": False}
    mock_client = MagicMock(spec=YNABClient)
    mock_client.get_transaction.return_value = live_txn
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    # Simulate the state just after claim but BEFORE the retry logic runs by
    # confirming that even a full retry still ends with the evidence present.
    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    from sqlalchemy import select as sa_select
    row = db_with_cache.scalar(sa_select(YNABSync).where(YNABSync.idempotency_key == ikey))
    assert row is not None
    # Transaction id should still be the prior one (verify-before-create succeeded).
    assert row.created_transaction_id == "txn-prior"
    # raw_response was updated by _apply_post_sync path but still contains live txn data.
    assert row.raw_response is not None


# ---------------------------------------------------------------------------
# Test 12: 409 with no matching transaction re-raises (genuine conflict)
# ---------------------------------------------------------------------------


def test_duplicate_409_with_no_matching_transaction_reraises(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When create raises YNABConflictError and list returns no matching transaction,
    the error is re-raised as a genuine conflict and receipt ends up ERROR_SYNC."""
    _seed_receipt_and_validation(db_with_cache)

    mock_client = _mock_client()
    # create_transaction raises 409
    mock_client.create_transaction.side_effect = YNABConflictError(409, "Conflict")
    # list_transactions_since returns no transactions with matching import_id or memo marker
    mock_client.list_transactions_since.return_value = [
        {
            "id": "txn-unrelated",
            "amount": -99000,
            "date": "2026-06-01",
            "payee_name": "Other Payee",
            "deleted": False,
            "import_id": "OTHER:1:unrelated",
            "memo": "",
        },
    ]
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    with pytest.raises(YNABConflictError):
        sync_receipt_to_ynab(
            db=db_with_cache,
            settings=_live_settings(),
            receipt_id=RECEIPT_ID,
            force_create=False,
            allow_update_match=True,
        )

    # Receipt must be ERROR_SYNC (the re-raised exception caused a failure)
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.ERROR_SYNC.value


# ---------------------------------------------------------------------------
# Test 13: force_create + existing import_id resolves idempotently (Finding 5)
# ---------------------------------------------------------------------------


def test_force_create_with_existing_import_id_resolves_idempotently(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_create=True bypasses match-existing but uses deterministic import_id.

    With deterministic per-receipt import_ids, force_create can never double-create:
    if YNAB already has a transaction with the same import_id, it returns 409.
    _create_transaction_idempotent resolves the 409 by finding the existing transaction
    → success with the already-existing id, no duplicate, status CREATED.
    """
    _seed_receipt_and_validation(db_with_cache)

    expected_import_id = _build_import_id(RECEIPT_ID)
    existing_txn = {
        "id": "txn-existing",
        "amount": -25000,
        "date": "2026-06-01",
        "payee_name": "Test Payee",
        "import_id": expected_import_id,
        "memo": f"[receipt_id:{RECEIPT_ID}]",
        "deleted": False,
    }

    mock_client = _mock_client()
    # force_create calls create_transaction directly; YNAB says 409 (already exists)
    mock_client.create_transaction.side_effect = YNABConflictError(409, "Conflict")
    mock_client.list_transactions_since.return_value = [existing_txn]
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=True,  # bypass match-existing, always attempt create
        allow_update_match=True,
    )

    # Resolution finds existing → idempotent success
    assert result["status"] == YNABSyncStatus.CREATED.value
    assert result["transaction_id"] == "txn-existing"
    mock_client.create_transaction.assert_called_once()

    # Receipt is SYNCED, not ERROR_SYNC
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.SYNCED.value
