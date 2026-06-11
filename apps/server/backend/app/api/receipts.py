from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.enums import ReceiptStatus, YNABSyncStatus
from app.jobs.queue import EXTRACTION_QUEUE_NAME, SYNC_QUEUE_NAME, enqueue_extraction_job, enqueue_sync_job
from app.models import ExtractionRun, Receipt, ReceiptCorrection, ReceiptTwin, TimingMetric, Validation, YNABSync
from app.schemas import (
    AllocationRecomputeRequest,
    AllocationRecomputeResponse,
    ConfirmedSectionsOut,
    DuplicateConfirmResponse,
    DuplicateOverrideRequest,
    DuplicateOverrideResponse,
    ExtractionRunOut,
    LockedFieldsOut,
    ReceiptCorrectionOut,
    ReceiptDetailOut,
    ReceiptSummary,
    ReceiptTwinOut,
    SaveDraftRequest,
    SaveDraftResponse,
    SaveTwinRequest,
    SaveTwinResponse,
    SyncEnqueueResponse,
    SyncRequest,
    TwinConfirmRequest,
    TwinConfirmResponse,
    ValidationOut,
    YNABSyncOut,
)
from app.services.allocation_workspace import (
    build_initial_allocation_workspace,
    reconcile_allocation_workspace,
    recompute_payload_from_workspace,
)
from app.services.correctness import award_water
from app.services.duplicates import apply_semantic_duplicate_state, build_semantic_signature
from app.services.incidents import record_incident
from app.services.storage import storage_path
from app.services.validation import validate_payload
from app.services.ynab import get_cached_reference_data
from receipt_shared.contracts import ReceiptTwinExtraction
from receipt_shared.money import dollars_to_milliunits

router = APIRouter(prefix="/receipts", tags=["receipts"])
logger = logging.getLogger(__name__)


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


def _primary_extraction(db: Session, receipt_id: str) -> ExtractionRun | None:
    primary = db.scalar(
        select(ExtractionRun)
        .where(
            ExtractionRun.receipt_id == receipt_id,
            ExtractionRun.is_primary_result.is_(True),
        )
        .order_by(ExtractionRun.created_at.desc(), ExtractionRun.id.desc())
        .limit(1)
    )
    if primary is not None:
        return primary
    return _latest_extraction(db, receipt_id)


def _latest_twin(db: Session, receipt_id: str) -> ReceiptTwin | None:
    return db.scalar(
        select(ReceiptTwin)
        .where(ReceiptTwin.receipt_id == receipt_id)
        .order_by(ReceiptTwin.version.desc())
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


def _latest_model_validation(db: Session, receipt_id: str) -> Validation | None:
    return db.scalar(
        select(Validation)
        .where(
            Validation.receipt_id == receipt_id,
            Validation.source == "model",
            Validation.is_valid.is_(True),
        )
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
        attempt_kind=run.attempt_kind,
        is_primary_result=run.is_primary_result,
        parent_run_id=run.parent_run_id,
        created_at=run.created_at,
    )


def _to_validation_schema(validation: Validation) -> ValidationOut:
    return ValidationOut(
        id=validation.id,
        version=validation.version,
        source=validation.source,
        payload=validation.payload,
        allocation_workspace=validation.allocation_workspace,
        is_valid=validation.is_valid,
        errors=validation.errors,
        created_at=validation.created_at,
    )


def _normalize_confirmed_sections(value: dict[str, Any] | None) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {"date_time": False, "total": False}
    return {
        "date_time": bool(value.get("date_time", False)),
        "total": bool(value.get("total", False)),
    }


def _to_twin_schema(twin: ReceiptTwin) -> ReceiptTwinOut:
    normalized = _normalize_confirmed_sections(twin.confirmed_sections)
    return ReceiptTwinOut(
        id=twin.id,
        receipt_id=twin.receipt_id,
        version=twin.version,
        source=twin.source,
        payload=twin.payload,
        confirmed_sections=ConfirmedSectionsOut(
            date_time=normalized["date_time"],
            total=normalized["total"],
        ),
        created_at=twin.created_at,
    )


def _locked_fields_for_twin(twin: ReceiptTwin | None) -> LockedFieldsOut:
    normalized = _normalize_confirmed_sections(twin.confirmed_sections if twin else None)
    return LockedFieldsOut(
        transaction_date=normalized["date_time"],
        transaction_time=normalized["date_time"],
        total_amount=normalized["total"],
    )


def _normalize_twin_payload(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = ReceiptTwinExtraction.model_validate(payload)
    return parsed.model_dump(mode="json")


def _apply_twin_locks_to_payload(payload: dict[str, Any], twin: ReceiptTwin | None) -> tuple[dict[str, Any], list[str]]:
    if twin is None:
        return payload, []

    locked_payload = dict(payload)
    warnings: list[str] = []
    confirmed_sections = _normalize_confirmed_sections(twin.confirmed_sections)
    twin_payload = twin.payload if isinstance(twin.payload, dict) else {}

    if confirmed_sections["date_time"]:
        for field in ("transaction_date", "transaction_time"):
            if locked_payload.get(field) != twin_payload.get(field):
                warnings.append(f"{field} is locked by confirmed receipt twin and was overridden")
            locked_payload[field] = twin_payload.get(field)

    if confirmed_sections["total"]:
        if locked_payload.get("total_amount") != twin_payload.get("total_amount"):
            warnings.append("total_amount is locked by confirmed receipt twin and was overridden")
        locked_payload["total_amount"] = twin_payload.get("total_amount")

    return locked_payload, warnings


def _update_receipt_display_fields_from_payload(receipt: Receipt, payload: dict[str, Any]) -> None:
    normalized_payee = str(payload.get("payee_name") or "").strip()
    receipt.display_payee_name = normalized_payee or None
    if payload.get("total_amount") is not None:
        receipt.display_total_milliunits = dollars_to_milliunits(payload["total_amount"], outflow=False)
    if payload.get("transaction_date"):
        receipt.display_receipt_date = datetime.fromisoformat(str(payload["transaction_date"])).date()


def _create_validation_version(
    db: Session,
    *,
    receipt: Receipt,
    payload: dict[str, Any],
    allocation_workspace: dict[str, Any] | None,
    source: str,
    is_valid: bool,
    errors: list[str],
) -> Validation:
    next_version = receipt.latest_validation_version + 1
    validation = Validation(
        receipt_id=receipt.id,
        version=next_version,
        source=source,
        payload=payload,
        allocation_workspace=allocation_workspace,
        is_valid=is_valid,
        errors=errors,
    )
    db.add(validation)
    receipt.latest_validation_version = next_version
    _update_receipt_display_fields_from_payload(receipt, payload)
    db.flush()
    return validation


def _refresh_validation_from_confirmed_twin_sections(
    db: Session,
    *,
    receipt: Receipt,
    twin: ReceiptTwin,
    settings: Settings,
    source: str,
) -> Validation | None:
    latest_validation = _latest_validation(db, receipt.id)
    if latest_validation is None:
        return None

    locked_payload, _ = _apply_twin_locks_to_payload(latest_validation.payload, twin)
    if locked_payload == latest_validation.payload:
        return None

    reference_data = get_cached_reference_data(db, settings)
    allowed_category_ids = {item.entity_id for item in reference_data["categories"]}
    allowed_account_ids = {item.entity_id for item in reference_data["accounts"]}
    normalized_payload, is_valid, errors = validate_payload(
        locked_payload,
        allowed_category_ids=allowed_category_ids,
        allowed_account_ids=allowed_account_ids,
    )
    validation = _create_validation_version(
        db,
        receipt=receipt,
        payload=normalized_payload,
        allocation_workspace=latest_validation.allocation_workspace,
        source=source,
        is_valid=is_valid,
        errors=errors,
    )

    if receipt.status in {ReceiptStatus.SYNCED.value, ReceiptStatus.ERROR_SYNC.value}:
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        receipt.status_reason = None

    return validation


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


def _latest_sync(db: Session, receipt_id: str) -> YNABSync | None:
    return db.scalar(
        select(YNABSync)
        .where(YNABSync.receipt_id == receipt_id)
        .order_by(YNABSync.completed_at.desc().nullslast(), YNABSync.id.desc())
        .limit(1)
    )


def _to_sync_schema(sync: YNABSync) -> YNABSyncOut:
    return YNABSyncOut(
        id=sync.id,
        status=sync.status,
        match_mode=sync.match_mode,
        raw_request=sync.raw_request,
        created_transaction_id=sync.created_transaction_id,
        matched_transaction_id=sync.matched_transaction_id,
        completed_at=sync.completed_at,
    )


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


def _amount_signature(value: Any) -> int:
    try:
        return int((Decimal(str(value or 0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP) * 1000))
    except Exception:
        return 0


def _payload_category_signature(payload: dict[str, Any] | None) -> tuple[str | None, list[tuple[int, str]]]:
    if not payload:
        return None, []
    category_id = str(payload.get("category_id") or "") or None
    splits = payload.get("splits", [])
    signature: list[tuple[int, str]] = []
    if isinstance(splits, list):
        for split in splits:
            if not isinstance(split, dict):
                continue
            signature.append(
                (
                    _amount_signature(split.get("amount", 0)),
                    str(split.get("category_id") or ""),
                )
            )
    # A single split with the full amount and same category is equivalent to
    # single-category mode.
    if len(signature) == 1:
        split_amount, split_category = signature[0]
        if (
            split_category
            and split_amount == _amount_signature(payload.get("total_amount", 0))
            and (category_id is None or category_id == split_category)
        ):
            return split_category, []
    # In split mode, ignore top-level category_id and split memo text. We only
    # award water for category/split-amount corrections.
    category_id = None if signature else category_id
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
                duplicate_of_receipt_id=receipt.duplicate_of_receipt_id,
            )
        )
    return summaries


@router.get("/{receipt_id}", response_model=ReceiptDetailOut)
def get_receipt_detail(receipt_id: str, db: Session = Depends(db_session)) -> ReceiptDetailOut:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    extraction = _latest_extraction(db, receipt_id)
    extraction_primary = _primary_extraction(db, receipt_id)
    validation = _latest_validation(db, receipt_id)
    model_validation = _latest_model_validation(db, receipt_id)
    twin = _latest_twin(db, receipt_id)
    has_successful_sync = _has_successful_sync(db, receipt_id)
    latest_sync_row = _latest_sync(db, receipt_id)
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
        extraction_primary=_to_extraction_schema(extraction_primary) if extraction_primary else None,
        latest_validation=_to_validation_schema(validation) if validation else None,
        model_validation=_to_validation_schema(model_validation) if model_validation else None,
        latest_twin=_to_twin_schema(twin) if twin else None,
        locked_fields=_locked_fields_for_twin(twin),
        ingested_at=receipt.ingested_at,
        extraction_started_at=receipt.extraction_started_at,
        extraction_completed_at=receipt.extraction_completed_at,
        sync_started_at=receipt.sync_started_at,
        sync_completed_at=receipt.sync_completed_at,
        has_successful_sync=has_successful_sync,
        latest_sync=_to_sync_schema(latest_sync_row) if latest_sync_row else None,
        correction_detected_at=correction.detected_at if correction else None,
        correction_expires_at=correction.expires_at if correction else None,
        correction_shade_opacity=shade,
        correction_message=_correction_note_for_display(correction),
        duplicate_of_receipt_id=receipt.duplicate_of_receipt_id,
        correction_history=[_to_correction_schema(item) for item in correction_history],
        created_at=receipt.created_at,
        updated_at=receipt.updated_at,
    )


@router.get("/{receipt_id}/file")
def get_receipt_file(
    receipt_id: str,
    preview: bool = Query(default=False),
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> FileResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    store_root = Path(settings.object_store_root).resolve()
    absolute_path = (store_root / receipt.storage_key).resolve()
    if not str(absolute_path).startswith(str(store_root) + "/"):
        raise HTTPException(status_code=404, detail="Stored file missing")
    if not absolute_path.exists():
        raise HTTPException(status_code=404, detail="Stored file missing")

    return FileResponse(
        path=absolute_path,
        media_type=receipt.mime_type,
        filename=receipt.original_filename,
        content_disposition_type="inline" if preview else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/{receipt_id}/twin", response_model=ReceiptTwinOut)
def get_receipt_twin(receipt_id: str, db: Session = Depends(db_session)) -> ReceiptTwinOut:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    twin = _latest_twin(db, receipt_id)
    if twin is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "twin_unavailable", "message": "Twin unavailable for receipt"},
        )
    return _to_twin_schema(twin)


@router.put("/{receipt_id}/twin", response_model=SaveTwinResponse)
def save_receipt_twin(
    receipt_id: str,
    request: SaveTwinRequest,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> SaveTwinResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    latest_twin = _latest_twin(db, receipt_id)
    if latest_twin is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "twin_unavailable", "message": "Twin unavailable for receipt"},
        )

    if request.base_version != latest_twin.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_twin_version",
                "message": f"Stale base_version {request.base_version}; latest is {latest_twin.version}",
            },
        )

    try:
        normalized_payload = _normalize_twin_payload(request.payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    if normalized_payload == latest_twin.payload:
        return SaveTwinResponse(twin=_to_twin_schema(latest_twin), changed=False)

    confirmed_sections = _normalize_confirmed_sections(latest_twin.confirmed_sections)
    next_version = latest_twin.version + 1
    new_twin = ReceiptTwin(
        receipt_id=receipt.id,
        version=next_version,
        source=request.source or "user",
        payload=normalized_payload,
        confirmed_sections=confirmed_sections,
    )
    db.add(new_twin)
    db.flush()
    receipt.latest_twin_version = next_version

    should_refresh_validation = False
    if confirmed_sections["date_time"]:
        should_refresh_validation = should_refresh_validation or (
            latest_twin.payload.get("transaction_date") != normalized_payload.get("transaction_date")
            or latest_twin.payload.get("transaction_time") != normalized_payload.get("transaction_time")
        )
    if confirmed_sections["total"]:
        should_refresh_validation = should_refresh_validation or (
            latest_twin.payload.get("total_amount") != normalized_payload.get("total_amount")
        )

    if should_refresh_validation:
        _refresh_validation_from_confirmed_twin_sections(
            db,
            receipt=receipt,
            twin=new_twin,
            settings=settings,
            source="twin",
        )

    db.commit()
    db.refresh(new_twin)
    return SaveTwinResponse(twin=_to_twin_schema(new_twin), changed=True)


@router.post("/{receipt_id}/twin/confirm", response_model=TwinConfirmResponse)
def confirm_receipt_twin_section(
    receipt_id: str,
    request: TwinConfirmRequest,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> TwinConfirmResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    twin = _latest_twin(db, receipt_id)
    if twin is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "twin_unavailable", "message": "Twin unavailable for receipt"},
        )

    confirmed_sections = _normalize_confirmed_sections(twin.confirmed_sections)
    current_state = confirmed_sections[request.section]
    if current_state == request.confirmed:
        return TwinConfirmResponse(twin=_to_twin_schema(twin), validation=None)

    confirmed_sections[request.section] = request.confirmed
    twin.confirmed_sections = confirmed_sections

    validation: Validation | None = None
    if request.confirmed:
        validation = _refresh_validation_from_confirmed_twin_sections(
            db,
            receipt=receipt,
            twin=twin,
            settings=settings,
            source="twin_confirm",
        )

    db.commit()
    db.refresh(twin)
    if validation is not None:
        db.refresh(validation)

    return TwinConfirmResponse(
        twin=_to_twin_schema(twin),
        validation=_to_validation_schema(validation) if validation else None,
    )


@router.post("/{receipt_id}/twin/retry-extract", response_model=SyncEnqueueResponse)
def retry_twin_extraction(
    receipt_id: str,
    db: Session = Depends(db_session),
) -> SyncEnqueueResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    try:
        job_id = enqueue_extraction_job(receipt_id=receipt.id)
    except Exception as exc:
        logger.exception("Failed to enqueue extraction job for receipt %s", receipt.id)
        raise HTTPException(status_code=503, detail="Failed to enqueue extraction job") from exc

    receipt.status = ReceiptStatus.EXTRACTING.value
    receipt.status_reason = None
    receipt.extraction_started_at = utcnow()
    db.commit()

    return SyncEnqueueResponse(
        receipt_id=receipt.id,
        queue_name=EXTRACTION_QUEUE_NAME,
        job_id=job_id,
        status=ReceiptStatus.EXTRACTING.value,
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
    latest_validation_before = _latest_validation(db, receipt.id)
    latest_twin = _latest_twin(db, receipt.id)
    locked_payload, lock_warnings = _apply_twin_locks_to_payload(request.payload, latest_twin)

    reference_data = get_cached_reference_data(db, settings)
    allowed_category_ids = {item.entity_id for item in reference_data["categories"]}
    allowed_account_ids = {item.entity_id for item in reference_data["accounts"]}
    normalized_payload, is_valid, errors = validate_payload(
        locked_payload,
        allowed_category_ids=allowed_category_ids,
        allowed_account_ids=allowed_account_ids,
    )
    twin_payload = latest_twin.payload if latest_twin and isinstance(latest_twin.payload, dict) else None
    twin_version = latest_twin.version if latest_twin else 0
    allocation_workspace = reconcile_allocation_workspace(
        normalized_payload,
        request.allocation_workspace,
        twin_payload=twin_payload,
        twin_version=twin_version,
    )
    validation = _create_validation_version(
        db,
        receipt=receipt,
        payload=normalized_payload,
        allocation_workspace=allocation_workspace,
        source=request.source,
        is_valid=is_valid,
        errors=errors,
    )
    next_version = validation.version

    payload_changed = latest_validation_before is None or latest_validation_before.payload != normalized_payload
    if payload_changed and receipt.status in {ReceiptStatus.SYNCED.value, ReceiptStatus.ERROR_SYNC.value}:
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        receipt.status_reason = None

    if is_valid:
        apply_semantic_duplicate_state(
            db,
            receipt=receipt,
            payload=normalized_payload,
        )
    else:
        receipt.duplicate_of_receipt_id = None
        receipt.semantic_signature = None
        receipt.semantic_payee_key = None
        receipt.semantic_total_cents = None
        receipt.semantic_transaction_date = None
        receipt.semantic_transaction_time = None
        receipt.duplicate_override_signature = None
        if receipt.status == ReceiptStatus.DUPLICATE_REVIEW.value:
            receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            receipt.status_reason = "Duplicate check deferred until draft is valid."

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

    return SaveDraftResponse(
        validation=_to_validation_schema(validation),
        can_sync=is_valid and receipt.status != ReceiptStatus.DUPLICATE_REVIEW.value,
        lock_warnings=lock_warnings,
    )


@router.post("/{receipt_id}/allocation/recompute", response_model=AllocationRecomputeResponse)
def recompute_allocation_workspace(
    receipt_id: str,
    request: AllocationRecomputeRequest,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> AllocationRecomputeResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    latest_validation = _latest_validation(db, receipt.id)
    if latest_validation is None:
        raise HTTPException(status_code=409, detail="Receipt has no validation draft")

    latest_twin = _latest_twin(db, receipt.id)
    twin_payload = latest_twin.payload if latest_twin and isinstance(latest_twin.payload, dict) else None
    twin_version = latest_twin.version if latest_twin else 0
    base_workspace = request.workspace or latest_validation.allocation_workspace
    if not base_workspace:
        base_workspace = build_initial_allocation_workspace(
            latest_validation.payload,
            twin_payload=twin_payload,
            twin_version=twin_version,
        )

    workspace_payload = reconcile_allocation_workspace(
        latest_validation.payload,
        base_workspace,
        twin_payload=twin_payload,
        twin_version=twin_version,
    )
    recomputed_payload, recomputed_workspace, warnings = recompute_payload_from_workspace(
        latest_validation.payload,
        workspace_payload,
        mode=request.mode,
    )

    reference_data = get_cached_reference_data(db, settings)
    allowed_category_ids = {item.entity_id for item in reference_data["categories"]}
    allowed_account_ids = {item.entity_id for item in reference_data["accounts"]}
    normalized_payload, is_valid, errors = validate_payload(
        recomputed_payload,
        allowed_category_ids=allowed_category_ids,
        allowed_account_ids=allowed_account_ids,
    )
    if not is_valid:
        raise HTTPException(status_code=422, detail={"errors": errors})

    return AllocationRecomputeResponse(
        payload=normalized_payload,
        workspace=recomputed_workspace,
        warnings=warnings,
    )


@router.post("/{receipt_id}/duplicate/confirm", response_model=DuplicateConfirmResponse)
def confirm_duplicate_receipt(
    receipt_id: str,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> DuplicateConfirmResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if receipt.status != ReceiptStatus.DUPLICATE_REVIEW.value or not receipt.duplicate_of_receipt_id:
        raise HTTPException(status_code=409, detail="Receipt is not in duplicate review state")

    kept_receipt_id = receipt.duplicate_of_receipt_id
    store_root = Path(settings.object_store_root).resolve()
    absolute_path = storage_path(store_root, receipt.storage_key).resolve()
    if str(absolute_path).startswith(str(store_root) + "/"):
        absolute_path.unlink(missing_ok=True)

    db.delete(receipt)
    db.commit()
    return DuplicateConfirmResponse(
        deleted_receipt_id=receipt_id,
        kept_receipt_id=kept_receipt_id,
    )


@router.post("/{receipt_id}/duplicate/override", response_model=DuplicateOverrideResponse)
def override_duplicate_receipt(
    receipt_id: str,
    request: DuplicateOverrideRequest,
    db: Session = Depends(db_session),
) -> DuplicateOverrideResponse:
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Override requires confirmed=true")

    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    latest_validation = _latest_validation(db, receipt.id)
    if latest_validation is None or not latest_validation.is_valid:
        raise HTTPException(status_code=409, detail="Receipt must have a valid draft before override")

    signature = receipt.semantic_signature or build_semantic_signature(latest_validation.payload)
    receipt.duplicate_override_signature = signature
    receipt.duplicate_of_receipt_id = None
    if receipt.status == ReceiptStatus.DUPLICATE_REVIEW.value:
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
    receipt.status_reason = "Duplicate detection overridden by user."
    db.commit()

    return DuplicateOverrideResponse(
        receipt_id=receipt.id,
        status=receipt.status,
        duplicate_of_receipt_id=receipt.duplicate_of_receipt_id,
    )


@router.post("/{receipt_id}/sync", response_model=SyncEnqueueResponse)
def sync_receipt(
    receipt_id: str,
    request: SyncRequest,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> SyncEnqueueResponse:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    validation = _latest_validation(db, receipt.id)
    if validation is None or not validation.is_valid:
        raise HTTPException(status_code=400, detail="Receipt must have a valid draft before sync")

    if not settings.ynab_sync_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ynab_sync_disabled",
                "message": (
                    "YNAB sync is disabled. Set YNAB_SYNC_ENABLED=true to enable writing "
                    "to YNAB."
                ),
            },
        )

    duplicate_state = apply_semantic_duplicate_state(
        db,
        receipt=receipt,
        payload=validation.payload,
    )
    if duplicate_state.duplicate_of_receipt_id:
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_receipt",
                "message": (
                    f"Duplicate detected against receipt {duplicate_state.duplicate_of_receipt_id}. "
                    "Resolve duplicate review before syncing."
                ),
                "duplicate_of_receipt_id": duplicate_state.duplicate_of_receipt_id,
            },
        )

    try:
        job_id = enqueue_sync_job(
            receipt_id=receipt.id,
            force_create=request.force_create,
            allow_update_match=request.allow_update_match,
        )
    except Exception as exc:
        logger.exception("Failed to enqueue sync job for receipt %s", receipt.id)
        raise HTTPException(status_code=503, detail="Failed to enqueue sync job") from exc

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
