from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.utils import utcnow
from app.enums import ReceiptStatus
from app.models import ExtractionRun, Receipt, ReceiptTwin, TimingMetric, Validation
from app.services.reconciliation import run_ynab_reconciliation
from app.services.validation import build_initial_validation_payload, validate_payload
from app.services.ynab import get_cached_reference_data, refresh_ynab_cache, sync_receipt_to_ynab
from receipt_shared.contracts import GeminiReceiptExtraction, ReceiptTwinExtraction, UnifiedReceiptExtraction
from receipt_shared.gemini import (
    GeminiAnalysisResult,
    GeminiAnalyzer,
    build_analysis_prompt,
    build_twin_extraction_prompt,
    build_unified_prompt,
)
from receipt_shared.money import dollars_to_milliunits
from receipt_shared.ynab_client import Category

logger = logging.getLogger(__name__)

ATTEMPT_UNIFIED = "unified"
ATTEMPT_FALLBACK_YNAB = "fallback_ynab"
ATTEMPT_FALLBACK_TWIN = "fallback_twin"

TWIN_PAYLOAD_FIELDS = (
    "store_name",
    "store_address",
    "transaction_date",
    "transaction_time",
    "currency",
    "line_items",
    "subtotal",
    "tax_total",
    "total_amount",
    "payment_method",
    "receipt_language",
)



def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _normalize_twin_payload(parsed_json: dict[str, Any]) -> dict[str, Any]:
    payload = {field: parsed_json.get(field) for field in TWIN_PAYLOAD_FIELDS}
    line_items = payload.get("line_items")
    if not isinstance(line_items, list):
        payload["line_items"] = []
    else:
        payload["line_items"] = [item for item in line_items if isinstance(item, dict)]

    if not payload.get("currency"):
        payload["currency"] = "USD"
    if not payload.get("receipt_language"):
        payload["receipt_language"] = "en"
    return payload


def _is_twin_payload_minimally_usable(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    total = _safe_decimal(payload.get("total_amount"))
    if total is None:
        return False
    if total <= Decimal("0"):
        return False
    return True


def _evaluate_twin_quality(
    payload: dict[str, Any],
    *,
    hard_fail_delta_abs: float,
    hard_fail_delta_pct: float,
) -> tuple[list[str], bool]:
    warnings: list[str] = []
    line_items = payload.get("line_items")
    if not isinstance(line_items, list) or len(line_items) == 0:
        warnings.append("line_items missing or empty")
        return warnings, False

    total = _safe_decimal(payload.get("total_amount"))
    if total is None:
        warnings.append("total_amount missing")
        return warnings, False

    additive_total = Decimal("0")
    additive_count = 0
    for item in line_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("item_type") or "product").strip().lower()
        if item_type in {"subtotal", "total"}:
            continue

        line_total = _safe_decimal(item.get("line_total"))
        if line_total is None:
            continue

        if item_type == "discount":
            additive_total -= abs(line_total)
        elif item_type in {"product", "fee", "tax"}:
            additive_total += abs(line_total)
        else:
            additive_total += line_total
        additive_count += 1

    if additive_count == 0:
        warnings.append("no additive line_item totals available for reconciliation")
        return warnings, False

    delta_abs = abs(total - additive_total)
    delta_pct = (delta_abs / abs(total)) if total != 0 else Decimal("0")

    if delta_abs > Decimal("0.05"):
        warnings.append(
            f"reconciliation drift abs_delta={float(delta_abs):.2f} pct_delta={float(delta_pct):.4f}"
        )

    hard_fail = (
        delta_abs > Decimal(str(hard_fail_delta_abs))
        and delta_pct > Decimal(str(hard_fail_delta_pct))
    )
    if hard_fail:
        warnings.append("severe reconciliation mismatch")

    return warnings, hard_fail


def _record_extraction_run(
    db,
    *,
    receipt_id: str,
    model_name: str,
    prompt_text: str,
    analysis: GeminiAnalysisResult,
    started_at: datetime,
    completed_at: datetime,
    attempt_kind: str,
    parent_run_id: int | None = None,
    schema_errors: list[str] | None = None,
) -> ExtractionRun:
    run = ExtractionRun(
        receipt_id=receipt_id,
        model_name=model_name,
        prompt_text=prompt_text,
        raw_output=analysis.raw_output,
        parsed_json=analysis.parsed_json,
        schema_valid=analysis.schema_valid,
        schema_errors=schema_errors if schema_errors is not None else analysis.schema_errors,
        duration_ms=analysis.duration_ms,
        started_at=started_at,
        completed_at=completed_at,
        attempt_kind=attempt_kind,
        parent_run_id=parent_run_id,
        is_primary_result=False,
    )
    db.add(run)
    db.flush()
    return run


def _set_primary_extraction_run(db, receipt_id: str, run_id: int) -> None:
    db.execute(
        update(ExtractionRun)
        .where(ExtractionRun.receipt_id == receipt_id)
        .values(is_primary_result=False)
    )
    db.execute(
        update(ExtractionRun)
        .where(ExtractionRun.id == run_id)
        .values(is_primary_result=True)
    )


def _create_validation(
    db,
    *,
    receipt: Receipt,
    payload: dict[str, Any],
    source: str,
) -> Validation:
    def _attempt() -> Validation:
        next_version = receipt.latest_validation_version + 1
        validation = Validation(
            receipt_id=receipt.id,
            version=next_version,
            source=source,
            payload=payload,
            is_valid=True,
            errors=[],
        )
        db.add(validation)
        receipt.latest_validation_version = next_version

        normalized_payee = str(payload.get("payee_name") or "").strip()
        receipt.display_payee_name = normalized_payee or None
        receipt.display_total_milliunits = dollars_to_milliunits(payload.get("total_amount", 0), outflow=False)
        if payload.get("transaction_date"):
            receipt.display_receipt_date = datetime.fromisoformat(str(payload["transaction_date"])).date()

        return validation

    try:
        # Use a savepoint so a version collision doesn't invalidate the outer transaction.
        # The unique constraint on (receipt_id, version) is the safety net against races.
        with db.begin_nested():
            return _attempt()
    except IntegrityError:
        db.refresh(receipt)
        return _attempt()


def _create_model_twin(
    db,
    *,
    receipt: Receipt,
    payload: dict[str, Any],
) -> ReceiptTwin:
    def _attempt() -> ReceiptTwin:
        next_version = receipt.latest_twin_version + 1
        twin = ReceiptTwin(
            receipt_id=receipt.id,
            version=next_version,
            source="model",
            payload=payload,
            confirmed_sections={"date_time": False, "total": False},
        )
        db.add(twin)
        receipt.latest_twin_version = next_version
        return twin

    try:
        # Use a savepoint so a version collision doesn't invalidate the outer transaction.
        # The unique constraint on (receipt_id, version) is the safety net against races.
        with db.begin_nested():
            return _attempt()
    except IntegrityError:
        db.refresh(receipt)
        return _attempt()


def _validate_ynab_payload(
    parsed_json: dict[str, Any],
    *,
    default_account_id: str | None,
    allowed_category_ids: set[str],
    allowed_account_ids: set[str],
) -> tuple[dict[str, Any], bool, list[str]]:
    initial_payload = build_initial_validation_payload(parsed_json, default_account_id)
    return validate_payload(
        initial_payload,
        allowed_category_ids=allowed_category_ids,
        allowed_account_ids=allowed_account_ids,
        allow_unknown_account=True,
    )


def _apply_twin_reality_to_validation(
    validation_payload: dict[str, Any],
    twin_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    updated = dict(validation_payload)
    disagreements: list[str] = []

    for field in ("transaction_date", "transaction_time", "total_amount"):
        twin_value = twin_payload.get(field)
        if updated.get(field) != twin_value:
            disagreements.append(field)
        updated[field] = twin_value

    return updated, disagreements


def _attach_traceability(parsed_json: dict[str, Any] | None, key: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(parsed_json, dict):
        return parsed_json
    updated = dict(parsed_json)
    traceability = updated.get("_traceability")
    if not isinstance(traceability, dict):
        traceability = {}
    traceability[key] = value
    updated["_traceability"] = traceability
    return updated


def _summarize_errors(groups: list[tuple[str, list[str]]]) -> str:
    flattened: list[str] = []
    for label, errors in groups:
        for error in errors:
            text = str(error).strip()
            if not text:
                continue
            flattened.append(f"{label}: {text}")
    return "; ".join(flattened)


@dataclass
class _ExtractionCtx:
    db: Any
    receipt: Receipt
    settings: Any
    analyzer: GeminiAnalyzer
    file_path: Path
    allowed_category_ids: set[str]
    allowed_account_ids: set[str]
    prompt_categories: list[Any]
    prompt_accounts: list[Any]
    prompt_payees: list[str]


@dataclass
class _UnifiedAttemptResult:
    run: ExtractionRun
    errors: list[str]
    validation_payload: dict[str, Any] | None
    twin_payload: dict[str, Any] | None
    ynab_critical_ok: bool
    completed_at: datetime
    duration_ms: int


def _build_extraction_ctx(db: Any, receipt: Receipt, settings: Any) -> _ExtractionCtx:
    reference_data = get_cached_reference_data(db, settings)
    categories = reference_data["categories"]
    accounts = reference_data["accounts"]
    payees = reference_data["payees"]

    if (not categories or not accounts or not payees) and settings.ynab_access_token and settings.ynab_budget_id:
        refresh_ynab_cache(db, settings)
        reference_data = get_cached_reference_data(db, settings)
        categories = reference_data["categories"]
        accounts = reference_data["accounts"]
        payees = reference_data["payees"]

    prompt_categories = [
        Category(id=item.entity_id, name=item.name, group_name=item.group_name or "Uncategorized")
        for item in categories
    ]
    analyzer = GeminiAnalyzer(
        settings.gemini_api_key,
        settings.gemini_model,
        settings.gemini_max_retries,
        model_registry_path=settings.ai_model_registry_path,
        limits_config_path=settings.ai_limits_config_path,
        usage_db_url=settings.ai_usage_db_url,
    )
    return _ExtractionCtx(
        db=db,
        receipt=receipt,
        settings=settings,
        analyzer=analyzer,
        file_path=Path(settings.object_store_root) / receipt.storage_key,
        allowed_category_ids={item.entity_id for item in categories},
        allowed_account_ids={item.entity_id for item in accounts},
        prompt_categories=prompt_categories,
        prompt_accounts=[account.raw_json for account in accounts],
        prompt_payees=[payee.name for payee in payees],
    )


def _run_simple_extraction(ctx: _ExtractionCtx) -> None:
    """Single-pass extraction used when twin extraction is disabled."""
    prompt_text = build_analysis_prompt(
        ctx.settings.gemini_prompt,
        ctx.prompt_categories,
        ctx.prompt_accounts,
        ctx.prompt_payees,
    )
    started_at = utcnow()
    analysis = ctx.analyzer.analyze_file(
        ctx.file_path,
        prompt_text,
        ctx.receipt.mime_type,
        response_schema=GeminiReceiptExtraction,
        route="ynab_extract.unified",
        metadata={"receipt_id": ctx.receipt.id, "attempt_kind": ATTEMPT_UNIFIED},
        correlation_id=ctx.receipt.id,
        limit_behavior=ctx.settings.ai_limit_behavior,
    )
    completed_at = utcnow()
    run = _record_extraction_run(
        ctx.db,
        receipt_id=ctx.receipt.id,
        model_name=ctx.settings.gemini_model,
        prompt_text=prompt_text,
        analysis=analysis,
        started_at=started_at,
        completed_at=completed_at,
        attempt_kind=ATTEMPT_UNIFIED,
    )

    run_errors = list(analysis.schema_errors)
    if analysis.schema_valid and analysis.parsed_json:
        normalized_payload, is_valid, errors = _validate_ynab_payload(
            analysis.parsed_json,
            default_account_id=ctx.settings.ynab_default_account_id,
            allowed_category_ids=ctx.allowed_category_ids,
            allowed_account_ids=ctx.allowed_account_ids,
        )
        if is_valid:
            _create_validation(ctx.db, receipt=ctx.receipt, payload=normalized_payload, source="model")
            _set_primary_extraction_run(ctx.db, ctx.receipt.id, run.id)
            ctx.receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            ctx.receipt.status_reason = None
        else:
            run_errors.extend([f"ynab_critical: {error}" for error in errors])
            ctx.receipt.status = ReceiptStatus.ERROR_EXTRACT.value
            ctx.receipt.status_reason = _summarize_errors([("unified", run_errors)])
    else:
        ctx.receipt.status = ReceiptStatus.ERROR_EXTRACT.value
        ctx.receipt.status_reason = _summarize_errors([("unified", run_errors)])

    run.schema_errors = run_errors
    ctx.receipt.extraction_completed_at = completed_at
    if ctx.receipt.status == ReceiptStatus.NEEDS_REVIEW.value:
        ctx.db.add(
            TimingMetric(
                receipt_id=ctx.receipt.id,
                metric_name="extraction_duration_ms",
                metric_value_ms=analysis.duration_ms,
                metadata_json={"model": ctx.settings.gemini_model, "attempt_kind": ATTEMPT_UNIFIED},
            )
        )
    ctx.db.commit()
    logger.info("Finished extraction receipt_id=%s status=%s", ctx.receipt.id, ctx.receipt.status)


def _run_unified_attempt(ctx: _ExtractionCtx) -> _UnifiedAttemptResult:
    """Run the primary unified extraction pass; does not commit."""
    unified_prompt = build_unified_prompt(
        ctx.settings.gemini_prompt,
        ctx.prompt_categories,
        ctx.prompt_accounts,
        ctx.prompt_payees,
    )
    unified_started_at = utcnow()
    unified_analysis = ctx.analyzer.analyze_file(
        ctx.file_path,
        unified_prompt,
        ctx.receipt.mime_type,
        response_schema=UnifiedReceiptExtraction,
        route="ynab_extract.unified",
        metadata={"receipt_id": ctx.receipt.id, "attempt_kind": ATTEMPT_UNIFIED},
        correlation_id=ctx.receipt.id,
        limit_behavior=ctx.settings.ai_limit_behavior,
    )
    unified_completed_at = utcnow()

    unified_errors = list(unified_analysis.schema_errors)
    unified_run = _record_extraction_run(
        ctx.db,
        receipt_id=ctx.receipt.id,
        model_name=ctx.settings.gemini_model,
        prompt_text=unified_prompt,
        analysis=unified_analysis,
        started_at=unified_started_at,
        completed_at=unified_completed_at,
        attempt_kind=ATTEMPT_UNIFIED,
        schema_errors=unified_errors,
    )

    unified_validation_payload: dict[str, Any] | None = None
    unified_twin_payload: dict[str, Any] | None = None
    unified_ynab_critical_ok = False

    if unified_analysis.schema_valid and unified_analysis.parsed_json:
        normalized_payload, is_valid, validation_errors = _validate_ynab_payload(
            unified_analysis.parsed_json,
            default_account_id=ctx.settings.ynab_default_account_id,
            allowed_category_ids=ctx.allowed_category_ids,
            allowed_account_ids=ctx.allowed_account_ids,
        )
        unified_twin_payload = _normalize_twin_payload(unified_analysis.parsed_json)
        twin_warnings, twin_hard_fail = _evaluate_twin_quality(
            unified_twin_payload,
            hard_fail_delta_abs=ctx.settings.twin_recon_hard_fail_delta_abs,
            hard_fail_delta_pct=ctx.settings.twin_recon_hard_fail_delta_pct,
        )

        if not is_valid:
            unified_errors.extend([f"ynab_critical: {error}" for error in validation_errors])
        else:
            unified_validation_payload = normalized_payload
            unified_ynab_critical_ok = True

        if twin_warnings:
            unified_errors.extend([f"twin_quality: {warning}" for warning in twin_warnings])

        if ctx.settings.twin_strict_mode and twin_hard_fail:
            unified_ynab_critical_ok = False
            unified_errors.append("twin_quality: strict mode escalated severe mismatch")
    else:
        unified_errors.extend(unified_analysis.schema_errors)

    return _UnifiedAttemptResult(
        run=unified_run,
        errors=unified_errors,
        validation_payload=unified_validation_payload,
        twin_payload=unified_twin_payload,
        ynab_critical_ok=unified_ynab_critical_ok,
        completed_at=unified_completed_at,
        duration_ms=unified_analysis.duration_ms,
    )


def _finalize_unified_success(ctx: _ExtractionCtx, unified: _UnifiedAttemptResult) -> None:
    """Persist a successful unified extraction result and commit."""
    _create_validation(ctx.db, receipt=ctx.receipt, payload=unified.validation_payload, source="model")
    if _is_twin_payload_minimally_usable(unified.twin_payload):
        _create_model_twin(ctx.db, receipt=ctx.receipt, payload=unified.twin_payload or {})
    _set_primary_extraction_run(ctx.db, ctx.receipt.id, unified.run.id)
    unified.run.schema_errors = unified.errors

    ctx.receipt.status = ReceiptStatus.NEEDS_REVIEW.value
    ctx.receipt.status_reason = None
    ctx.receipt.extraction_completed_at = unified.completed_at
    ctx.db.add(
        TimingMetric(
            receipt_id=ctx.receipt.id,
            metric_name="extraction_duration_ms",
            metric_value_ms=unified.duration_ms,
            metadata_json={"model": ctx.settings.gemini_model, "attempt_kind": ATTEMPT_UNIFIED},
        )
    )
    ctx.db.commit()
    logger.info("Finished extraction receipt_id=%s status=%s", ctx.receipt.id, ctx.receipt.status)


def _run_fallback_and_finalize(ctx: _ExtractionCtx, unified: _UnifiedAttemptResult) -> None:
    """Run the YNAB-only and twin fallback passes, then reconcile and commit."""
    # --- fallback YNAB pass ---
    fallback_ynab_prompt = build_analysis_prompt(
        ctx.settings.gemini_prompt,
        ctx.prompt_categories,
        ctx.prompt_accounts,
        ctx.prompt_payees,
    )
    fallback_ynab_started_at = utcnow()
    fallback_ynab_analysis = ctx.analyzer.analyze_file(
        ctx.file_path,
        fallback_ynab_prompt,
        ctx.receipt.mime_type,
        response_schema=GeminiReceiptExtraction,
        route="ynab_extract.fallback_ynab",
        metadata={"receipt_id": ctx.receipt.id, "attempt_kind": ATTEMPT_FALLBACK_YNAB},
        correlation_id=ctx.receipt.id,
        limit_behavior=ctx.settings.ai_limit_behavior,
    )
    fallback_ynab_completed_at = utcnow()

    fallback_ynab_errors = list(fallback_ynab_analysis.schema_errors)
    fallback_ynab_run = _record_extraction_run(
        ctx.db,
        receipt_id=ctx.receipt.id,
        model_name=ctx.settings.gemini_model,
        prompt_text=fallback_ynab_prompt,
        analysis=fallback_ynab_analysis,
        started_at=fallback_ynab_started_at,
        completed_at=fallback_ynab_completed_at,
        attempt_kind=ATTEMPT_FALLBACK_YNAB,
        parent_run_id=unified.run.id,
        schema_errors=fallback_ynab_errors,
    )

    fallback_ynab_payload: dict[str, Any] | None = None
    fallback_ynab_valid = False
    if fallback_ynab_analysis.schema_valid and fallback_ynab_analysis.parsed_json:
        normalized_payload, is_valid, validation_errors = _validate_ynab_payload(
            fallback_ynab_analysis.parsed_json,
            default_account_id=ctx.settings.ynab_default_account_id,
            allowed_category_ids=ctx.allowed_category_ids,
            allowed_account_ids=ctx.allowed_account_ids,
        )
        if is_valid:
            fallback_ynab_payload = normalized_payload
            fallback_ynab_valid = True
        else:
            fallback_ynab_errors.extend([f"ynab_critical: {error}" for error in validation_errors])
    else:
        fallback_ynab_errors.extend(fallback_ynab_analysis.schema_errors)

    # --- fallback twin pass ---
    fallback_twin_prompt = build_twin_extraction_prompt(ctx.settings.gemini_prompt)
    fallback_twin_started_at = utcnow()
    fallback_twin_analysis = ctx.analyzer.analyze_file(
        ctx.file_path,
        fallback_twin_prompt,
        ctx.receipt.mime_type,
        response_schema=ReceiptTwinExtraction,
        route="ynab_extract.fallback_twin",
        metadata={"receipt_id": ctx.receipt.id, "attempt_kind": ATTEMPT_FALLBACK_TWIN},
        correlation_id=ctx.receipt.id,
        limit_behavior=ctx.settings.ai_limit_behavior,
    )
    fallback_twin_completed_at = utcnow()

    fallback_twin_errors = list(fallback_twin_analysis.schema_errors)
    fallback_twin_run = _record_extraction_run(
        ctx.db,
        receipt_id=ctx.receipt.id,
        model_name=ctx.settings.gemini_model,
        prompt_text=fallback_twin_prompt,
        analysis=fallback_twin_analysis,
        started_at=fallback_twin_started_at,
        completed_at=fallback_twin_completed_at,
        attempt_kind=ATTEMPT_FALLBACK_TWIN,
        parent_run_id=unified.run.id,
        schema_errors=fallback_twin_errors,
    )

    fallback_twin_payload: dict[str, Any] | None = None
    if fallback_twin_analysis.schema_valid and fallback_twin_analysis.parsed_json:
        fallback_twin_payload = _normalize_twin_payload(fallback_twin_analysis.parsed_json)
        twin_warnings, _ = _evaluate_twin_quality(
            fallback_twin_payload,
            hard_fail_delta_abs=ctx.settings.twin_recon_hard_fail_delta_abs,
            hard_fail_delta_pct=ctx.settings.twin_recon_hard_fail_delta_pct,
        )
        if twin_warnings:
            fallback_twin_errors.extend([f"twin_quality: {warning}" for warning in twin_warnings])
    else:
        fallback_twin_errors.extend(fallback_twin_analysis.schema_errors)

    # --- reconcile twin reality into YNAB payload ---
    final_validation_payload = fallback_ynab_payload
    if fallback_ynab_valid and final_validation_payload is not None and _is_twin_payload_minimally_usable(fallback_twin_payload):
        final_validation_payload, disagreements = _apply_twin_reality_to_validation(
            final_validation_payload,
            fallback_twin_payload or {},
        )
        if disagreements:
            fallback_ynab_run.parsed_json = _attach_traceability(
                fallback_ynab_run.parsed_json,
                "reality_field_disagreement",
                disagreements,
            )
            fallback_twin_run.parsed_json = _attach_traceability(
                fallback_twin_run.parsed_json,
                "reality_field_disagreement",
                disagreements,
            )

    # --- final validation and commit ---
    if fallback_ynab_valid and final_validation_payload is not None:
        normalized_payload, is_valid, validation_errors = validate_payload(
            final_validation_payload,
            allowed_category_ids=ctx.allowed_category_ids,
            allowed_account_ids=ctx.allowed_account_ids,
            allow_unknown_account=True,
        )
        if is_valid:
            _create_validation(ctx.db, receipt=ctx.receipt, payload=normalized_payload, source="model")
            if _is_twin_payload_minimally_usable(fallback_twin_payload):
                _create_model_twin(ctx.db, receipt=ctx.receipt, payload=fallback_twin_payload or {})
            _set_primary_extraction_run(ctx.db, ctx.receipt.id, fallback_ynab_run.id)
            ctx.receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            ctx.receipt.status_reason = None
            ctx.receipt.extraction_completed_at = max(fallback_ynab_completed_at, fallback_twin_completed_at)
            ctx.db.add(
                TimingMetric(
                    receipt_id=ctx.receipt.id,
                    metric_name="extraction_duration_ms",
                    metric_value_ms=fallback_ynab_analysis.duration_ms,
                    metadata_json={"model": ctx.settings.gemini_model, "attempt_kind": ATTEMPT_FALLBACK_YNAB},
                )
            )
        else:
            fallback_ynab_valid = False
            fallback_ynab_errors.extend([f"ynab_critical: {error}" for error in validation_errors])

    if not fallback_ynab_valid:
        ctx.receipt.status = ReceiptStatus.ERROR_EXTRACT.value
        ctx.receipt.status_reason = _summarize_errors(
            [
                ("unified", unified.errors),
                ("fallback_ynab", fallback_ynab_errors),
                ("fallback_twin", fallback_twin_errors),
            ]
        )
        ctx.receipt.extraction_completed_at = max(fallback_ynab_completed_at, fallback_twin_completed_at)

    unified.run.schema_errors = unified.errors
    fallback_ynab_run.schema_errors = fallback_ynab_errors
    fallback_twin_run.schema_errors = fallback_twin_errors

    ctx.db.commit()
    logger.info("Finished extraction receipt_id=%s status=%s", ctx.receipt.id, ctx.receipt.status)


def run_extraction_job(receipt_id: str) -> None:
    settings = get_settings()

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        if receipt is None:
            logger.warning("Receipt %s not found for extraction", receipt_id)
            return

        extraction_started_at = utcnow()
        receipt.status = ReceiptStatus.EXTRACTING.value
        receipt.status_reason = None
        receipt.extraction_started_at = extraction_started_at
        db.commit()

        try:
            if not settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY is not configured")
            logger.info("Starting Gemini extraction receipt_id=%s", receipt.id)

            ctx = _build_extraction_ctx(db, receipt, settings)

            if not settings.twin_extraction_enabled:
                _run_simple_extraction(ctx)
                return

            unified = _run_unified_attempt(ctx)
            if unified.ynab_critical_ok and unified.validation_payload is not None:
                _finalize_unified_success(ctx, unified)
                return

            _run_fallback_and_finalize(ctx, unified)

        except Exception as exc:
            receipt.status = ReceiptStatus.ERROR_EXTRACT.value
            receipt.status_reason = str(exc)
            receipt.extraction_completed_at = utcnow()
            db.commit()
            logger.exception("Extraction job failed for receipt %s", receipt_id)


def run_sync_job(receipt_id: str, force_create: bool = False, allow_update_match: bool = True) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        try:
            sync_receipt_to_ynab(
                db,
                settings,
                receipt_id=receipt_id,
                force_create=force_create,
                allow_update_match=allow_update_match,
            )
        except Exception:
            logger.exception("YNAB sync failed for receipt %s", receipt_id)


def run_reconciliation_job() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        try:
            result = run_ynab_reconciliation(db, settings)
            db.commit()
            logger.info(
                "YNAB reconciliation completed run_id=%s scanned=%s detected=%s penalties=%s",
                result.get("run_id"),
                result.get("scanned_receipts"),
                result.get("detected_mistakes"),
                result.get("applied_penalties"),
            )
        except Exception:
            logger.exception("YNAB reconciliation failed")
