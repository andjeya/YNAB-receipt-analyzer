"""Tests for M3 type-to-organize (POST /receipts/{id}/allocation/organize).

The Gemini call is stubbed; we exercise the endpoint's materialize → validate →
persist path and its error handling, plus materialize_proposals directly.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api import receipts as receipts_api
from app.api.receipts import organize_allocation
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import Receipt, ReceiptCandidateSet, Validation, YNABCache
from app.schemas import OrganizeAllocationRequest
from app.services.category_resolution import materialize_proposals
from app.services.validation import validate_payload
from receipt_shared.gemini import GeminiAnalysisResult

ALLOWED_CATS = {"cat-1", "cat-2"}
ALLOWED_ACCTS = {"acct-1"}
CATEGORY_NAMES = {"cat-1": "Groceries", "cat-2": "Gifts"}
BASE_PAYLOAD = {
    "payee_name": "Party City", "account_id": "acct-1", "transaction_date": "2026-06-01",
    "transaction_time": None, "memo": "", "total_amount": 10.0, "category_id": "cat-1", "splits": [],
}

PROPOSALS = [
    {"label": "Gifts", "rationale": "party supplies are gifts", "confidence": 0.7, "category_id": "cat-2", "splits": []},
    {"label": "Split", "rationale": "meal split", "confidence": 0.5, "category_id": None,
     "splits": [{"category_id": "cat-1", "amount": 5.0, "memo": ""}, {"category_id": "cat-2", "amount": 5.0, "memo": ""}]},
]


def _result(parsed: dict | None, *, valid: bool = True) -> GeminiAnalysisResult:
    return GeminiAnalysisResult(
        raw_output="", parsed_json=parsed, schema_valid=valid, schema_errors=[],
        duration_ms=1, parse_source="response_schema", structured_output_available=True,
    )


class _FakeAnalyzer:
    """Stand-in for GeminiAnalyzer that returns canned proposals."""

    next_result: GeminiAnalysisResult | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def analyze_text(self, *args: Any, **kwargs: Any) -> GeminiAnalysisResult:
        assert _FakeAnalyzer.next_result is not None
        return _FakeAnalyzer.next_result


def _settings_with_key(base: Settings) -> Settings:
    return base.model_copy(update={"gemini_api_key": "test-key"})


def _add_cat2(db: Any, settings: Settings) -> None:
    db.add(YNABCache(
        budget_id=settings.ynab_budget_id, entity_type=YNABCacheEntityType.CATEGORY.value,
        entity_id="cat-2", name="Gifts", group_name=None, raw_json={"id": "cat-2"},
    ))
    db.flush()


def _make_receipt(db: Any, rid: str) -> Receipt:
    receipt = Receipt(
        id=rid, storage_key=f"r/{rid}", original_filename=f"{rid}.jpg", file_hash=f"h-{rid}",
        file_ext=".jpg", mime_type="image/jpeg", file_size_bytes=10,
        status=ReceiptStatus.NEEDS_REVIEW.value, latest_validation_version=1,
    )
    db.add(receipt)
    db.add(Validation(receipt_id=rid, version=1, source="model", payload=dict(BASE_PAYLOAD),
                      allocation_workspace=None, is_valid=True, errors=[]))
    db.flush()
    return receipt


# --- materialize_proposals --------------------------------------------------

def test_materialize_proposals_validates_and_orders() -> None:
    out = materialize_proposals(
        BASE_PAYLOAD, PROPOSALS, twin_payload=None, twin_version=0, category_names=CATEGORY_NAMES,
        allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS,
    )
    assert [c["category_id"] for c in out] == ["cat-2", None]  # order preserved
    assert out[0]["provenance"] == "user_instruction"
    # The split proposal sums to total and validates.
    split = out[1]
    mat = {**BASE_PAYLOAD, "category_id": None, "splits": split["splits"]}
    _, is_valid, _ = validate_payload(mat, allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS)
    assert is_valid


def test_materialize_proposals_redistributes_nonsumming_split_to_exact_total() -> None:
    # Model amounts are treated as WEIGHTS; the result is re-apportioned to the
    # exact total in milliunits (the model's literal amounts never reach the POST).
    out = materialize_proposals(
        BASE_PAYLOAD,
        [{"splits": [{"category_id": "cat-1", "amount": 3.0}, {"category_id": "cat-2", "amount": 4.0}]}],
        twin_payload=None, twin_version=0, category_names=CATEGORY_NAMES,
        allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS,
    )
    assert len(out) == 1  # kept (redistributed), not dropped
    assert sum(s["amount"] for s in out[0]["splits"]) == 10.0
    mat = {**BASE_PAYLOAD, "category_id": None, "splits": out[0]["splits"]}
    _, is_valid, _ = validate_payload(mat, allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS)
    assert is_valid


def test_materialize_proposals_drops_invalid() -> None:
    out = materialize_proposals(
        BASE_PAYLOAD,
        [{"category_id": "cat-gone", "splits": []}, {"category_id": "cat-2", "splits": []}],
        twin_payload=None, twin_version=0, category_names=CATEGORY_NAMES,
        allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS,
    )
    assert [c["category_id"] for c in out] == ["cat-2"]


# --- organize endpoint ------------------------------------------------------

def test_organize_returns_transient_proposals(db_with_cache: Any, test_settings: Settings, monkeypatch: Any) -> None:
    _add_cat2(db_with_cache, test_settings)
    _make_receipt(db_with_cache, "org-1")
    db_with_cache.commit()
    monkeypatch.setattr(receipts_api, "GeminiAnalyzer", _FakeAnalyzer)
    _FakeAnalyzer.next_result = _result({"proposals": PROPOSALS})

    out = organize_allocation(
        "org-1", OrganizeAllocationRequest(instruction="party supplies to gifts"),
        db_with_cache, _settings_with_key(test_settings),
    )

    assert [p.category_id for p in out.proposals] == ["cat-2", None]
    assert out.proposals[1].splits and sum(s["amount"] for s in out.proposals[1].splits) == 10.0
    # Transient: nothing persisted, so no stray has_candidates side effect.
    assert db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id="org-1").count() == 0


def test_organize_no_valid_proposal_is_422(db_with_cache: Any, test_settings: Settings, monkeypatch: Any) -> None:
    receipt = _make_receipt(db_with_cache, "org-2")
    db_with_cache.commit()
    monkeypatch.setattr(receipts_api, "GeminiAnalyzer", _FakeAnalyzer)
    _FakeAnalyzer.next_result = _result({"proposals": []})

    with pytest.raises(HTTPException) as exc:
        organize_allocation("org-2", OrganizeAllocationRequest(instruction="nonsense"),
                            db_with_cache, _settings_with_key(test_settings))
    assert exc.value.status_code == 422
    assert db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id="org-2").count() == 0


def test_organize_unparsable_response_is_422(db_with_cache: Any, test_settings: Settings, monkeypatch: Any) -> None:
    _make_receipt(db_with_cache, "org-3")
    db_with_cache.commit()
    monkeypatch.setattr(receipts_api, "GeminiAnalyzer", _FakeAnalyzer)
    _FakeAnalyzer.next_result = _result(None, valid=False)

    with pytest.raises(HTTPException) as exc:
        organize_allocation("org-3", OrganizeAllocationRequest(instruction="x"),
                            db_with_cache, _settings_with_key(test_settings))
    assert exc.value.status_code == 422


def test_organize_without_api_key_is_503(db_with_cache: Any, test_settings: Settings) -> None:
    _make_receipt(db_with_cache, "org-4")
    db_with_cache.commit()
    # Force the key null regardless of any GEMINI_API_KEY in the environment.
    no_key = test_settings.model_copy(update={"gemini_api_key": None})
    with pytest.raises(HTTPException) as exc:
        organize_allocation("org-4", OrganizeAllocationRequest(instruction="x"), db_with_cache, no_key)
    assert exc.value.status_code == 503


def test_organize_without_validation_is_409(db_with_cache: Any, test_settings: Settings) -> None:
    # Receipt with no validation draft yet.
    db_with_cache.add(Receipt(
        id="org-novalid", storage_key="r/x", original_filename="x.jpg", file_hash="h-x",
        file_ext=".jpg", mime_type="image/jpeg", file_size_bytes=10,
        status=ReceiptStatus.NEEDS_REVIEW.value, latest_validation_version=0,
    ))
    db_with_cache.commit()
    with pytest.raises(HTTPException) as exc:
        organize_allocation("org-novalid", OrganizeAllocationRequest(instruction="x"),
                            db_with_cache, _settings_with_key(test_settings))
    assert exc.value.status_code == 409


def test_organize_missing_receipt_is_404(db_with_cache: Any, test_settings: Settings) -> None:
    with pytest.raises(HTTPException) as exc:
        organize_allocation("nope", OrganizeAllocationRequest(instruction="x"),
                            db_with_cache, _settings_with_key(test_settings))
    assert exc.value.status_code == 404
