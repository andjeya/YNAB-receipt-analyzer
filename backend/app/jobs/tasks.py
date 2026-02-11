from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, select

from app.config import get_settings
from app.db import SessionLocal
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import ExtractionRun, Receipt, TimingMetric, Validation, YNABCache
from app.services.validation import build_initial_validation_payload, validate_payload
from app.services.ynab import refresh_ynab_cache, sync_receipt_to_ynab
from receipt_shared.gemini import GeminiAnalyzer, build_analysis_prompt
from receipt_shared.money import dollars_to_milliunits
from receipt_shared.ynab_client import Category

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_extraction_job(receipt_id: str) -> None:
    settings = get_settings()

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        if receipt is None:
            logger.warning("Receipt %s not found for extraction", receipt_id)
            return

        started_at = utcnow()
        receipt.status = ReceiptStatus.EXTRACTING.value
        receipt.status_reason = None
        receipt.extraction_started_at = started_at
        db.commit()

        try:
            if not settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY is not configured")

            categories = list(
                db.scalars(
                    select(YNABCache).where(
                        and_(
                            YNABCache.entity_type == YNABCacheEntityType.CATEGORY.value,
                            YNABCache.budget_id == (settings.ynab_budget_id or ""),
                        )
                    )
                )
            )
            if not categories and settings.ynab_access_token and settings.ynab_budget_id:
                refresh_ynab_cache(db, settings)
                categories = list(
                    db.scalars(
                        select(YNABCache).where(
                            and_(
                                YNABCache.entity_type == YNABCacheEntityType.CATEGORY.value,
                                YNABCache.budget_id == settings.ynab_budget_id,
                            )
                        )
                    )
                )

            prompt_categories = [
                Category(id=item.entity_id, name=item.name, group_name=item.group_name or "Uncategorized")
                for item in categories
            ]
            prompt_text = build_analysis_prompt(settings.gemini_prompt, prompt_categories)

            file_path = Path(settings.object_store_root) / receipt.storage_key
            analyzer = GeminiAnalyzer(settings.gemini_api_key, settings.gemini_model)
            analysis = analyzer.analyze_file(file_path, prompt_text, receipt.mime_type)
            completed_at = utcnow()

            run = ExtractionRun(
                receipt_id=receipt.id,
                model_name=settings.gemini_model,
                prompt_text=prompt_text,
                raw_output=analysis.raw_output,
                parsed_json=analysis.parsed_json,
                schema_valid=analysis.schema_valid,
                schema_errors=analysis.schema_errors,
                duration_ms=analysis.duration_ms,
                started_at=started_at,
                completed_at=completed_at,
            )
            db.add(run)

            if analysis.schema_valid and analysis.parsed_json:
                payload = build_initial_validation_payload(analysis.parsed_json, settings.ynab_default_account_id)
                normalized_payload, is_valid, errors = validate_payload(payload)
                validation = Validation(
                    receipt_id=receipt.id,
                    version=receipt.latest_validation_version + 1,
                    source="model",
                    payload=normalized_payload,
                    is_valid=is_valid,
                    errors=errors,
                )
                db.add(validation)

                receipt.latest_validation_version += 1
                receipt.status = ReceiptStatus.NEEDS_REVIEW.value
                receipt.status_reason = None
                receipt.extraction_completed_at = completed_at
                receipt.display_payee_name = normalized_payload.get("payee_name")
                receipt.display_total_milliunits = dollars_to_milliunits(normalized_payload.get("total_amount", 0), outflow=False)
                if normalized_payload.get("transaction_date"):
                    receipt.display_receipt_date = datetime.fromisoformat(normalized_payload["transaction_date"]).date()

                db.add(
                    TimingMetric(
                        receipt_id=receipt.id,
                        metric_name="extraction_duration_ms",
                        metric_value_ms=analysis.duration_ms,
                        metadata_json={"model": settings.gemini_model},
                    )
                )
            else:
                receipt.status = ReceiptStatus.ERROR_EXTRACT.value
                receipt.status_reason = "; ".join(analysis.schema_errors)
                receipt.extraction_completed_at = completed_at

            db.commit()
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
