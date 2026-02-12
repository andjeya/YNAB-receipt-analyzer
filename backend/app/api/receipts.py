from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings, get_settings
from app.enums import ReceiptStatus
from app.jobs.queue import SYNC_QUEUE_NAME, enqueue_sync_job
from app.models import Receipt, TimingMetric, Validation, ExtractionRun
from app.schemas import (
    ExtractionRunOut,
    ReceiptDetailOut,
    ReceiptSummary,
    SaveDraftRequest,
    SaveDraftResponse,
    SyncEnqueueResponse,
    SyncRequest,
    ValidationOut,
)
from app.services.game import apply_user_validation_gamification
from app.services.validation import validate_payload

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
    return [
        ReceiptSummary(
            id=receipt.id,
            status=receipt.status,
            original_filename=receipt.original_filename,
            display_payee_name=receipt.display_payee_name,
            display_total_milliunits=receipt.display_total_milliunits,
            display_receipt_date=receipt.display_receipt_date,
            ingested_at=receipt.ingested_at,
            updated_at=receipt.updated_at,
        )
        for receipt in receipts
    ]


@router.get("/{receipt_id}", response_model=ReceiptDetailOut)
def get_receipt_detail(receipt_id: str, db: Session = Depends(db_session)) -> ReceiptDetailOut:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    extraction = _latest_extraction(db, receipt_id)
    validation = _latest_validation(db, receipt_id)

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
        ingested_at=receipt.ingested_at,
        extraction_started_at=receipt.extraction_started_at,
        extraction_completed_at=receipt.extraction_completed_at,
        sync_started_at=receipt.sync_started_at,
        sync_completed_at=receipt.sync_completed_at,
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
    )


@router.post("/{receipt_id}/draft", response_model=SaveDraftResponse)
def save_draft(
    receipt_id: str,
    request: SaveDraftRequest,
    db: Session = Depends(db_session),
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

    normalized_payload, is_valid, errors = validate_payload(request.payload)
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
    receipt.display_payee_name = normalized_payload.get("payee_name")
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
        validation_reference_ts = prior_user_valid.created_at if prior_user_valid is not None else validation.created_at
        if prior_user_valid is None:
            now = utcnow()
            if receipt.extraction_completed_at:
                db.add(
                    TimingMetric(
                        receipt_id=receipt.id,
                        metric_name="validation_duration_ms",
                        metric_value_ms=_duration_ms(now, receipt.extraction_completed_at),
                        metadata_json={"validation_version": next_version},
                    )
                )
            db.add(
                TimingMetric(
                    receipt_id=receipt.id,
                    metric_name="receipt_age_at_validation_ms",
                    metric_value_ms=_duration_ms(now, receipt.ingested_at),
                    metadata_json={"validation_version": next_version},
                )
            )
        apply_user_validation_gamification(
            db,
            receipt=receipt,
            validated_at=validation_reference_ts or utcnow(),
            settings=get_settings(),
        )

    db.commit()
    db.refresh(validation)

    return SaveDraftResponse(validation=_to_validation_schema(validation), can_sync=is_valid)


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

    receipt.status = ReceiptStatus.SYNCING.value
    receipt.status_reason = None
    receipt.sync_started_at = utcnow()
    db.commit()

    job_id = enqueue_sync_job(
        receipt_id=receipt.id,
        force_create=request.force_create,
        allow_update_match=request.allow_update_match,
    )

    return SyncEnqueueResponse(
        receipt_id=receipt.id,
        queue_name=SYNC_QUEUE_NAME,
        job_id=job_id,
        status=ReceiptStatus.SYNCING.value,
    )
