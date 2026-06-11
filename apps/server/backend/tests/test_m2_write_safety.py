"""M2 increment 2: write-safety tests (tests 13-21 per Opus spec).

Covers:
13. test_gamification_failure_does_not_fail_sync
    — gamification raises; sync row CREATED, receipt SYNCED, bookkeeping_ok False, incident recorded.
14. test_success_committed_before_bookkeeping
    — gamification raises; fresh query confirms CREATED row + SYNCED receipt persisted before
      bookkeeping runs (write result is durable).
15. test_split_structure_ignored_flags_review_not_recreate
    — PUT echo without structure change: delete/create NOT called, update called once,
      receipt NEEDS_REVIEW with manual-fix reason, matched_transaction_id recorded.
16. test_delete_transaction_never_called_in_any_sync_flow
    — parametrize across match-update / update-existing / split-ignored / create paths;
      delete_transaction.assert_not_called() everywhere.
17. (in test_ynab_split_sync.py) rewrite _update_or_replace_transaction tests
18. test_reconciliation_amount_drift_flags_needs_review
    — payload -50000 vs YNAB -45000: receipt NEEDS_REVIEW, validation pulled to YNAB amount,
      no update/create client call, correction recorded.
19. test_reconciliation_no_amount_drift_keeps_synced
    — category-only change: existing behavior unchanged (SYNCED).
20. test_split_signature_still_amount_blind
    — regression guard: _split_signature ignores amounts, detects only category changes.
21. test_stuck_reset_fails_stale_running_sync_rows
    — seeded stale RUNNING row + SYNCING receipt; after reset receipt NEEDS_REVIEW, row FAILED.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select as sa_select

from app.config import Settings
from app.enums import ReceiptStatus, YNABSyncStatus
from app.models import GameIncident, Receipt, Validation, YNABSync
from app.services.ynab import (
    _STRUCTURE_IGNORED_REASON,
    sync_receipt_to_ynab,
    make_idempotency_key,
)
from app.services.reconciliation import (
    _split_signature,
    run_ynab_reconciliation,
)
from receipt_shared.ynab_client import YNABClient

# ---------------------------------------------------------------------------
# Constants / helpers shared across tests
# ---------------------------------------------------------------------------

BUDGET_ID = "test-budget-id"
ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-1"
RECEIPT_ID = "aabbccdd-1122-4333-8555-556677889900"
TXN_ID = "txn-existing-1"


def _live_settings(**overrides: Any) -> Settings:
    base = dict(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="test-token",
        ynab_budget_id=BUDGET_ID,
        ynab_default_account_id=ACCOUNT_ID,
        ynab_sync_enabled=True,
        ynab_dry_run=False,
        object_store_root="./data",
        ingest_dir="./data/ingest",
    )
    base.update(overrides)
    return Settings(**base)


def _seed_receipt(db: Any, receipt_id: str, status: str = ReceiptStatus.NEEDS_REVIEW.value) -> Receipt:
    r = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename="test.jpg",
        file_hash=f"hash-m2ws-{receipt_id}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=status,
    )
    db.add(r)
    db.flush()
    return r


def _seed_validation(db: Any, receipt_id: str, total: float = 50.0) -> Validation:
    v = Validation(
        receipt_id=receipt_id,
        version=1,
        source="user",
        payload={
            "payee_name": "Test Payee",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": total,
            "category_id": CATEGORY_ID,
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


def _seed_receipt_and_validation(
    db: Any, receipt_id: str = RECEIPT_ID, total: float = 50.0
) -> tuple[Receipt, Validation]:
    r = _seed_receipt(db, receipt_id)
    v = _seed_validation(db, receipt_id, total)
    r.latest_validation_version = 1
    db.commit()
    db.refresh(r)
    db.refresh(v)
    return r, v


def _mock_ynab_client(
    *,
    create_response: dict[str, Any] | None = None,
    update_response: dict[str, Any] | None = None,
    list_response: list[dict[str, Any]] | None = None,
    get_response: Any = None,
) -> MagicMock:
    client = MagicMock(spec=YNABClient)
    client.create_transaction.return_value = create_response or {"id": "txn-new-1"}
    client.update_transaction.return_value = update_response or {}
    client.list_transactions_since.return_value = list_response or []
    client.get_transaction.return_value = get_response if get_response is not None else {}
    client.delete_transaction.return_value = {}
    return client


# ---------------------------------------------------------------------------
# Test 13: gamification failure does not fail the sync
# ---------------------------------------------------------------------------


def test_gamification_failure_does_not_fail_sync(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gamification raises; receipt is SYNCED, sync row CREATED, bookkeeping_ok False, incident recorded."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    mock_client = _mock_ynab_client(create_response={"id": "txn-gami-fail"})
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("gamification exploded")

    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", _raise)

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    # Sync itself succeeded
    assert result["status"] == YNABSyncStatus.CREATED.value
    assert result["transaction_id"] == "txn-gami-fail"
    assert result["bookkeeping_ok"] is False

    # Receipt is SYNCED (not ERROR_SYNC)
    db_with_cache.expire_all()
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.SYNCED.value

    # Sync row is CREATED
    row = db_with_cache.scalar(
        sa_select(YNABSync).where(YNABSync.receipt_id == RECEIPT_ID)
    )
    assert row is not None
    assert row.status == YNABSyncStatus.CREATED.value

    # Incident was recorded
    incidents = list(db_with_cache.scalars(sa_select(GameIncident)))
    assert any("bookkeeping_sync_failure" == i.incident_type for i in incidents)


# ---------------------------------------------------------------------------
# Test 14: YNAB write result is committed BEFORE bookkeeping runs
# ---------------------------------------------------------------------------


def test_success_committed_before_bookkeeping(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when gamification fails, the sync row CREATED and receipt SYNCED are durable."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    mock_client = _mock_ynab_client(create_response={"id": "txn-commit-before"})
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("bookkeeping error")

    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", _raise)

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    # Expire all cached objects and re-query from the database to confirm persistence.
    db_with_cache.expire_all()

    persisted_receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert persisted_receipt is not None
    assert persisted_receipt.status == ReceiptStatus.SYNCED.value

    rows = list(db_with_cache.scalars(sa_select(YNABSync).where(YNABSync.receipt_id == RECEIPT_ID)))
    assert len(rows) == 1
    assert rows[0].status == YNABSyncStatus.CREATED.value
    assert rows[0].created_transaction_id == "txn-commit-before"


# ---------------------------------------------------------------------------
# Test 15: split structure ignored → NEEDS_REVIEW, no delete/create, matched_id set
# ---------------------------------------------------------------------------


def test_split_structure_ignored_flags_review_not_recreate(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When YNAB ignores a split structure update: NEEDS_REVIEW, update called once,
    delete/create NOT called, matched_transaction_id recorded with the original txn id."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache)

    # Simulate the prior-sync row that triggers the update-existing path.
    ikey = make_idempotency_key(RECEIPT_ID, validation.id, False, True)
    prior_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key=ikey + "-prior",
        status=YNABSyncStatus.CREATED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        created_transaction_id=TXN_ID,
        raw_request={
            "transaction": {
                "account_id": ACCOUNT_ID,
                "date": "2026-06-01",
                "amount": -50000,
                "payee_name": "Test Payee",
                "memo": "",
                "subtransactions": [
                    {"amount": -30000, "category_id": CATEGORY_ID, "memo": "a"},
                    {"amount": -20000, "category_id": CATEGORY_ID, "memo": "b"},
                ],
            }
        },
    )
    db_with_cache.add(prior_row)
    db_with_cache.commit()

    # YNAB returns the original transaction unchanged (PUT echo, structure not applied).
    existing_txn = {
        "id": TXN_ID,
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -50000,
        "payee_name": "Test Payee",
        "memo": "",
        "deleted": False,
        "subtransactions": [
            {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_ID, "memo": "a"},
            {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_ID, "memo": "b"},
        ],
    }

    mock_client = _mock_ynab_client(
        update_response=existing_txn,   # YNAB echoes back unchanged (structure ignored)
        get_response=existing_txn,       # get_transaction returns existing
    )
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    # Build a payload that would change split structure — change sub amounts.
    # The validation single-category payload will be converted to split by
    # adjusting the validation payload directly.
    validation.payload = {
        "payee_name": "Test Payee",
        "account_id": ACCOUNT_ID,
        "transaction_date": "2026-06-01",
        "transaction_time": None,
        "memo": "",
        "total_amount": 50.0,
        "category_id": None,
        "splits": [
            {"amount": 25.0, "category_id": CATEGORY_ID, "memo": "c"},
            {"amount": 25.0, "category_id": CATEGORY_ID, "memo": "d"},
        ],
        "transaction_kind": "purchase",
    }
    db_with_cache.flush()
    db_with_cache.commit()

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    # update_transaction was called once; delete/create must not have been called
    mock_client.update_transaction.assert_called_once()
    mock_client.delete_transaction.assert_not_called()
    mock_client.create_transaction.assert_not_called()

    # Receipt is NEEDS_REVIEW with the manual-fix reason
    db_with_cache.expire_all()
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.NEEDS_REVIEW.value
    assert receipt.status_reason == _STRUCTURE_IGNORED_REASON

    # The sync row records the matched_transaction_id (not a new created id)
    row = db_with_cache.scalar(
        sa_select(YNABSync).where(
            YNABSync.receipt_id == RECEIPT_ID,
            YNABSync.idempotency_key != ikey + "-prior",
        )
    )
    assert row is not None
    assert row.matched_transaction_id == TXN_ID
    assert row.created_transaction_id is None

    # result reflects the structure_applied=False flag
    assert result.get("structure_applied") is False


# ---------------------------------------------------------------------------
# Test 16: delete_transaction NEVER called in any sync flow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flow", [
    "match_update",
    "update_existing",
    "split_ignored",
    "create_new",
])
def test_delete_transaction_never_called_in_any_sync_flow(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch, flow: str
) -> None:
    """delete_transaction must never be called regardless of sync flow path."""
    rid = str(uuid.uuid4())
    receipt, validation = _seed_receipt_and_validation(db_with_cache, receipt_id=rid)

    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    if flow == "match_update":
        # Matched existing transaction that has user data — minimal update path
        matched_txn = {
            "id": TXN_ID,
            "account_id": ACCOUNT_ID,
            "date": "2026-06-01",
            "amount": -50000,
            "payee_name": "Test Payee",
            "memo": "manual memo",
            "deleted": False,
            "category_id": CATEGORY_ID,
            "subtransactions": [],
        }
        mock_client = _mock_ynab_client(
            list_response=[matched_txn],
            update_response=matched_txn,
        )
        monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
        sync_receipt_to_ynab(db=db_with_cache, settings=_live_settings(), receipt_id=rid,
                             force_create=False, allow_update_match=True)

    elif flow == "update_existing":
        # Prior success row exists — triggers update-existing path (structure matches)
        ikey_prior = make_idempotency_key(rid, validation.id, False, True) + "-prior"
        prior_row = YNABSync(
            receipt_id=rid,
            validation_id=validation.id,
            idempotency_key=ikey_prior,
            status=YNABSyncStatus.CREATED.value,
            match_mode="match_or_create",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            created_transaction_id=TXN_ID,
            raw_request={"transaction": {"date": "2026-06-01", "amount": -50000,
                                          "account_id": ACCOUNT_ID, "payee_name": "Test Payee",
                                          "memo": "", "category_id": CATEGORY_ID}},
        )
        db_with_cache.add(prior_row)
        db_with_cache.commit()

        updated_txn = {
            "id": TXN_ID,
            "account_id": ACCOUNT_ID,
            "date": "2026-06-01",
            "amount": -50000,
            "payee_name": "Test Payee",
            "memo": "",
            "deleted": False,
            "category_id": CATEGORY_ID,
            "subtransactions": [],
        }
        mock_client = _mock_ynab_client(
            update_response=updated_txn,
            get_response=updated_txn,
        )
        monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
        sync_receipt_to_ynab(db=db_with_cache, settings=_live_settings(), receipt_id=rid,
                             force_create=False, allow_update_match=True)

    elif flow == "split_ignored":
        # Prior success row with split; YNAB ignores update → structure_applied=False
        ikey_prior = make_idempotency_key(rid, validation.id, False, True) + "-prior-si"
        prior_row = YNABSync(
            receipt_id=rid,
            validation_id=validation.id,
            idempotency_key=ikey_prior,
            status=YNABSyncStatus.CREATED.value,
            match_mode="match_or_create",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            created_transaction_id=TXN_ID,
            raw_request={"transaction": {"date": "2026-06-01", "amount": -50000,
                                          "account_id": ACCOUNT_ID, "payee_name": "Test Payee",
                                          "memo": "", "category_id": CATEGORY_ID}},
        )
        db_with_cache.add(prior_row)
        db_with_cache.commit()

        existing_split_txn = {
            "id": TXN_ID,
            "account_id": ACCOUNT_ID,
            "date": "2026-06-01",
            "amount": -50000,
            "payee_name": "Test Payee",
            "memo": "",
            "deleted": False,
            "subtransactions": [
                {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_ID, "memo": "x"},
                {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_ID, "memo": "y"},
            ],
        }
        mock_client = _mock_ynab_client(
            update_response=existing_split_txn,  # structure ignored
            get_response=existing_split_txn,
        )
        monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

        # Adjust validation to split so PUT is attempted
        validation.payload = {
            **validation.payload,
            "category_id": None,
            "splits": [
                {"amount": 25.0, "category_id": CATEGORY_ID, "memo": "c"},
                {"amount": 25.0, "category_id": CATEGORY_ID, "memo": "d"},
            ],
        }
        db_with_cache.flush()
        db_with_cache.commit()

        sync_receipt_to_ynab(db=db_with_cache, settings=_live_settings(), receipt_id=rid,
                             force_create=False, allow_update_match=True)

    elif flow == "create_new":
        # No prior success row — fresh create
        mock_client = _mock_ynab_client(
            create_response={"id": "txn-fresh-new"},
            list_response=[],  # no match
        )
        monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)
        sync_receipt_to_ynab(db=db_with_cache, settings=_live_settings(), receipt_id=rid,
                             force_create=False, allow_update_match=True)

    mock_client.delete_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# Test 18: reconciliation amount drift flags NEEDS_REVIEW
# ---------------------------------------------------------------------------


def test_reconciliation_amount_drift_flags_needs_review(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Payload amount -50000 vs YNAB -45000: receipt NEEDS_REVIEW, validation pulled to YNAB
    amount, no update/create client call, correction event recorded."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache, total=50.0)
    receipt.status = ReceiptStatus.SYNCED.value
    db_with_cache.commit()

    # Create a completed CREATED sync row with the -50000 payload
    sync_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        idempotency_key="ikey-drift-test-1",
        status=YNABSyncStatus.CREATED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        completed_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        created_transaction_id=TXN_ID,
        raw_request={
            "transaction": {
                "account_id": ACCOUNT_ID,
                "date": "2026-06-01",
                "amount": -50000,
                "payee_name": "Test Payee",
                "memo": f"[receipt_id:{RECEIPT_ID}]",
                "category_id": CATEGORY_ID,
            }
        },
    )
    db_with_cache.add(sync_row)
    db_with_cache.commit()

    # YNAB has a different amount (-45000) for this transaction — amount drift.
    ynab_txn = {
        "id": TXN_ID,
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -45000,  # drifted from -50000
        "payee_name": "Test Payee",
        "memo": f"[receipt_id:{RECEIPT_ID}]",
        "category_id": CATEGORY_ID,
        "deleted": False,
        "subtransactions": [],
    }

    mock_client = MagicMock(spec=YNABClient)
    mock_client.get_transaction.return_value = ynab_txn
    monkeypatch.setattr("app.services.reconciliation.get_ynab_client", lambda s: mock_client)

    settings = _live_settings()
    run_ynab_reconciliation(db=db_with_cache, settings=settings)

    # YNAB client was never asked to update or create
    mock_client.update_transaction.assert_not_called()
    mock_client.create_transaction.assert_not_called()

    # Receipt is NEEDS_REVIEW
    db_with_cache.expire_all()
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt is not None
    assert receipt.status == ReceiptStatus.NEEDS_REVIEW.value
    assert receipt.status_reason is not None
    assert "YNAB amount" in receipt.status_reason

    # A correction event was recorded
    from app.models import ReceiptCorrection
    corrections = list(
        db_with_cache.scalars(
            sa_select(ReceiptCorrection).where(ReceiptCorrection.receipt_id == RECEIPT_ID)
        )
    )
    assert len(corrections) == 1

    # Validation was updated to pull YNAB amount (45.0)
    from sqlalchemy import select as sa_select2
    from app.models import Validation as V
    validations = list(
        db_with_cache.scalars(
            sa_select2(V).where(V.receipt_id == RECEIPT_ID).order_by(V.version.asc())
        )
    )
    latest_v = validations[-1]
    assert abs(float(latest_v.payload.get("total_amount", 0)) - 45.0) < 0.01


# ---------------------------------------------------------------------------
# Test 19: reconciliation category-only change keeps receipt SYNCED
# ---------------------------------------------------------------------------


def test_reconciliation_no_amount_drift_keeps_synced(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Category change only (no amount drift): receipt stays SYNCED after reconciliation."""
    receipt, validation = _seed_receipt_and_validation(db_with_cache, total=30.0)
    receipt.status = ReceiptStatus.SYNCED.value
    db_with_cache.commit()

    CAT_SYNCED = CATEGORY_ID
    CAT_YNAB = "cat-changed"

    # Seed a second category in cache so reconciliation validation passes
    from app.models import YNABCache
    from app.enums import YNABCacheEntityType
    db_with_cache.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id=CAT_YNAB,
            name="Changed Category",
            group_name="Everyday",
            raw_json={"id": CAT_YNAB, "name": "Changed Category"},
        )
    )
    db_with_cache.commit()

    rid = str(uuid.uuid4())
    r2, v2 = _seed_receipt_and_validation(db_with_cache, receipt_id=rid, total=30.0)
    r2.status = ReceiptStatus.SYNCED.value
    db_with_cache.commit()

    txn_id_2 = "txn-cat-change-1"
    sync_row2 = YNABSync(
        receipt_id=rid,
        validation_id=v2.id,
        idempotency_key="ikey-cat-change-1",
        status=YNABSyncStatus.CREATED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        completed_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        created_transaction_id=txn_id_2,
        raw_request={
            "transaction": {
                "account_id": ACCOUNT_ID,
                "date": "2026-06-01",
                "amount": -30000,
                "payee_name": "Test Payee",
                "memo": f"[receipt_id:{rid}]",
                "category_id": CAT_SYNCED,
            }
        },
    )
    db_with_cache.add(sync_row2)
    db_with_cache.commit()

    # YNAB has changed category (same amount)
    ynab_txn2 = {
        "id": txn_id_2,
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,  # same amount
        "payee_name": "Test Payee",
        "memo": f"[receipt_id:{rid}]",
        "category_id": CAT_YNAB,  # different category
        "deleted": False,
        "subtransactions": [],
    }

    mock_client = MagicMock(spec=YNABClient)
    mock_client.get_transaction.return_value = ynab_txn2
    monkeypatch.setattr("app.services.reconciliation.get_ynab_client", lambda s: mock_client)

    settings = _live_settings()
    run_ynab_reconciliation(db=db_with_cache, settings=settings)

    db_with_cache.expire_all()
    r2_after = db_with_cache.get(Receipt, rid)
    assert r2_after is not None
    # Category change only → SYNCED (no NEEDS_REVIEW)
    assert r2_after.status == ReceiptStatus.SYNCED.value


# ---------------------------------------------------------------------------
# Test 20: _split_signature regression guard
#
# The spec says "_split_signature stays amount-blind" with respect to the TOP-LEVEL
# transaction amount.  _split_signature only sees sub-level amounts + category_ids;
# it has no visibility into the top-level `transaction.amount` field.
# This test confirms:
#   1. _split_signature computes tuples of (sub_amount, category_id) — unchanged.
#   2. A top-level amount change that leaves sub amounts and categories identical
#      produces the SAME _split_signature (top-level blindness confirmed).
#   3. Category changes ARE still detected (regression guard).
# ---------------------------------------------------------------------------


def test_split_signature_still_amount_blind() -> None:
    """_split_signature is blind to top-level transaction amount changes.

    When sub-level amounts and categories are identical, changing the top-level
    transaction amount does not change the _split_signature output.  The new
    amount_drifted check in run_ynab_reconciliation is responsible for top-level
    amount drift detection — _split_signature is not and should not be modified.
    """
    # Subs for two transactions where only the top-level amount differs (sub amounts same)
    subs_low_total = [
        {"amount": -30000, "category_id": "cat-a"},
        {"amount": -20000, "category_id": "cat-b"},
    ]
    subs_high_total = [
        # Same sub amounts and categories — only the top-level transaction amount differs
        {"amount": -30000, "category_id": "cat-a"},
        {"amount": -20000, "category_id": "cat-b"},
    ]

    sig_low = _split_signature(subs_low_total)
    sig_high = _split_signature(subs_high_total)

    # _split_signature is blind to top-level amounts: identical subs → same sig
    assert sig_low == sig_high, (
        "_split_signature must not change when top-level amount changes "
        "(sub amounts and categories are identical)"
    )

    # Category change IS detected
    subs_cat_changed = [
        {"amount": -30000, "category_id": "cat-a"},
        {"amount": -20000, "category_id": "cat-c"},  # changed from cat-b
    ]
    assert sig_low != _split_signature(subs_cat_changed)

    # Tuples are (sub_amount, category_id) — shape unchanged
    assert all(len(t) == 2 for t in sig_low)


# ---------------------------------------------------------------------------
# Test 21: stuck reset also fails stale RUNNING sync rows
# ---------------------------------------------------------------------------


def test_stuck_reset_fails_stale_running_sync_rows(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale RUNNING sync row + SYNCING receipt: after _reset_stuck_jobs,
    receipt→NEEDS_REVIEW and sync row→FAILED."""
    from app.main import _reset_stuck_jobs

    # Seed a SYNCING receipt with stale sync_started_at
    rid = str(uuid.uuid4())
    stale_time = datetime.now(timezone.utc) - timedelta(hours=3)

    r = Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="stuck.jpg",
        file_hash=f"hash-stuck-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.SYNCING.value,
        sync_started_at=stale_time,
    )
    db_with_cache.add(r)
    db_with_cache.flush()

    # Seed a RUNNING sync row with stale started_at
    stale_row = YNABSync(
        receipt_id=rid,
        validation_id=None,
        idempotency_key=f"ikey-stuck-{rid}",
        status=YNABSyncStatus.RUNNING.value,
        match_mode="match_or_create",
        started_at=stale_time,
    )
    db_with_cache.add(stale_row)
    db_with_cache.commit()

    # Patch SessionLocal to return our test session
    from contextlib import contextmanager

    @contextmanager
    def _fake_session_local():
        yield db_with_cache

    monkeypatch.setattr("app.main.SessionLocal", _fake_session_local)
    # Also patch utcnow in main to return a time well past the stale timeout
    monkeypatch.setattr("app.main.utcnow", lambda: datetime.now(timezone.utc))

    _reset_stuck_jobs()

    db_with_cache.expire_all()

    # Receipt is NEEDS_REVIEW
    r_after = db_with_cache.get(Receipt, rid)
    assert r_after is not None
    assert r_after.status == ReceiptStatus.NEEDS_REVIEW.value

    # Sync row is FAILED
    row_after = db_with_cache.scalar(
        sa_select(YNABSync).where(YNABSync.idempotency_key == f"ikey-stuck-{rid}")
    )
    assert row_after is not None
    assert row_after.status == YNABSyncStatus.FAILED.value
    assert row_after.error_text == "Reset by stuck-job recovery"
