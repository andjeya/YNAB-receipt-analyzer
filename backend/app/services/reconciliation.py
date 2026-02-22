from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
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

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _active_subtransactions(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    return [sub for sub in transaction.get("subtransactions", []) if not sub.get("deleted")]


def _split_signature(subtransactions: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    signature: list[tuple[int, str, str]] = []
    for sub in subtransactions:
        signature.append(
            (
                int(sub.get("amount", 0)),
                str(sub.get("category_id") or ""),
                str(sub.get("memo") or ""),
            )
        )
    return sorted(signature)


def _sync_payload_signature(transaction_payload: dict[str, Any]) -> tuple[str | None, list[tuple[int, str, str]]]:
    category_id = str(transaction_payload.get("category_id") or "") or None
    return category_id, _split_signature(transaction_payload.get("subtransactions", []))


def _ynab_transaction_signature(transaction: dict[str, Any]) -> tuple[str | None, list[tuple[int, str, str]]]:
    category_id = str(transaction.get("category_id") or "") or None
    return category_id, _split_signature(_active_subtransactions(transaction))


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
    return abs(Decimal(int(value)) / Decimal("1000"))


def _build_corrected_payload(
    prior_payload: dict[str, Any],
    ynab_transaction: dict[str, Any],
) -> dict[str, Any]:
    next_payload = dict(prior_payload)

    subtransactions = _active_subtransactions(ynab_transaction)
    if subtransactions:
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


def _apply_corrected_validation(
    db: Session,
    *,
    receipt: Receipt,
    ynab_transaction: dict[str, Any],
    settings: Settings,
) -> None:
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
    payee_name = str(normalized_payload.get("payee_name") or "").strip()
    receipt.display_payee_name = payee_name or None
    receipt.display_total_milliunits = int(float(normalized_payload.get("total_amount", 0)) * 1000)
    if normalized_payload.get("transaction_date"):
        receipt.display_receipt_date = datetime.fromisoformat(str(normalized_payload["transaction_date"])).date()
    # Keep receipts synced after reconciliation updates; correction metadata and
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
        if synced_signature == current_signature:
            continue

        signature_hash = _correction_signature(transaction_payload, ynab_transaction)
        if _existing_correction(db, receipt.id, signature_hash):
            continue

        detected_at = utcnow()
        note = f"{_category_note(transaction_payload, ynab_transaction)} | sig={signature_hash}"
        logger.info(
            "Reconciliation mismatch receipt_id=%s transaction_id=%s synced_sig=%s current_sig=%s",
            receipt.id,
            transaction_id,
            synced_signature,
            current_signature,
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
            reason="ynab_category_or_split_changed",
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
            severity = "critical"
            title = "Board Burn Triggered"
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
            message_parts.append(f"{waters_spent_total} water spent to prevent board burn.")
        if burns_triggered_total > 0:
            message_parts.append(
                f"Board burned {burns_triggered_total} time{'s' if burns_triggered_total != 1 else ''}."
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
