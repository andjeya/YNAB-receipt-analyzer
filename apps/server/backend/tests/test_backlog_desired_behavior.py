"""Backlog: DESIRED behavior the app does not yet implement (plan B-01..B-06).

These encode product decisions / accounting-safe expectations that are NOT yet
built. They are intentionally NOT passing:

  * B-01, B-02 — a real code path exists, so we assert the DESIRED behavior and
    mark the test xfail(strict=False). When the behavior is implemented the test
    will XPASS, signalling it is time to drop the marker.
  * B-03..B-06 — no implementation surface exists yet (currency, multi-tender,
    cash-back, mixed purchase/return). These are skipped stubs that document the
    desired behavior and the policy reference; unskip them when the feature lands.

Keeping these in-tree (green via xfail/skip) makes the gaps visible without
breaking the suite. See the approved plan + session note in plans/2026/06/week-25.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select as sa_select

from app.config import Settings
from app.enums import ReceiptStatus, YNABSyncStatus
from app.models import Receipt, Validation, YNABSync
from app.services.duplicates import apply_semantic_duplicate_state
from app.services.ynab import make_idempotency_key, sync_receipt_to_ynab
from receipt_shared.ynab_client import YNABClient

BUDGET_ID = "test-budget-id"
ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-1"


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


# ===========================================================================
# B-01 — Deleted YNAB transaction on resync: flag for review, do NOT recreate
# Decision 2026-06-16. Current code (services/ynab.py _update_existing_transaction)
# CREATES a fresh transaction when the prior one is gone; desired = NEEDS_REVIEW.
# ===========================================================================


@pytest.mark.xfail(
    reason="Backlog B-01: resync against a deleted YNAB txn currently recreates; "
    "desired = flag NEEDS_REVIEW and do not recreate.",
    strict=False,
)
def test_b01_resync_against_deleted_txn_flags_review_not_recreate(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = str(uuid.uuid4())
    receipt = Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="b01.jpg",
        file_hash=f"hash-b01-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db_with_cache.add(receipt)
    db_with_cache.flush()

    validation = Validation(
        receipt_id=rid,
        version=1,
        source="user",
        payload={
            "payee_name": "Test Payee",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": 50.0,
            "category_id": CATEGORY_ID,
            "splits": [],
            "transaction_kind": "purchase",
        },
        is_valid=True,
        errors=[],
    )
    db_with_cache.add(validation)
    db_with_cache.flush()
    receipt.latest_validation_version = 1

    prior_row = YNABSync(
        receipt_id=rid,
        validation_id=validation.id,
        idempotency_key=make_idempotency_key(rid, validation.id, False, True) + "-prior",
        status=YNABSyncStatus.CREATED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        completed_at=datetime.now(timezone.utc) - timedelta(minutes=9),
        created_transaction_id="txn-gone",
        raw_request={
            "transaction": {
                "account_id": ACCOUNT_ID,
                "date": "2026-06-01",
                "amount": -50000,
                "payee_name": "Test Payee",
                "memo": "",
                "category_id": CATEGORY_ID,
            }
        },
    )
    db_with_cache.add(prior_row)
    db_with_cache.commit()

    # YNAB reports the prior transaction as deleted, and no exact match exists.
    client = MagicMock(spec=YNABClient)
    client.get_transaction.return_value = {"id": "txn-gone", "deleted": True}
    client.list_transactions_since.return_value = []
    client.create_transaction.return_value = {"id": "txn-recreated"}
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=rid,
        force_create=False,
        allow_update_match=True,
    )

    # DESIRED: no recreate; receipt flagged for human review.
    client.create_transaction.assert_not_called()
    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, rid)
    assert after is not None
    assert after.status == ReceiptStatus.NEEDS_REVIEW.value


# ===========================================================================
# B-02 — Time-less duplicate: surface a non-blocking near-match warning
# Decision 2026-06-16. Current code returns no signature (full bypass).
# ===========================================================================


@pytest.mark.xfail(
    reason="Backlog B-02: time-less payee/date/total match currently bypasses "
    "duplicate detection; desired = non-blocking near-match warning.",
    strict=False,
)
def test_b02_timeless_match_surfaces_near_match_warning(db_session: Any) -> None:
    def _receipt(rid: str) -> Receipt:
        return Receipt(
            id=rid,
            storage_key=f"receipts/{rid}.jpg",
            original_filename="b02.jpg",
            file_hash=f"hash-b02-{rid}",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=1024,
            status=ReceiptStatus.NEEDS_REVIEW.value,
        )

    payload = {
        "payee_name": "Costco",
        "transaction_date": "2026-06-01",
        "transaction_time": None,  # no time
        "total_amount": 89.21,
        "transaction_kind": "purchase",
    }

    first = _receipt("b02-1")
    second = _receipt("b02-2")
    db_session.add_all([first, second])
    db_session.flush()

    apply_semantic_duplicate_state(db_session, receipt=first, payload=payload)
    db_session.flush()
    res2 = apply_semantic_duplicate_state(db_session, receipt=second, payload=payload)

    # DESIRED: a near-match warning is raised (not a hard block, not a silent bypass).
    assert res2.near_match is True
    assert res2.duplicate_of_receipt_id is None  # not hard-blocked


# ===========================================================================
# B-03..B-06 — Not-yet-designed features. Skipped stubs describing desired
# behavior; unskip and implement assertions when the feature lands.
# ===========================================================================


@pytest.mark.skip(reason="Backlog B-03 (CUR-01/02): non-USD currency detection/blocking not implemented")
def test_b03_non_usd_receipt_blocks_sync() -> None:
    """Desired: a receipt whose currency differs from the YNAB budget currency must
    block sync (clear reason) until an explicit converted/confirmed amount exists.
    No silent single-currency sync. Requires a currency field + gate that do not
    exist yet."""


@pytest.mark.skip(reason="Backlog B-04 (CUR-03): multi-tender detection not implemented")
def test_b04_multi_tender_receipt_requires_review() -> None:
    """Desired: a receipt paid across multiple tenders (e.g. gift card + credit
    card) must either record only the amount charged to the mapped account or be
    forced to manual review — never a silent single-account sync of the full total."""


@pytest.mark.skip(reason="Backlog B-05 (DISC-07): cash-back detection not implemented")
def test_b05_cash_back_not_silently_booked_as_spend() -> None:
    """Desired: register cash-back must not be silently booked as spending. Block /
    manual review until an explicit split/transfer model exists."""


@pytest.mark.skip(reason="Backlog B-06 (RET-08/09): mixed purchase+return handling not implemented")
def test_b06_mixed_purchase_return_receipt() -> None:
    """Desired: a receipt mixing purchases and returns is modeled as a purchase
    whose returned items reduce the net total (credits), or forced to manual review
    if it cannot reconcile. A net-zero receipt blocks sync (total must be > 0)."""
