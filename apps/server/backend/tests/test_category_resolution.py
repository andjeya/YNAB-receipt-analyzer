"""Tests for M1 candidate generation (category_resolution + worker persistence)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from receipt_shared.contracts import UnifiedReceiptExtraction

from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.jobs.tasks import _ExtractionCtx, _persist_candidate_set
from app.models import PayeeCategoryMemory, Receipt, ReceiptCandidateSet, Validation, YNABCache
from app.services.category_resolution import (
    _distribute_milliunits,
    build_candidate_arrangements,
    materialize_arrangement,
    payee_category_hint_text,
)
from app.services.validation import validate_payload
from receipt_shared.money import dollars_to_milliunits

ALLOWED_CATS = {"cat-1", "cat-2", "cat-3"}
ALLOWED_ACCTS = {"acct-1"}
CATEGORY_NAMES = {"cat-1": "Groceries", "cat-2": "Household", "cat-3": "Dining Out"}

BASE_PAYLOAD = {
    "payee_name": "Costco",
    "account_id": "acct-1",
    "transaction_date": "2026-06-01",
    "transaction_time": None,
    "memo": "",
    "total_amount": 10.0,
    "category_id": "cat-1",
    "splits": [],
}


# --- _distribute_milliunits -------------------------------------------------

def test_distribute_sums_exactly_to_total() -> None:
    amounts = _distribute_milliunits(10.0, [1.0, 1.0, 1.0])  # 10/3 doesn't divide evenly
    mu = sum(dollars_to_milliunits(a, outflow=False) for a in amounts)
    assert mu == dollars_to_milliunits(10.0, outflow=False)


def test_distribute_equal_when_all_zero_weights() -> None:
    amounts = _distribute_milliunits(9.0, [0.0, 0.0, 0.0])
    assert sum(dollars_to_milliunits(a, outflow=False) for a in amounts) == 9000
    assert all(a > 0 for a in amounts)


def test_distribute_respects_weight_proportions() -> None:
    amounts = _distribute_milliunits(10.0, [6.0, 4.0])
    assert amounts[0] == 6.0 and amounts[1] == 4.0


# --- materialize_arrangement ------------------------------------------------

def test_materialize_single_category() -> None:
    mat = materialize_arrangement(BASE_PAYLOAD, {"category_id": "cat-2", "splits": []}, allowed_category_ids=ALLOWED_CATS)
    assert mat is not None and mat["category_id"] == "cat-2" and mat["splits"] == []
    assert mat["account_id"] == "acct-1" and mat["total_amount"] == 10.0


def test_materialize_split_sums_to_total_and_validates() -> None:
    arrangement = {"splits": [{"category_id": "cat-1", "amount": 3.33}, {"category_id": "cat-2", "amount": 6.67}]}
    mat = materialize_arrangement(BASE_PAYLOAD, arrangement, allowed_category_ids=ALLOWED_CATS)
    assert mat is not None and mat["category_id"] is None
    _, is_valid, errors = validate_payload(mat, allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS)
    assert is_valid, errors


def test_materialize_unknown_category_returns_none() -> None:
    assert materialize_arrangement(BASE_PAYLOAD, {"category_id": "cat-gone", "splits": []}, allowed_category_ids=ALLOWED_CATS) is None
    bad_split = {"splits": [{"category_id": "cat-1", "amount": 5.0}, {"category_id": "cat-gone", "amount": 5.0}]}
    assert materialize_arrangement(BASE_PAYLOAD, bad_split, allowed_category_ids=ALLOWED_CATS) is None


# --- build_candidate_arrangements -------------------------------------------

def _build(gemini: list[dict], flags: list[dict] | None = None) -> list[dict]:
    return build_candidate_arrangements(
        BASE_PAYLOAD,
        gemini,
        ambiguity_flags=flags or [{"line_item": "x", "candidate_category_ids": ["cat-1", "cat-2"], "confidence": 0.8}],
        twin_payload=None,
        twin_version=0,
        category_names=CATEGORY_NAMES,
        allowed_category_ids=ALLOWED_CATS,
        allowed_account_ids=ALLOWED_ACCTS,
    )


def test_build_primary_is_first_and_labeled() -> None:
    arr = _build([{"category_id": "cat-2", "confidence": 0.4, "label": "Household"}])
    assert arr[0]["category_id"] == "cat-1"  # primary
    assert arr[0]["provenance"] == "model_primary"
    assert arr[0]["label"] == "Groceries"
    assert arr[1]["category_id"] == "cat-2"


def test_build_dedupes_alt_equal_to_primary() -> None:
    arr = _build([{"category_id": "cat-1", "confidence": 0.4}])  # same as primary
    assert len(arr) == 1


def test_build_caps_at_three_and_drops_invalid() -> None:
    arr = _build([
        {"category_id": "cat-2", "confidence": 0.5},
        {"category_id": "cat-3", "confidence": 0.4},
        {"category_id": "cat-gone", "confidence": 0.6},  # invalid → dropped
        {"splits": [{"category_id": "cat-1", "amount": 5.0}, {"category_id": "cat-2", "amount": 5.0}], "confidence": 0.3},
    ])
    assert len(arr) == 3
    for cand in arr:
        mat = {**BASE_PAYLOAD, "category_id": cand["category_id"], "splits": cand["splits"]}
        _, is_valid, _ = validate_payload(mat, allowed_category_ids=ALLOWED_CATS, allowed_account_ids=ALLOWED_ACCTS)
        assert is_valid


def test_build_orders_alternatives_by_confidence() -> None:
    arr = _build([
        {"category_id": "cat-3", "confidence": 0.2},
        {"category_id": "cat-2", "confidence": 0.7},
    ])
    assert [a["category_id"] for a in arr] == ["cat-1", "cat-2", "cat-3"]


# --- payee_category_hint_text -----------------------------------------------

def test_hint_text_empty_when_no_memory(db_with_cache: Any, test_settings: Settings) -> None:
    assert payee_category_hint_text(db_with_cache, test_settings.ynab_budget_id, CATEGORY_NAMES) == ""


def test_hint_text_lists_learned_pairs(db_with_cache: Any, test_settings: Settings) -> None:
    db_with_cache.add(
        PayeeCategoryMemory(budget_id=test_settings.ynab_budget_id, payee_key="costco", category_id="cat-1")
    )
    db_with_cache.commit()
    text = payee_category_hint_text(db_with_cache, test_settings.ynab_budget_id, CATEGORY_NAMES)
    assert "costco" in text and "Groceries" in text


# --- _persist_candidate_set (worker integration) ----------------------------

def _ctx(db: Any, receipt: Receipt, settings: Settings) -> _ExtractionCtx:
    return _ExtractionCtx(
        db=db,
        receipt=receipt,
        settings=settings,
        analyzer=None,  # type: ignore[arg-type]
        file_path=Path("."),
        allowed_category_ids=ALLOWED_CATS,
        allowed_account_ids=ALLOWED_ACCTS,
        prompt_categories=[],
        prompt_accounts=[],
        prompt_payees=[],
        category_names=CATEGORY_NAMES,
        category_hints="",
    )


def _make_receipt(db: Any, rid: str) -> Receipt:
    receipt = Receipt(
        id=rid, storage_key=f"r/{rid}", original_filename=f"{rid}.jpg", file_hash=f"h-{rid}",
        file_ext=".jpg", mime_type="image/jpeg", file_size_bytes=10,
        status=ReceiptStatus.NEEDS_REVIEW.value, latest_validation_version=1,
    )
    db.add(receipt)
    db.flush()
    return receipt


def test_persist_creates_candidate_set(db_with_cache: Any, test_settings: Settings) -> None:
    db_with_cache.add(YNABCache(
        budget_id=test_settings.ynab_budget_id, entity_type=YNABCacheEntityType.CATEGORY.value,
        entity_id="cat-2", name="Household", group_name=None, raw_json={"id": "cat-2"},
    ))
    receipt = _make_receipt(db_with_cache, "cr-persist-1")
    db_with_cache.commit()

    _persist_candidate_set(
        _ctx(db_with_cache, receipt, test_settings),
        base_payload=dict(BASE_PAYLOAD),
        gemini_candidates=[{"category_id": "cat-2", "confidence": 0.4}],
        ambiguity_flags=[{"line_item": "x", "candidate_category_ids": ["cat-1", "cat-2"], "confidence": 0.8}],
        twin_payload=None,
        twin_version=0,
        base_validation_version=1,
    )
    db_with_cache.commit()

    cs = db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id=receipt.id).one()
    assert cs.version == 1 and cs.source == "model_topk" and cs.chosen_index is None
    assert cs.base_validation_version == 1
    assert len(cs.candidates) == 2


def test_persist_skips_when_not_uncertain(db_with_cache: Any, test_settings: Settings) -> None:
    receipt = _make_receipt(db_with_cache, "cr-persist-2")
    db_with_cache.commit()
    _persist_candidate_set(
        _ctx(db_with_cache, receipt, test_settings),
        base_payload=dict(BASE_PAYLOAD),
        gemini_candidates=[],
        ambiguity_flags=[],  # not uncertain
        twin_payload=None, twin_version=0, base_validation_version=1,
    )
    db_with_cache.commit()
    assert db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id=receipt.id).count() == 0


def test_unified_extraction_survives_garbage_candidates() -> None:
    """A malformed candidate_arrangements must NEVER fail the core extraction."""
    base = {"account_id": "acct-1", "total_amount": 10.0, "category_id": "cat-1"}
    for garbage in ["nope", None, [1, 2, 3], {"a": 1}, [["x"]]]:
        model = UnifiedReceiptExtraction.model_validate({**base, "candidate_arrangements": garbage})
        assert model.category_id == "cat-1"
        assert model.candidate_arrangements == []

    # Wrong leaf types are coerced, not rejected — one usable (sanitized) arrangement.
    model = UnifiedReceiptExtraction.model_validate({
        **base,
        "candidate_arrangements": [
            {"category_id": 12345, "splits": "nope", "confidence": "high", "label": None, "rationale": 7}
        ],
    })
    assert model.category_id == "cat-1"  # primary untouched
    assert len(model.candidate_arrangements) == 1
    arr = model.candidate_arrangements[0]
    assert arr.category_id is None and arr.splits == [] and arr.confidence == 0.0
    assert arr.label == "" and arr.rationale == "7"


def test_distribute_handles_non_finite_weights() -> None:
    amounts = _distribute_milliunits(10.0, [float("inf"), 5.0])
    assert sum(dollars_to_milliunits(a, outflow=False) for a in amounts) == 10000


def test_build_does_not_mutate_base_payload() -> None:
    base = dict(BASE_PAYLOAD)
    snapshot = copy.deepcopy(base)
    build_candidate_arrangements(
        base,
        [{"splits": [{"category_id": "cat-1", "amount": float("inf")}, {"category_id": "cat-2", "amount": 5.0}]}],
        ambiguity_flags=[{"line_item": "x", "candidate_category_ids": ["cat-1", "cat-2"], "confidence": 0.8}],
        twin_payload=None,
        twin_version=0,
        category_names=CATEGORY_NAMES,
        allowed_category_ids=ALLOWED_CATS,
        allowed_account_ids=ALLOWED_ACCTS,
    )
    assert base == snapshot  # base payload + its nested splits list untouched


def test_persist_version_collision_does_not_break_extraction(
    db_with_cache: Any, test_settings: Settings, monkeypatch: Any
) -> None:
    """A (receipt_id, version) collision must be swallowed inside a savepoint and
    leave the outer session/extraction commit intact."""
    db_with_cache.add(YNABCache(
        budget_id=test_settings.ynab_budget_id, entity_type=YNABCacheEntityType.CATEGORY.value,
        entity_id="cat-2", name="Household", group_name=None, raw_json={"id": "cat-2"},
    ))
    receipt = _make_receipt(db_with_cache, "cr-collide")
    db_with_cache.add(ReceiptCandidateSet(
        receipt_id=receipt.id, version=1, source="model_topk", twin_version=None,
        base_validation_version=1, candidates=[{"category_id": "cat-1", "splits": []}], chosen_index=None,
    ))
    db_with_cache.commit()

    # Simulate the race: max(version) reads stale 0, so the insert recomputes version=1
    # and collides with the existing row at flush time.
    monkeypatch.setattr(db_with_cache, "scalar", lambda *a, **k: 0)
    _persist_candidate_set(
        _ctx(db_with_cache, receipt, test_settings),
        base_payload=dict(BASE_PAYLOAD),
        gemini_candidates=[{"category_id": "cat-2", "confidence": 0.4}],
        ambiguity_flags=[{"line_item": "x", "candidate_category_ids": ["cat-1", "cat-2"], "confidence": 0.8}],
        twin_payload=None, twin_version=0, base_validation_version=1,
    )
    monkeypatch.undo()
    # Outer session is still usable (would raise if the savepoint poisoned it).
    db_with_cache.commit()
    assert db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id=receipt.id).count() == 1


def test_persist_skips_when_only_primary_survives(db_with_cache: Any, test_settings: Settings) -> None:
    receipt = _make_receipt(db_with_cache, "cr-persist-3")
    db_with_cache.commit()
    _persist_candidate_set(
        _ctx(db_with_cache, receipt, test_settings),
        base_payload=dict(BASE_PAYLOAD),
        gemini_candidates=[{"category_id": "cat-gone", "confidence": 0.5}],  # invalid → dropped, only primary left
        ambiguity_flags=[{"line_item": "x", "candidate_category_ids": ["cat-1"], "confidence": 0.8}],
        twin_payload=None, twin_version=0, base_validation_version=1,
    )
    db_with_cache.commit()
    assert db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id=receipt.id).count() == 0
