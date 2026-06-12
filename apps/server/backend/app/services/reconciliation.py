from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameEventType, ReceiptStatus, YNABSyncStatus
from app.models import (
    GameEvent,
    Receipt,
    ReceiptCorrection,
    Validation,
    YNABReconciliationRun,
    YNABSync,
)
from app.services.correctness import add_fire, get_or_create_correctness_state
from app.services.incidents import record_incident
from app.services.validation import validate_payload
from app.services.ynab import get_cached_reference_data, get_ynab_client
from app.utils import utcnow
from receipt_shared.money import dollars_to_milliunits

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _active_subtransactions(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    return [sub for sub in transaction.get("subtransactions", []) if not sub.get("deleted")]


def _split_signature(subtransactions: list[dict[str, Any]]) -> list[tuple[int, str]]:
    signature: list[tuple[int, str]] = []
    for sub in subtransactions:
        signature.append(
            (
                int(sub.get("amount", 0)),
                str(sub.get("category_id") or ""),
            )
        )
    return sorted(signature)


def _normalize_category_and_splits(
    *,
    category_id: str | None,
    split_signature: list[tuple[int, str]],
    total_amount_milliunits: int,
) -> tuple[str | None, list[tuple[int, str]]]:
    normalized_category_id = str(category_id or "") or None

    # A single split with the full amount and same category is semantically
    # equivalent to a single-category transaction.
    if len(split_signature) == 1:
        split_amount, split_category = split_signature[0]
        if (
            split_category
            and abs(split_amount) == abs(int(total_amount_milliunits))
            and (normalized_category_id is None or normalized_category_id == split_category)
        ):
            return split_category, []

    # In split mode, top-level category is not semantically meaningful.
    if split_signature:
        return None, split_signature

    return normalized_category_id, split_signature


def _sync_payload_signature(transaction_payload: dict[str, Any]) -> tuple[str | None, list[tuple[int, str]]]:
    subtransactions_raw = transaction_payload.get("subtransactions", [])
    subtransactions = subtransactions_raw if isinstance(subtransactions_raw, list) else []
    split_signature = _split_signature(subtransactions)
    return _normalize_category_and_splits(
        category_id=str(transaction_payload.get("category_id") or "") or None,
        split_signature=split_signature,
        total_amount_milliunits=int(transaction_payload.get("amount", 0)),
    )


def _ynab_transaction_signature(transaction: dict[str, Any]) -> tuple[str | None, list[tuple[int, str]]]:
    active_subtransactions = _active_subtransactions(transaction)
    split_signature = _split_signature(active_subtransactions)
    return _normalize_category_and_splits(
        category_id=str(transaction.get("category_id") or "") or None,
        split_signature=split_signature,
        total_amount_milliunits=int(transaction.get("amount", 0)),
    )


def _latest_successful_sync_rows(db: Session, since_at: datetime) -> list[YNABSync]:
    rows = list(
        db.scalars(
            select(YNABSync)
            .where(
                YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
                YNABSync.completed_at.is_not(None),
                YNABSync.completed_at >= since_at,
            )
            .order_by(YNABSync.completed_at.desc(), YNABSync.id.desc())
        )
    )

    by_receipt: dict[str, YNABSync] = {}
    for row in rows:
        if row.receipt_id not in by_receipt:
            by_receipt[row.receipt_id] = row
    return list(by_receipt.values())


def _dollars_from_milliunits(value: int) -> float:
    return float(abs(Decimal(int(value)) / Decimal("1000")))


def _build_corrected_payload(
    prior_payload: dict[str, Any],
    ynab_transaction: dict[str, Any],
) -> dict[str, Any]:
    next_payload = dict(prior_payload)

    subtransactions = _active_subtransactions(ynab_transaction)
    if subtransactions:
        sub_amounts = [int(sub.get("amount", 0)) for sub in subtransactions]
        has_positive = any(a > 0 for a in sub_amounts)
        has_negative = any(a < 0 for a in sub_amounts)
        if has_positive and has_negative:
            # Mixed inflow/outflow splits — cannot safely adopt; log and return prior payload unchanged.
            logger.warning(
                "Reconciliation: YNAB transaction has mixed inflow/outflow splits; "
                "skipping payload update to avoid corruption. "
                "transaction_id=%s",
                ynab_transaction.get("id"),
            )
            return prior_payload
        total_amount_mu = int(ynab_transaction.get("amount", 0))
        ynab_kind = "refund" if total_amount_mu > 0 else "purchase"
        next_payload["transaction_kind"] = ynab_kind
        next_payload["category_id"] = None
        next_payload["splits"] = [
            {
                "category_id": str(sub.get("category_id") or ""),
                "amount": _dollars_from_milliunits(int(sub.get("amount", 0))),
                "memo": str(sub.get("memo") or ""),
            }
            for sub in subtransactions
        ]
    else:
        total_amount_mu = int(ynab_transaction.get("amount", 0))
        ynab_kind = "refund" if total_amount_mu > 0 else "purchase"
        next_payload["transaction_kind"] = ynab_kind
        next_payload["category_id"] = str(ynab_transaction.get("category_id") or "")
        next_payload["splits"] = []

    if ynab_transaction.get("payee_name"):
        next_payload["payee_name"] = str(ynab_transaction.get("payee_name") or "")
    if ynab_transaction.get("date"):
        next_payload["transaction_date"] = str(ynab_transaction.get("date") or "")

    next_payload["memo"] = str(ynab_transaction.get("memo") or next_payload.get("memo") or "")
    next_payload["total_amount"] = _dollars_from_milliunits(int(ynab_transaction.get("amount", 0)))
    return next_payload


def _latest_validation(db: Session, receipt_id: str) -> Validation | None:
    return db.scalar(
        select(Validation)
        .where(Validation.receipt_id == receipt_id)
        .order_by(Validation.version.desc())
        .limit(1)
    )


_AMOUNT_DRIFT_REASON = (
    "Reconciliation detected a YNAB amount that differs from the synced payload. "
    "The local validation has been pulled to match the YNAB amount (YNAB is source of truth); "
    "the YNAB transaction was not modified. Review and re-sync if the amount is incorrect."
)


def _apply_corrected_validation(
    db: Session,
    *,
    receipt: Receipt,
    ynab_transaction: dict[str, Any],
    settings: Settings,
    amount_drifted: bool = False,
) -> None:
    """Build and persist a corrected validation pulled from the current YNAB transaction.

    TASK 5b — amount_drifted flag:
    When amount_drifted=True the YNAB amount differs from the payload that was synced.
    We pull the corrected validation (YNAB is source of truth) and set receipt status to
    NEEDS_REVIEW so the user is prompted to review the amount discrepancy.  We do NOT push
    the synced amount back to YNAB — the amount update path would be a separate sync.
    """
    latest_validation = _latest_validation(db, receipt.id)
    if latest_validation is None:
        return

    reference_data = get_cached_reference_data(db, settings)
    allowed_category_ids = {item.entity_id for item in reference_data["categories"]}
    allowed_account_ids = {item.entity_id for item in reference_data["accounts"]}

    corrected_payload = _build_corrected_payload(latest_validation.payload, ynab_transaction)
    normalized_payload, is_valid, errors = validate_payload(
        corrected_payload,
        allowed_category_ids=allowed_category_ids,
        allowed_account_ids=allowed_account_ids,
    )
    if not is_valid:
        logger.warning(
            "Reconciliation produced invalid payload receipt_id=%s errors=%s",
            receipt.id,
            errors,
        )
        return

    def _insert_validation() -> None:
        next_version = receipt.latest_validation_version + 1
        db.add(
            Validation(
                receipt_id=receipt.id,
                version=next_version,
                source="reconciliation",
                payload=normalized_payload,
                is_valid=True,
                errors=[],
            )
        )
        receipt.latest_validation_version = next_version

    try:
        # Use a savepoint so a version collision doesn't invalidate the outer transaction.
        # The unique constraint on (receipt_id, version) is the safety net against races.
        with db.begin_nested():
            _insert_validation()
    except IntegrityError:
        db.refresh(receipt)
        _insert_validation()

    payee_name = str(normalized_payload.get("payee_name") or "").strip()
    receipt.display_payee_name = payee_name or None
    receipt.display_total_milliunits = dollars_to_milliunits(normalized_payload.get("total_amount", 0), outflow=False)
    if normalized_payload.get("transaction_date"):
        receipt.display_receipt_date = datetime.fromisoformat(str(normalized_payload["transaction_date"])).date()

    if amount_drifted:
        # TASK 5b: amount drift — YNAB amount differs; pulled to YNAB value.
        # Set NEEDS_REVIEW so the user is prompted; do NOT push the old amount back.
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        receipt.status_reason = _AMOUNT_DRIFT_REASON
    else:
        # Category/split change only — keep receipts synced; correction metadata and
        # iconography communicate what changed without forcing a review loop.
        receipt.status = ReceiptStatus.SYNCED.value
        receipt.status_reason = None


def _split_note(subtransactions: list[dict[str, Any]]) -> str:
    if not subtransactions:
        return "[empty split]"
    parts: list[str] = []
    for sub in sorted(
        subtransactions,
        key=lambda item: (
            str(item.get("category_id") or ""),
            int(item.get("amount", 0)),
            str(item.get("memo") or ""),
        ),
    ):
        category_id = str(sub.get("category_id") or "[none]")
        amount = _dollars_from_milliunits(int(sub.get("amount", 0)))
        parts.append(f"{category_id}:${amount:.2f}")
    preview = ", ".join(parts[:4])
    if len(parts) > 4:
        preview = f"{preview}, +{len(parts) - 4} more"
    return preview


def _category_note(sync_payload: dict[str, Any], ynab_transaction: dict[str, Any]) -> str:
    synced_category = str(sync_payload.get("category_id") or "")
    corrected_category = str(ynab_transaction.get("category_id") or "")
    synced_splits = sync_payload.get("subtransactions", [])
    corrected_splits = _active_subtransactions(ynab_transaction)
    synced_is_split = isinstance(synced_splits, list) and len(synced_splits) > 0
    corrected_is_split = len(corrected_splits) > 0

    if synced_is_split or corrected_is_split:
        synced_desc = f"split [{_split_note(synced_splits if isinstance(synced_splits, list) else [])}]"
        corrected_desc = (
            f"split [{_split_note(corrected_splits)}]"
            if corrected_is_split
            else (corrected_category or "[none]")
        )
        return f"Category/split synced as {synced_desc}, corrected in YNAB to {corrected_desc}"

    return f"Category synced as {synced_category or '[none]'}, corrected in YNAB to {corrected_category or '[none]'}"


def _correction_signature(sync_payload: dict[str, Any], ynab_transaction: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(str(_sync_payload_signature(sync_payload)).encode("utf-8"))
    digest.update(str(_ynab_transaction_signature(ynab_transaction)).encode("utf-8"))
    return digest.hexdigest()


def _existing_correction(
    db: Session,
    receipt_id: str,
    signature: str,
) -> bool:
    row = db.scalar(
        select(ReceiptCorrection.id)
        .where(
            ReceiptCorrection.receipt_id == receipt_id,
            ReceiptCorrection.note.like(f"%sig={signature}%"),
        )
        .limit(1)
    )
    return row is not None


def _successful_sync_after(db: Session, receipt_id: str, detected_at: datetime) -> datetime | None:
    return db.scalar(
        select(YNABSync.completed_at)
        .where(
            YNABSync.receipt_id == receipt_id,
            YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
            YNABSync.completed_at.is_not(None),
            YNABSync.completed_at > detected_at,
        )
        .order_by(YNABSync.completed_at.asc())
        .limit(1)
    )


def run_ynab_reconciliation(db: Session, settings: Settings) -> dict[str, Any]:
    if not settings.ynab_budget_id:
        raise ValueError("YNAB_BUDGET_ID is not configured")

    started_at = utcnow()
    run = YNABReconciliationRun(
        started_at=started_at,
        lookback_days=settings.ynab_reconciliation_lookback_days,
    )
    db.add(run)
    db.flush()

    since_at = started_at - timedelta(days=settings.ynab_reconciliation_lookback_days)
    client = get_ynab_client(settings)

    scanned_receipts = 0
    detected_mistakes = 0
    correction_receipts: list[str] = []
    fires_added_total = 0
    waters_spent_total = 0
    burns_triggered_total = 0

    for sync_row in _latest_successful_sync_rows(db, since_at):
        receipt = db.get(Receipt, sync_row.receipt_id)
        if receipt is None:
            continue
        transaction_id = sync_row.created_transaction_id or sync_row.matched_transaction_id
        if not transaction_id:
            continue

        transaction_payload = (sync_row.raw_request or {}).get("transaction")
        if not isinstance(transaction_payload, dict):
            continue

        scanned_receipts += 1

        try:
            ynab_transaction = client.get_transaction(settings.ynab_budget_id, transaction_id)
        except Exception as exc:
            logger.warning(
                "Reconciliation fetch failed receipt_id=%s transaction_id=%s error=%s",
                receipt.id,
                transaction_id,
                exc,
            )
            continue

        if ynab_transaction.get("deleted"):
            continue

        synced_signature = _sync_payload_signature(transaction_payload)
        current_signature = _ynab_transaction_signature(ynab_transaction)

        # TASK 5b: detect top-level amount drift independently of category/split changes.
        # _split_signature is intentionally amount-blind (it only tracks category_id per sub).
        # Amount drift means the synced transaction amount differs from what YNAB now reports.
        amount_drifted = int(transaction_payload.get("amount", 0)) != int(ynab_transaction.get("amount", 0))

        if synced_signature == current_signature and not amount_drifted:
            continue

        signature_hash = _correction_signature(transaction_payload, ynab_transaction)
        if _existing_correction(db, receipt.id, signature_hash):
            continue

        detected_at = utcnow()
        note = f"{_category_note(transaction_payload, ynab_transaction)} | sig={signature_hash}"
        logger.info(
            "Reconciliation mismatch receipt_id=%s transaction_id=%s synced_sig=%s current_sig=%s amount_drifted=%s",
            receipt.id,
            transaction_id,
            synced_signature,
            current_signature,
            amount_drifted,
        )

        db.add(
            ReceiptCorrection(
                receipt_id=receipt.id,
                ynab_transaction_id=transaction_id,
                synced_category_id=str(transaction_payload.get("category_id") or "") or None,
                corrected_category_id=str(ynab_transaction.get("category_id") or "") or None,
                synced_splits_json=transaction_payload.get("subtransactions", []),
                corrected_splits_json=_active_subtransactions(ynab_transaction),
                detected_at=detected_at,
                expires_at=detected_at + timedelta(days=settings.correction_fade_days),
                note=note,
            )
        )
        db.add(
            GameEvent(
                event_type=GameEventType.CORRECTION_DETECTED.value,
                receipt_id=receipt.id,
                payload_json={
                    "signature": signature_hash,
                    "synced_category_id": str(transaction_payload.get("category_id") or "") or None,
                    "corrected_category_id": str(ynab_transaction.get("category_id") or "") or None,
                    "amount_drifted": amount_drifted,
                },
                idempotency_key=f"correction_detected:{receipt.id}:{signature_hash}",
                created_at=detected_at,
            )
        )

        fire_result = add_fire(
            db,
            settings,
            units=1,
            receipt_id=receipt.id,
            idempotency_key=f"fire:reconciliation:{receipt.id}:{signature_hash}",
            reason="ynab_amount_drifted" if amount_drifted else "ynab_category_or_split_changed",
            created_at=detected_at,
        )
        fires_added_total += int(fire_result.get("fires_added", 0))
        waters_spent_total += int(fire_result.get("waters_spent", 0))
        burns_triggered_total += int(fire_result.get("burns_triggered", 0))

        _apply_corrected_validation(
            db,
            receipt=receipt,
            ynab_transaction=ynab_transaction,
            settings=settings,
            amount_drifted=amount_drifted,
        )

        detected_mistakes += 1
        correction_receipts.append(receipt.id)

    for correction in db.scalars(select(ReceiptCorrection)):
        resynced_at = _successful_sync_after(db, correction.receipt_id, correction.detected_at)
        if resynced_at is not None:
            correction.resynced_at = _as_utc(resynced_at)
            correction.resync_penalty_applied = True

    applied_penalties = 0

    correctness = get_or_create_correctness_state(db)
    correctness.last_reconciled_at = utcnow()

    run.scanned_receipts = scanned_receipts
    run.detected_mistakes = detected_mistakes
    run.applied_penalties = applied_penalties
    run.completed_at = utcnow()
    run.details_json = {
        "receipt_ids": correction_receipts,
        "window_start": since_at.isoformat(),
        "window_end": started_at.isoformat(),
    }

    logger.info(
        "YNAB reconciliation run_id=%s scanned=%s detected=%s penalties=%s",
        run.id,
        scanned_receipts,
        detected_mistakes,
        applied_penalties,
    )

    if detected_mistakes > 0 or waters_spent_total > 0 or burns_triggered_total > 0:
        if burns_triggered_total > 0:
            severity = "warning"
            title = "A week burned"
        elif waters_spent_total > 0:
            severity = "warning"
            title = "Fires Added and Water Spent"
        else:
            severity = "warning"
            title = "YNAB Corrections Detected"

        message_parts = [
            f"{detected_mistakes} transaction{'s' if detected_mistakes != 1 else ''} corrected in YNAB.",
            f"{fires_added_total} fire{'s' if fires_added_total != 1 else ''} added.",
        ]
        if waters_spent_total > 0:
            message_parts.append(f"{waters_spent_total} water spent to prevent a week burn.")
        if burns_triggered_total > 0:
            message_parts.append(
                f"{'A week' if burns_triggered_total == 1 else str(burns_triggered_total) + ' weeks'} burned."
            )

        record_incident(
            db,
            incident_type="reconciliation_summary",
            severity=severity,
            title=title,
            message=" ".join(message_parts),
            details={
                "run_id": run.id,
                "scanned_receipts": scanned_receipts,
                "detected_mistakes": detected_mistakes,
                "fires_added": fires_added_total,
                "waters_spent": waters_spent_total,
                "burns_triggered": burns_triggered_total,
                "receipt_ids": correction_receipts,
            },
            idempotency_key=f"incident:reconciliation:{run.id}",
            created_at=run.completed_at,
        )

    return {
        "run_id": run.id,
        "scanned_receipts": scanned_receipts,
        "detected_mistakes": detected_mistakes,
        "applied_penalties": applied_penalties,
        "fires_added": fires_added_total,
        "waters_spent": waters_spent_total,
        "burns_triggered": burns_triggered_total,
    }
