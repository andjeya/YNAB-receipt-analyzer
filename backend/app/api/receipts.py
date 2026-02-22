from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.enums import GameEventType, ReceiptStatus, YNABSyncStatus
from app.jobs.queue import SYNC_QUEUE_NAME, enqueue_sync_job
from app.models import ExtractionRun, GameEvent, Receipt, ReceiptCorrection, TimingMetric, Validation, YNABSync
from app.schemas import (
    ExtractionRunOut,
    ReceiptCorrectionOut,
    ReceiptDetailOut,
    ReceiptSummary,
    SaveDraftRequest,
    SaveDraftResponse,
    SyncEnqueueResponse,
    SyncRequest,
    ValidationOut,
)
from app.services.correctness import award_water
from app.services.incidents import record_incident
from app.services.validation import validate_payload
from app.services.ynab import get_cached_reference_data

router = APIRouter(prefix="/receipts", tags=["receipts"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(later: datetime, earlier: datetime) -> int:
    # SQLite returns naive datetimes even with timezone=True; normalize both to UTC-aware.
    if later.tzinfo is None:
        later = later.replace(tzinfo=timezone.utc)
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=timezone.utc)
    return max(int((later - earlier).total_seconds() * 1000), 0)


def _latest_extraction(db: Session, receipt_id: str) -> ExtractionRun | None:
    return db.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.receipt_id == receipt_id)
        .order_by(ExtractionRun.created_at.desc())
        .limit(1)
    )


def _latest_validation(db: Session, receipt_id: str) -> Validation | None:
    return db.scalar(
        select(Validation)
        .where(Validation.receipt_id == receipt_id)
        .order_by(Validation.version.desc())
        .limit(1)
    )


def _first_model_validation(db: Session, receipt_id: str) -> Validation | None:
    return db.scalar(
        select(Validation)
        .where(
            Validation.receipt_id == receipt_id,
            Validation.source == "model",
        )
        .order_by(Validation.version.asc())
        .limit(1)
    )


def _to_extraction_schema(run: ExtractionRun) -> ExtractionRunOut:
    return ExtractionRunOut(
        id=run.id,
        model_name=run.model_name,
        schema_valid=run.schema_valid,
        schema_errors=run.schema_errors,
        parsed_json=run.parsed_json,
        raw_output=run.raw_output,
        duration_ms=run.duration_ms,
        created_at=run.created_at,
    )


def _to_validation_schema(validation: Validation) -> ValidationOut:
    return ValidationOut(
        id=validation.id,
        version=validation.version,
        source=validation.source,
        payload=validation.payload,
        is_valid=validation.is_valid,
        errors=validation.errors,
        created_at=validation.created_at,
    )


def _has_successful_sync(db: Session, receipt_id: str) -> bool:
    row_id = db.scalar(
        select(YNABSync.id)
        .where(
            YNABSync.receipt_id == receipt_id,
            YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
        )
        .limit(1)
    )
    return row_id is not None


def _to_correction_schema(correction: ReceiptCorrection) -> ReceiptCorrectionOut:
    return ReceiptCorrectionOut(
        id=correction.id,
        receipt_id=correction.receipt_id,
        ynab_transaction_id=correction.ynab_transaction_id,
        synced_category_id=correction.synced_category_id,
        corrected_category_id=correction.corrected_category_id,
        synced_splits_json=correction.synced_splits_json,
        corrected_splits_json=correction.corrected_splits_json,
        detected_at=correction.detected_at,
        expires_at=correction.expires_at,
        resynced_at=correction.resynced_at,
        resync_penalty_applied=correction.resync_penalty_applied,
        note=correction.note,
    )


def _latest_correction(db: Session, receipt_id: str) -> ReceiptCorrection | None:
    return db.scalar(
        select(ReceiptCorrection)
        .where(ReceiptCorrection.receipt_id == receipt_id)
        .order_by(ReceiptCorrection.detected_at.desc(), ReceiptCorrection.id.desc())
        .limit(1)
    )


def _correction_note_for_display(correction: ReceiptCorrection | None) -> str | None:
    if correction is None or not correction.note:
        return None
    return correction.note.split("| sig=", 1)[0].strip()


def _correction_shade_opacity(correction: ReceiptCorrection | None, now: datetime) -> float | None:
    if correction is None:
        return None
    detected_at = correction.detected_at
    expires_at = correction.expires_at
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    total_seconds = (expires_at - detected_at).total_seconds()
    remaining_seconds = (expires_at - now).total_seconds()
    if total_seconds <= 0 or remaining_seconds <= 0:
        return 0.0
    return round(max(min(remaining_seconds / total_seconds, 1.0), 0.0), 4)


def _payload_category_signature(payload: dict[str, Any] | None) -> tuple[str, list[tuple[str, str, str]]]:
    if not payload:
        return "", []
    category_id = str(payload.get("category_id") or "")
    splits = payload.get("splits", [])
    signature: list[tuple[str, str, str]] = []
    if isinstance(splits, list):
        for split in splits:
            if not isinstance(split, dict):
                continue
            signature.append(
                (
                    str(split.get("category_id") or ""),
                    str(split.get("amount") or ""),
                    str(split.get("memo") or ""),
                )
            )
    return category_id, sorted(signature)


def _is_manual_category_correction(model_payload: dict[str, Any] | None, user_payload: dict[str, Any]) -> bool:
    return _payload_category_signature(model_payload) != _payload_category_signature(user_payload)


@router.get("", response_model=list[ReceiptSummary])
def list_receipts(
    status: str | None = Query(default=None),
    sort: str = Query(default="newest"),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(db_session),
) -> list[ReceiptSummary]:
    stmt = select(Receipt)
    if status:
        stmt = stmt.where(Receipt.status == status)
    if sort == "oldest":
        stmt = stmt.order_by(Receipt.ingested_at.asc())
    else:
        stmt = stmt.order_by(Receipt.ingested_at.desc())
    stmt = stmt.limit(limit)
    receipts = list(db.scalars(stmt))
    now = utcnow()
    summaries: list[ReceiptSummary] = []
    for receipt in receipts:
        correction = _latest_correction(db, receipt.id)
        shade = _correction_shade_opacity(correction, now)
        summaries.append(
            ReceiptSummary(
                id=receipt.id,
                status=receipt.status,
                original_filename=receipt.original_filename,
                display_payee_name=receipt.display_payee_name,
                display_total_milliunits=receipt.display_total_milliunits,
                display_receipt_date=receipt.display_receipt_date,
                ingested_at=receipt.ingested_at,
                updated_at=receipt.updated_at,
                correction_detected_at=correction.detected_at if correction else None,
                correction_expires_at=correction.expires_at if correction else None,
                correction_shade_opacity=shade,
                correction_message=_correction_note_for_display(correction),
            )
        )
    return summaries


@router.get("/{receipt_id}", response_model=ReceiptDetailOut)
def get_receipt_detail(receipt_id: str, db: Session = Depends(db_session)) -> ReceiptDetailOut:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    extraction = _latest_extraction(db, receipt_id)
    validation = _latest_validation(db, receipt_id)
    model_validation = _first_model_validation(db, receipt_id)
    has_successful_sync = _has_successful_sync(db, receipt_id)
    correction = _latest_correction(db, receipt.id)
    correction_history = list(
        db.scalars(
            select(ReceiptCorrection)
            .where(ReceiptCorrection.receipt_id == receipt.id)
            .order_by(ReceiptCorrection.detected_at.desc(), ReceiptCorrection.id.desc())
            .limit(20)
        )
    )
    shade = _correction_shade_opacity(correction, utcnow())

    return ReceiptDetailOut(
        id=receipt.id,
        status=receipt.status,
        status_reason=receipt.status_reason,
        original_filename=receipt.original_filename,
        storage_key=receipt.storage_key,
        mime_type=receipt.mime_type,
        display_payee_name=receipt.display_payee_name,
        display_total_milliunits=receipt.display_total_milliunits,
        display_receipt_date=receipt.display_receipt_date,
        latest_extraction=_to_extraction_schema(extraction) if extraction else None,
        latest_validation=_to_validation_schema(validation) if validation else None,
        model_validation=_to_validation_schema(model_validation) if model_validation else None,
        ingested_at=receipt.ingested_at,
        extraction_started_at=receipt.extraction_started_at,
        extraction_completed_at=receipt.extraction_completed_at,
        sync_started_at=receipt.sync_started_at,
        sync_completed_at=receipt.sync_completed_at,
        has_successful_sync=has_successful_sync,
        correction_detected_at=correction.detected_at if correction else None,
        correction_expires_at=correction.expires_at if correction else None,
        correction_shade_opacity=shade,
        correction_message=_correction_note_for_display(correction),
        correction_history=[_to_correction_schema(item) for item in correction_history],
        created_at=receipt.created_at,
        updated_at=receipt.updated_at,
    )


@router.get("/{receipt_id}/file")
def get_receipt_file(
    receipt_id: str,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> FileResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    absolute_path = Path(settings.object_store_root) / receipt.storage_key
    if not absolute_path.exists():
        raise HTTPException(status_code=404, detail="Stored file missing")

    return FileResponse(
        path=absolute_path,
        media_type=receipt.mime_type,
        filename=receipt.original_filename,
        content_disposition_type="inline",
    )


@router.post("/{receipt_id}/draft", response_model=SaveDraftResponse)
def save_draft(
    receipt_id: str,
    request: SaveDraftRequest,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> SaveDraftResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    prior_user_valid = db.scalar(
        select(Validation)
        .where(
            Validation.receipt_id == receipt.id,
            Validation.source == "user",
            Validation.is_valid.is_(True),
        )
        .order_by(Validation.created_at.asc())
        .limit(1)
    )
    model_validation = _first_model_validation(db, receipt.id)

    reference_data = get_cached_reference_data(db, settings)
    allowed_category_ids = {item.entity_id for item in reference_data["categories"]}
    allowed_account_ids = {item.entity_id for item in reference_data["accounts"]}
    normalized_payload, is_valid, errors = validate_payload(
        request.payload,
        allowed_category_ids=allowed_category_ids,
        allowed_account_ids=allowed_account_ids,
    )
    next_version = receipt.latest_validation_version + 1

    validation = Validation(
        receipt_id=receipt.id,
        version=next_version,
        source=request.source,
        payload=normalized_payload,
        is_valid=is_valid,
        errors=errors,
    )
    db.add(validation)
    db.flush()

    receipt.latest_validation_version = next_version
    normalized_payee = str(normalized_payload.get("payee_name") or "").strip()
    receipt.display_payee_name = normalized_payee or None
    if normalized_payload.get("total_amount") is not None:
        receipt.display_total_milliunits = int(float(normalized_payload["total_amount"]) * 1000)
    if normalized_payload.get("transaction_date"):
        receipt.display_receipt_date = datetime.fromisoformat(normalized_payload["transaction_date"]).date()

    if receipt.status in {
        ReceiptStatus.SYNCED.value,
        ReceiptStatus.ERROR_SYNC.value,
    }:
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        receipt.status_reason = None

    if request.source == "user" and is_valid:
        now = utcnow()
        if prior_user_valid is None and receipt.extraction_completed_at:
            db.add(
                TimingMetric(
                    receipt_id=receipt.id,
                    metric_name="validation_duration_ms",
                    metric_value_ms=_duration_ms(now, receipt.extraction_completed_at),
                    metadata_json={"validation_version": next_version},
                )
            )
        if _is_manual_category_correction(
            model_validation.payload if model_validation else None,
            normalized_payload,
        ):
            awarded = award_water(
                db,
                settings,
                units=1,
                receipt_id=receipt.id,
                idempotency_key=f"water:manual_correction:{receipt.id}",
                reason="manual_category_or_split_correction",
            )
            if awarded > 0:
                record_incident(
                    db,
                    incident_type="water_earned",
                    severity="info",
                    title="Water Earned",
                    message=f"Manual category correction earned {awarded} water.",
                    details={"receipt_id": receipt.id, "units": awarded},
                    idempotency_key=f"incident:water_earned:{receipt.id}:{next_version}",
                    created_at=now,
                )
            db.add(
                TimingMetric(
                    receipt_id=receipt.id,
                    metric_name="receipt_age_at_validation_ms",
                    metric_value_ms=_duration_ms(now, receipt.ingested_at),
                    metadata_json={"validation_version": next_version},
                )
            )

    db.commit()
    db.refresh(validation)

    return SaveDraftResponse(validation=_to_validation_schema(validation), can_sync=is_valid)


@router.post("/{receipt_id}/reject", response_model=SaveDraftResponse)
def reject_receipt(
    receipt_id: str,
    db: Session = Depends(db_session),
) -> SaveDraftResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    latest = _latest_validation(db, receipt.id)
    payload = latest.payload if latest else {}
    next_version = receipt.latest_validation_version + 1
    validation = Validation(
        receipt_id=receipt.id,
        version=next_version,
        source="reject",
        payload=payload,
        is_valid=False,
        errors=["Rejected by user. Update fields and resync required."],
    )
    db.add(validation)
    db.add(
        GameEvent(
            event_type=GameEventType.RESYNC_REQUIRED.value,
            receipt_id=receipt.id,
            payload_json={"reason": "user_reject"},
            idempotency_key=f"resync_required:reject:{receipt.id}:{next_version}",
            created_at=utcnow(),
        )
    )
    receipt.latest_validation_version = next_version
    receipt.status = ReceiptStatus.NEEDS_REVIEW.value
    receipt.status_reason = "Rejected by user. Resync required after corrections."
    db.commit()
    db.refresh(validation)
    return SaveDraftResponse(validation=_to_validation_schema(validation), can_sync=False)


@router.post("/{receipt_id}/sync", response_model=SyncEnqueueResponse)
def sync_receipt(
    receipt_id: str,
    request: SyncRequest,
    db: Session = Depends(db_session),
) -> SyncEnqueueResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    validation = _latest_validation(db, receipt.id)
    if validation is None or not validation.is_valid:
        raise HTTPException(status_code=400, detail="Receipt must have a valid draft before sync")

    try:
        job_id = enqueue_sync_job(
            receipt_id=receipt.id,
            force_create=request.force_create,
            allow_update_match=request.allow_update_match,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to enqueue sync job: {exc}") from exc

    receipt.status = ReceiptStatus.SYNCING.value
    receipt.status_reason = None
    receipt.sync_started_at = utcnow()
    db.commit()

    return SyncEnqueueResponse(
        receipt_id=receipt.id,
        queue_name=SYNC_QUEUE_NAME,
        job_id=job_id,
        status=ReceiptStatus.SYNCING.value,
    )
