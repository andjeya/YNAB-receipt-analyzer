from __future__ import annotations

import json
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.receipts import (
    confirm_receipt_twin_section,
    get_receipt_detail,
    get_receipt_twin,
    save_draft,
    save_receipt_twin,
)
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.jobs import tasks
from app.models import Base, ExtractionRun, Receipt, ReceiptTwin, Validation, YNABCache
from app.schemas import SaveDraftRequest, SaveTwinRequest, TwinConfirmRequest
from receipt_shared.gemini import GeminiAnalysisResult


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
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-2",
                name="Household",
                group_name="Everyday",
                raw_json={"id": "cat-2", "name": "Household"},
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


def _valid_payload(*, category_id: str = "cat-1") -> dict[str, object]:
    return {
        "payee_name": "Store",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": "10:30",
        "memo": "Imported",
        "total_amount": 12.5,
        "transaction_kind": "purchase",
        "category_id": category_id,
        "splits": [],
    }


def _valid_twin_payload(
    *,
    transaction_date: str = "2026-02-15",
    transaction_time: str | None = "10:30",
    total_amount: float = 12.5,
) -> dict[str, object]:
    return {
        "store_name": "Store",
        "store_address": "Address",
        "transaction_date": transaction_date,
        "transaction_time": transaction_time,
        "currency": "USD",
        "line_items": [
            {
                "index": 0,
                "raw_text": "ITEM",
                "translated_text": "ITEM",
                "quantity": 1,
                "unit_price": float(total_amount),
                "line_total": float(total_amount),
                "tax_code": None,
                "item_type": "product",
            }
        ],
        "subtotal": float(total_amount),
        "tax_total": 0.0,
        "total_amount": float(total_amount),
        "payment_method": "card",
        "receipt_language": "en",
    }


def _unified_payload(
    *,
    account_id: str = "acct-1",
    category_id: str = "cat-1",
    transaction_date: str = "2026-02-15",
    transaction_time: str | None = "10:30",
    total_amount: float = 12.5,
) -> dict[str, object]:
    payload = _valid_twin_payload(
        transaction_date=transaction_date,
        transaction_time=transaction_time,
        total_amount=total_amount,
    )
    payload.update(
        {
            "payee_name": "Store",
            "account_id": account_id,
            "memo": "Imported",
            "category_id": category_id,
            "splits": [],
            "category_ambiguity_flags": [],
        }
    )
    return payload


def _analysis(parsed_json: dict[str, object] | None, *, schema_valid: bool = True, errors: list[str] | None = None) -> GeminiAnalysisResult:
    raw_output = json.dumps(parsed_json) if parsed_json is not None else ""
    return GeminiAnalysisResult(
        raw_output=raw_output,
        parsed_json=parsed_json,
        schema_valid=schema_valid,
        schema_errors=errors or [],
        duration_ms=42,
        parse_source="response_schema",
        structured_output_available=schema_valid,
    )


class _SessionContext(AbstractContextManager[Session]):
    def __init__(self, session: Session):
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False


class _FakeAnalyzer:
    responses: list[GeminiAnalysisResult] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def analyze_file(  # noqa: ANN001
        self,
        _file_path: Path,
        _prompt_text: str,
        _mime_type: str | None = None,
        response_schema=None,
        **_kwargs,
    ):
        if not self.responses:
            raise AssertionError("No fake Gemini response queued")
        return self.responses.pop(0)


def _seed_receipt(db: Session, receipt_id: str = "11111111-2222-4333-8444-555555555555") -> Receipt:
    receipt = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename="receipt.jpg",
        file_hash=f"hash-{receipt_id}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=123,
        status=ReceiptStatus.INGESTED.value,
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def _patch_extraction_environment(monkeypatch: pytest.MonkeyPatch, db: Session, tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        gemini_model="test-model",
        object_store_root=tmp_path,
        twin_extraction_enabled=True,
        ynab_default_account_id="acct-1",
    )

    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: _SessionContext(db))
    monkeypatch.setattr(tasks, "GeminiAnalyzer", _FakeAnalyzer)
    monkeypatch.setattr(
        tasks,
        "get_cached_reference_data",
        lambda _db, _settings: {
            "categories": [SimpleNamespace(entity_id="cat-1", name="Groceries", group_name="Everyday")],
            "accounts": [SimpleNamespace(entity_id="acct-1", raw_json={"id": "acct-1", "name": "Checking"})],
            "payees": [SimpleNamespace(name="Store")],
        },
    )


def test_run_extraction_unified_success_creates_primary_twin_and_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    with _memory_session() as db:
        receipt = _seed_receipt(db)
        file_path = tmp_path / receipt.storage_key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"fake")

        _patch_extraction_environment(monkeypatch, db, tmp_path)
        _FakeAnalyzer.responses = [_analysis(_unified_payload())]

        tasks.run_extraction_job(receipt.id)

        refreshed = db.get(Receipt, receipt.id)
        assert refreshed is not None
        assert refreshed.status == ReceiptStatus.NEEDS_REVIEW.value
        assert refreshed.latest_validation_version == 1
        assert refreshed.latest_twin_version == 1

        runs = list(db.scalars(select(ExtractionRun).where(ExtractionRun.receipt_id == receipt.id).order_by(ExtractionRun.id.asc())))
        assert len(runs) == 1
        assert runs[0].attempt_kind == tasks.ATTEMPT_UNIFIED
        assert runs[0].is_primary_result is True

        validation = db.scalar(
            select(Validation)
            .where(Validation.receipt_id == receipt.id)
            .order_by(Validation.version.desc())
            .limit(1)
        )
        assert validation is not None
        assert validation.is_valid is True

        twin = db.scalar(
            select(ReceiptTwin)
            .where(ReceiptTwin.receipt_id == receipt.id)
            .order_by(ReceiptTwin.version.desc())
            .limit(1)
        )
        assert twin is not None
        assert twin.confirmed_sections == {"date_time": False, "total": False}


def test_run_extraction_fallback_uses_twin_reality_fields_on_disagreement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    with _memory_session() as db:
        receipt = _seed_receipt(db, receipt_id="22222222-3333-4444-8555-666666666666")
        file_path = tmp_path / receipt.storage_key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"fake")

        _patch_extraction_environment(monkeypatch, db, tmp_path)
        _FakeAnalyzer.responses = [
            _analysis(_unified_payload(account_id="acct-missing", total_amount=20.0)),
            _analysis(_unified_payload(transaction_date="2026-02-15", total_amount=20.0)),
            _analysis(_valid_twin_payload(transaction_date="2026-02-16", total_amount=19.0, transaction_time=None)),
        ]

        tasks.run_extraction_job(receipt.id)

        refreshed = db.get(Receipt, receipt.id)
        assert refreshed is not None
        assert refreshed.status == ReceiptStatus.NEEDS_REVIEW.value

        primary_run = db.scalar(
            select(ExtractionRun)
            .where(
                ExtractionRun.receipt_id == receipt.id,
                ExtractionRun.is_primary_result.is_(True),
            )
            .limit(1)
        )
        assert primary_run is not None
        assert primary_run.attempt_kind == tasks.ATTEMPT_FALLBACK_YNAB

        all_runs = list(db.scalars(select(ExtractionRun).where(ExtractionRun.receipt_id == receipt.id).order_by(ExtractionRun.id.asc())))
        assert [run.attempt_kind for run in all_runs] == [
            tasks.ATTEMPT_UNIFIED,
            tasks.ATTEMPT_FALLBACK_YNAB,
            tasks.ATTEMPT_FALLBACK_TWIN,
        ]
        assert all_runs[1].parent_run_id == all_runs[0].id
        assert all_runs[2].parent_run_id == all_runs[0].id

        latest_validation = db.scalar(
            select(Validation)
            .where(Validation.receipt_id == receipt.id)
            .order_by(Validation.version.desc())
            .limit(1)
        )
        assert latest_validation is not None
        assert latest_validation.payload["transaction_date"] == "2026-02-16"
        assert latest_validation.payload["total_amount"] == 19.0

        traceability = (all_runs[1].parsed_json or {}).get("_traceability", {})
        assert "reality_field_disagreement" in traceability


def test_save_draft_enforces_confirmed_twin_locks_and_preserves_synced_when_payload_unchanged():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = Receipt(
            id="33333333-4444-4555-8666-777777777777",
            storage_key="receipts/33333333-4444-4555-8666-777777777777.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-3",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=123,
            status=ReceiptStatus.SYNCED.value,
            latest_validation_version=1,
            latest_twin_version=1,
        )
        db.add(receipt)
        db.add(
            Validation(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload={**_valid_payload(), "transaction_time": "10:30:00"},
                is_valid=True,
                errors=None,
            )
        )
        db.add(
            ReceiptTwin(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload=_valid_twin_payload(),
                confirmed_sections={"date_time": True, "total": True},
            )
        )
        db.commit()

        response = save_draft(
            receipt_id=receipt.id,
            request=SaveDraftRequest(
                payload={
                    **_valid_payload(),
                    "transaction_date": "2026-02-01",
                    "transaction_time": "01:00",
                    "total_amount": 99.99,
                },
                source="user",
            ),
            db=db,
            settings=settings,
        )

        latest_validation = db.scalar(
            select(Validation)
            .where(Validation.receipt_id == receipt.id)
            .order_by(Validation.version.desc())
            .limit(1)
        )
        assert latest_validation is not None
        assert latest_validation.payload["transaction_date"] == "2026-02-15"
        assert latest_validation.payload["transaction_time"] == "10:30:00"
        assert latest_validation.payload["total_amount"] == 12.5
        assert len(response.lock_warnings) == 3

        refreshed = db.get(Receipt, receipt.id)
        assert refreshed is not None
        assert refreshed.status == ReceiptStatus.SYNCED.value


def test_save_twin_returns_409_on_stale_base_version():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = Receipt(
            id="44444444-5555-4666-8777-888888888888",
            storage_key="receipts/44444444-5555-4666-8777-888888888888.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-4",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=123,
            status=ReceiptStatus.NEEDS_REVIEW.value,
            latest_twin_version=2,
        )
        db.add(receipt)
        db.add(
            ReceiptTwin(
                receipt_id=receipt.id,
                version=2,
                source="model",
                payload=_valid_twin_payload(),
                confirmed_sections={"date_time": False, "total": False},
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc:
            save_receipt_twin(
                receipt_id=receipt.id,
                request=SaveTwinRequest(base_version=1, payload=_valid_twin_payload()),
                db=db,
                settings=settings,
            )

        assert exc.value.status_code == 409


def test_confirm_twin_idempotent_when_state_already_matches():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = Receipt(
            id="55555555-6666-4777-8888-999999999999",
            storage_key="receipts/55555555-6666-4777-8888-999999999999.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-5",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=123,
            status=ReceiptStatus.NEEDS_REVIEW.value,
            latest_validation_version=1,
            latest_twin_version=1,
        )
        db.add(receipt)
        db.add(
            Validation(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload=_valid_payload(),
                is_valid=True,
                errors=None,
            )
        )
        db.add(
            ReceiptTwin(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload=_valid_twin_payload(),
                confirmed_sections={"date_time": False, "total": False},
            )
        )
        db.commit()

        response = confirm_receipt_twin_section(
            receipt_id=receipt.id,
            request=TwinConfirmRequest(section="date_time", confirmed=False),
            db=db,
            settings=settings,
        )

        assert response.validation is None
        refreshed = db.get(Receipt, receipt.id)
        assert refreshed is not None
        assert refreshed.latest_validation_version == 1


def test_confirm_twin_section_updates_validation_and_transitions_synced_receipt():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = Receipt(
            id="66666666-7777-4888-8999-aaaaaaaaaaaa",
            storage_key="receipts/66666666-7777-4888-8999-aaaaaaaaaaaa.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-6",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=123,
            status=ReceiptStatus.SYNCED.value,
            latest_validation_version=1,
            latest_twin_version=1,
        )
        db.add(receipt)
        db.add(
            Validation(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload={**_valid_payload(), "transaction_date": "2026-02-01", "transaction_time": None},
                is_valid=True,
                errors=None,
            )
        )
        db.add(
            ReceiptTwin(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload=_valid_twin_payload(transaction_date="2026-02-15", transaction_time="11:45"),
                confirmed_sections={"date_time": False, "total": False},
            )
        )
        db.commit()

        response = confirm_receipt_twin_section(
            receipt_id=receipt.id,
            request=TwinConfirmRequest(section="date_time", confirmed=True),
            db=db,
            settings=settings,
        )

        assert response.validation is not None
        assert response.validation.payload["transaction_date"] == "2026-02-15"
        assert response.validation.payload["transaction_time"] == "11:45:00"

        refreshed = db.get(Receipt, receipt.id)
        assert refreshed is not None
        assert refreshed.latest_validation_version == 2
        assert refreshed.status == ReceiptStatus.NEEDS_REVIEW.value


def test_receipt_detail_uses_primary_extraction_and_twin_missing_is_degraded_404():
    with _memory_session() as db:
        receipt = Receipt(
            id="77777777-8888-4999-8aaa-bbbbbbbbbbbb",
            storage_key="receipts/77777777-8888-4999-8aaa-bbbbbbbbbbbb.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-7",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=123,
            status=ReceiptStatus.NEEDS_REVIEW.value,
        )
        db.add(receipt)

        run_primary = ExtractionRun(
            receipt_id=receipt.id,
            model_name="model",
            prompt_text="p1",
            raw_output="{}",
            parsed_json={"a": 1},
            schema_valid=True,
            schema_errors=[],
            duration_ms=12,
            attempt_kind="fallback_ynab",
            is_primary_result=True,
            parent_run_id=None,
            started_at=datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 2, 15, 10, 1, tzinfo=timezone.utc),
            created_at=datetime(2026, 2, 15, 10, 1, tzinfo=timezone.utc),
        )
        run_latest = ExtractionRun(
            receipt_id=receipt.id,
            model_name="model",
            prompt_text="p2",
            raw_output="{}",
            parsed_json={"a": 2},
            schema_valid=True,
            schema_errors=[],
            duration_ms=13,
            attempt_kind="unified",
            is_primary_result=False,
            parent_run_id=None,
            started_at=datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 2, 16, 10, 1, tzinfo=timezone.utc),
            created_at=datetime(2026, 2, 16, 10, 1, tzinfo=timezone.utc),
        )
        db.add(run_primary)
        db.add(run_latest)
        db.commit()

        detail = get_receipt_detail(receipt.id, db=db)
        assert detail.latest_extraction is not None
        assert detail.extraction_primary is not None
        assert detail.latest_extraction.id == run_latest.id
        assert detail.extraction_primary.id == run_primary.id
        assert detail.latest_twin is None
        assert detail.locked_fields.total_amount is False

        with pytest.raises(HTTPException) as exc:
            get_receipt_twin(receipt.id, db=db)
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "twin_unavailable"
