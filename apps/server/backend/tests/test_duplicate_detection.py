from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.receipts import confirm_duplicate_receipt, override_duplicate_receipt, save_draft, sync_receipt
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import Base, Receipt, Validation, YNABCache
from app.schemas import DuplicateOverrideRequest, SaveDraftRequest, SyncRequest
from app.services.duplicates import apply_semantic_duplicate_state, normalize_payee_key, normalize_total_cents, normalize_transaction_time


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_cache_entities(db: Session, budget_id: str) -> None:
    db.add_all(
        [
            YNABCache(
                budget_id=budget_id,
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-1",
                name="Groceries",
                group_name="Everyday",
                raw_json={"id": "cat-1", "name": "Groceries"},
            ),
            YNABCache(
                budget_id=budget_id,
                entity_type=YNABCacheEntityType.ACCOUNT.value,
                entity_id="acct-1",
                name="Checking",
                group_name=None,
                raw_json={"id": "acct-1", "name": "Checking"},
            ),
        ]
    )


def _seed_receipt(db: Session, *, receipt_id: str, file_hash: str) -> Receipt:
    receipt = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename=f"{receipt_id}.jpg",
        file_hash=file_hash,
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=123,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def _payload(*, payee: str, total: float, date_text: str = "2026-03-01", time_text: str | None = "09:41") -> dict[str, object]:
    return {
        "payee_name": payee,
        "account_id": "acct-1",
        "transaction_date": date_text,
        "transaction_time": time_text,
        "memo": "Imported",
        "total_amount": total,
        "category_id": "cat-1",
        "splits": [],
    }


def test_semantic_normalization_helpers_are_deterministic() -> None:
    assert normalize_payee_key("Trader Joe's") == "trader joes"
    assert normalize_payee_key("  trader   joes ") == "trader joes"
    assert normalize_total_cents("$12.30") == 1230
    assert normalize_total_cents(12.3) == 1230
    assert normalize_transaction_time("09:41:59") == "09:41"


def test_save_draft_flags_semantic_duplicate_and_blocks_sync() -> None:
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        first = _seed_receipt(
            db,
            receipt_id="11111111-2222-4333-8444-555555555555",
            file_hash="hash-dup-1",
        )
        second = _seed_receipt(
            db,
            receipt_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            file_hash="hash-dup-2",
        )

        first_response = save_draft(
            receipt_id=first.id,
            request=SaveDraftRequest(payload=_payload(payee="Trader Joe's", total=12.30), source="user"),
            db=db,
            settings=settings,
        )
        second_response = save_draft(
            receipt_id=second.id,
            request=SaveDraftRequest(payload=_payload(payee="trader joes", total=12.3), source="user"),
            db=db,
            settings=settings,
        )

        db.refresh(second)

        assert first_response.can_sync is True
        assert second_response.can_sync is False
        assert second.status == ReceiptStatus.DUPLICATE_REVIEW.value
        assert second.duplicate_of_receipt_id == first.id
        assert second.semantic_total_cents == 1230
        assert second.semantic_transaction_time == "09:41"


def test_duplicate_detection_requires_matching_time() -> None:
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        first = _seed_receipt(
            db,
            receipt_id="22222222-3333-4444-8555-666666666666",
            file_hash="hash-time-1",
        )
        second = _seed_receipt(
            db,
            receipt_id="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            file_hash="hash-time-2",
        )

        save_draft(
            receipt_id=first.id,
            request=SaveDraftRequest(payload=_payload(payee="Costco", total=89.21, time_text="14:10"), source="user"),
            db=db,
            settings=settings,
        )
        second_response = save_draft(
            receipt_id=second.id,
            request=SaveDraftRequest(payload=_payload(payee="costco", total=89.21, time_text="14:12"), source="user"),
            db=db,
            settings=settings,
        )

        db.refresh(second)

        assert second_response.can_sync is True
        assert second.status == ReceiptStatus.NEEDS_REVIEW.value
        assert second.duplicate_of_receipt_id is None


def test_override_duplicate_signature_allows_editing_without_retrigger() -> None:
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        first = _seed_receipt(
            db,
            receipt_id="33333333-4444-4555-8666-777777777777",
            file_hash="hash-override-1",
        )
        second = _seed_receipt(
            db,
            receipt_id="cccccccc-dddd-4eee-8fff-000000000000",
            file_hash="hash-override-2",
        )

        save_draft(
            receipt_id=first.id,
            request=SaveDraftRequest(payload=_payload(payee="Trader Joe's", total=12.30), source="user"),
            db=db,
            settings=settings,
        )
        save_draft(
            receipt_id=second.id,
            request=SaveDraftRequest(payload=_payload(payee="trader joes", total=12.3), source="user"),
            db=db,
            settings=settings,
        )

        override_response = override_duplicate_receipt(
            receipt_id=second.id,
            request=DuplicateOverrideRequest(confirmed=True),
            db=db,
        )
        second_response = save_draft(
            receipt_id=second.id,
            request=SaveDraftRequest(payload=_payload(payee="Trader Joes", total=12.30), source="user"),
            db=db,
            settings=settings,
        )

        db.refresh(second)

        assert override_response.status == ReceiptStatus.NEEDS_REVIEW.value
        assert second.duplicate_override_signature is not None
        assert second_response.can_sync is True
        assert second.status == ReceiptStatus.NEEDS_REVIEW.value
        assert second.duplicate_of_receipt_id is None


def test_duplicate_review_rows_are_not_selected_as_canonical_targets() -> None:
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        original = _seed_receipt(
            db,
            receipt_id="abababab-1111-4222-8333-cccccccccccc",
            file_hash="hash-canonical-1",
        )
        incoming = _seed_receipt(
            db,
            receipt_id="dededede-4444-4555-8666-ffffffffffff",
            file_hash="hash-canonical-2",
        )

        save_draft(
            receipt_id=original.id,
            request=SaveDraftRequest(payload=_payload(payee="Trader Joe's", total=12.30), source="user"),
            db=db,
            settings=settings,
        )
        save_draft(
            receipt_id=incoming.id,
            request=SaveDraftRequest(payload=_payload(payee="trader joes", total=12.3), source="user"),
            db=db,
            settings=settings,
        )

        original_response = save_draft(
            receipt_id=original.id,
            request=SaveDraftRequest(payload=_payload(payee="Trader Joes", total=12.30), source="user"),
            db=db,
            settings=settings,
        )

        db.refresh(original)
        db.refresh(incoming)

        assert incoming.status == ReceiptStatus.DUPLICATE_REVIEW.value
        assert incoming.duplicate_of_receipt_id == original.id
        assert original_response.can_sync is True
        assert original.status == ReceiptStatus.NEEDS_REVIEW.value
        assert original.duplicate_of_receipt_id is None


def test_confirm_duplicate_hard_deletes_incoming_receipt_and_scan(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, object_store_root=str(tmp_path))

    with _memory_session() as db:
        kept = _seed_receipt(
            db,
            receipt_id="44444444-5555-4666-8777-888888888888",
            file_hash="hash-confirm-keep",
        )
        duplicate = _seed_receipt(
            db,
            receipt_id="dddddddd-eeee-4fff-8000-111111111111",
            file_hash="hash-confirm-delete",
        )
        duplicate.status = ReceiptStatus.DUPLICATE_REVIEW.value
        duplicate.duplicate_of_receipt_id = kept.id
        db.commit()

        duplicate_file = tmp_path / duplicate.storage_key
        duplicate_file.parent.mkdir(parents=True, exist_ok=True)
        duplicate_file.write_bytes(b"fake-scan")

        response = confirm_duplicate_receipt(
            receipt_id=duplicate.id,
            db=db,
            settings=settings,
        )

        assert response.deleted_receipt_id == duplicate.id
        assert response.kept_receipt_id == kept.id
        assert db.get(Receipt, duplicate.id) is None
        assert db.get(Receipt, kept.id) is not None
        assert duplicate_file.exists() is False


def test_sync_endpoint_blocks_semantic_duplicate_with_409() -> None:
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        first = _seed_receipt(
            db,
            receipt_id="55555555-6666-4777-8888-999999999999",
            file_hash="hash-sync-1",
        )
        second = _seed_receipt(
            db,
            receipt_id="eeeeeeee-ffff-4000-8111-222222222222",
            file_hash="hash-sync-2",
        )

        save_draft(
            receipt_id=first.id,
            request=SaveDraftRequest(payload=_payload(payee="Trader Joe's", total=12.30), source="user"),
            db=db,
            settings=settings,
        )
        save_draft(
            receipt_id=second.id,
            request=SaveDraftRequest(payload=_payload(payee="trader joes", total=12.3), source="user"),
            db=db,
            settings=settings,
        )

        # ynab_sync_enabled=True so the kill-switch does not fire before the duplicate check.
        sync_settings = Settings(_env_file=None, ynab_budget_id="budget-1", ynab_sync_enabled=True)
        with pytest.raises(HTTPException) as exc_info:
            sync_receipt(
                receipt_id=second.id,
                request=SyncRequest(),
                db=db,
                settings=sync_settings,
            )

        assert exc_info.value.status_code == 409
        assert isinstance(exc_info.value.detail, dict)
        assert exc_info.value.detail.get("code") == "duplicate_receipt"


# ---------------------------------------------------------------------------
# Task D — Kind-aware near-duplicate messaging
# ---------------------------------------------------------------------------

def _seed_receipt_with_validation(
    db: Session,
    *,
    receipt_id: str,
    file_hash: str,
    payload: dict,
) -> Receipt:
    """Seed a receipt AND a Validation row so that _latest_kind_for_receipt can read its kind."""
    receipt = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename=f"{receipt_id}.jpg",
        file_hash=file_hash,
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=123,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.flush()  # get receipt.id into the session
    validation = Validation(
        receipt_id=receipt_id,
        version=1,
        payload=payload,
        source="user",
        is_valid=True,
        errors=None,
    )
    db.add(validation)
    db.commit()
    db.refresh(receipt)
    return receipt


def test_kind_differing_match_is_downgraded_to_near_match_not_blocked() -> None:
    """A refund that matches a purchase on signature must not be blocked as a duplicate."""
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    purchase_payload = _payload(payee="Trader Joe's", total=12.30, time_text="09:41")
    purchase_payload["transaction_kind"] = "purchase"

    refund_payload = _payload(payee="Trader Joe's", total=12.30, time_text="09:41")
    refund_payload["transaction_kind"] = "refund"

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        purchase_receipt = _seed_receipt_with_validation(
            db,
            receipt_id="aaaaaaaa-0001-4000-8000-000000000001",
            file_hash="hash-kind-purchase",
            payload=purchase_payload,
        )
        # Seed semantic fields on the purchase receipt so signature is present
        apply_semantic_duplicate_state(db, receipt=purchase_receipt, payload=purchase_payload)
        db.commit()

        # Now save a refund with the same payee/date/time/total
        refund_receipt = _seed_receipt(
            db,
            receipt_id="aaaaaaaa-0001-4000-8000-000000000002",
            file_hash="hash-kind-refund",
        )
        result = apply_semantic_duplicate_state(db, receipt=refund_receipt, payload=refund_payload)
        db.commit()

        db.refresh(refund_receipt)

        # Must NOT be flagged as DUPLICATE_REVIEW — it's a different kind
        assert refund_receipt.status != ReceiptStatus.DUPLICATE_REVIEW.value
        assert refund_receipt.duplicate_of_receipt_id is None
        assert result.near_match is True
        assert result.near_match_reason is not None
        assert "Near-match" in result.near_match_reason


def test_same_kind_match_still_blocks_as_duplicate() -> None:
    """Two purchases with same signature must still be treated as hard duplicates."""
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    purchase_payload_1 = _payload(payee="Costco", total=50.00, time_text="10:00")
    purchase_payload_1["transaction_kind"] = "purchase"

    purchase_payload_2 = _payload(payee="costco", total=50.00, time_text="10:00")
    purchase_payload_2["transaction_kind"] = "purchase"

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        first = _seed_receipt_with_validation(
            db,
            receipt_id="bbbbbbbb-0001-4000-8000-000000000001",
            file_hash="hash-same-kind-1",
            payload=purchase_payload_1,
        )
        from app.services.duplicates import apply_semantic_duplicate_state
        apply_semantic_duplicate_state(db, receipt=first, payload=purchase_payload_1)
        db.commit()

        second = _seed_receipt(
            db,
            receipt_id="bbbbbbbb-0001-4000-8000-000000000002",
            file_hash="hash-same-kind-2",
        )
        result = apply_semantic_duplicate_state(db, receipt=second, payload=purchase_payload_2)
        db.commit()

        db.refresh(second)

        assert second.status == ReceiptStatus.DUPLICATE_REVIEW.value
        assert second.duplicate_of_receipt_id == first.id
        assert result.near_match is False


def test_mixed_pool_blocks_when_any_same_kind_match_exists() -> None:
    """If the matched pool contains a different-kind receipt FIRST but a same-kind
    receipt later, the same-kind match must still block (no slip-through)."""
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    refund_payload = _payload(payee="Trader Joe's", total=12.30, time_text="09:41")
    refund_payload["transaction_kind"] = "refund"
    purchase_payload = _payload(payee="Trader Joe's", total=12.30, time_text="09:41")
    purchase_payload["transaction_kind"] = "purchase"

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        # Seed a DIFFERENT-kind (refund) match first (earlier ingested_at)...
        refund_receipt = _seed_receipt_with_validation(
            db,
            receipt_id="dddddddd-0001-4000-8000-000000000001",
            file_hash="hash-mixed-refund",
            payload=refund_payload,
        )
        apply_semantic_duplicate_state(db, receipt=refund_receipt, payload=refund_payload)
        db.commit()
        # ...then a SAME-kind (purchase) match.
        purchase_receipt = _seed_receipt_with_validation(
            db,
            receipt_id="dddddddd-0001-4000-8000-000000000002",
            file_hash="hash-mixed-purchase",
            payload=purchase_payload,
        )
        apply_semantic_duplicate_state(db, receipt=purchase_receipt, payload=purchase_payload)
        db.commit()

        # A second purchase arrives — the pool is [refund, purchase]; it MUST block
        # on the same-kind purchase, not be downgraded on the refund's differing kind.
        incoming = _seed_receipt(
            db,
            receipt_id="dddddddd-0001-4000-8000-000000000003",
            file_hash="hash-mixed-incoming",
        )
        result = apply_semantic_duplicate_state(db, receipt=incoming, payload=purchase_payload)
        db.commit()
        db.refresh(incoming)

        assert incoming.status == ReceiptStatus.DUPLICATE_REVIEW.value
        assert incoming.duplicate_of_receipt_id == purchase_receipt.id
        assert result.near_match is False


def test_near_match_result_has_near_match_false_by_default() -> None:
    """DuplicateCheckResult.near_match defaults to False (no regression in normal path)."""
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = _seed_receipt(
            db,
            receipt_id="cccccccc-0001-4000-8000-000000000001",
            file_hash="hash-no-match",
        )
        payload = _payload(payee="Target", total=20.00, time_text="11:00")
        result = apply_semantic_duplicate_state(db, receipt=receipt, payload=payload)
        assert result.near_match is False
