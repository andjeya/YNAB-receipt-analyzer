"""Tests for the candidate-set choose endpoint (M0 — Quicker sync).

Covers the promotion path POST /receipts/{id}/candidates/{version}/choose:
- merges ONLY category/splits onto the current validation (money fields untouched)
- re-validates and writes a normal user Validation
- staleness guard (twin drift) and invalid-candidate rejection
- game economy: accepting a generated guess earns no water; a type_to_organize
  edit can
- detail/list wiring (candidate_set, has_candidates)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.receipts import choose_candidate, get_receipt_detail, list_receipts
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import (
    GameCorrectnessState,
    Receipt,
    ReceiptCandidateSet,
    ReceiptTwin,
    Validation,
    YNABCache,
)
from app.schemas import ChooseCandidateRequest

ACCT_ID = "acct-1"
BASE_PAYLOAD = {
    "payee_name": "Costco",
    "account_id": ACCT_ID,
    "transaction_date": "2026-06-01",
    "transaction_time": None,
    "memo": "",
    "total_amount": 10.0,
    "category_id": "cat-1",
    "splits": [],
}


def _add_cat2(db: Any, settings: Settings) -> None:
    db.add(
        YNABCache(
            budget_id=settings.ynab_budget_id,
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id="cat-2",
            name="Household Goods",
            group_name="Everyday",
            raw_json={"id": "cat-2", "name": "Household Goods"},
        )
    )
    db.flush()


def _make_receipt(db: Any, rid: str, status: str = ReceiptStatus.NEEDS_REVIEW.value) -> Receipt:
    receipt = Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename=f"{rid}.jpg",
        file_hash=f"hash-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=512,
        status=status,
        latest_validation_version=0,
    )
    db.add(receipt)
    db.flush()
    return receipt


def _add_validation(db: Any, receipt: Receipt, payload: dict, *, source: str = "model") -> Validation:
    version = receipt.latest_validation_version + 1
    v = Validation(
        receipt_id=receipt.id,
        version=version,
        source=source,
        payload=payload,
        allocation_workspace=None,
        is_valid=True,
        errors=[],
    )
    db.add(v)
    receipt.latest_validation_version = version
    db.flush()
    return v


def _add_twin(db: Any, rid: str, *, version: int = 1) -> ReceiptTwin:
    twin = ReceiptTwin(
        receipt_id=rid,
        version=version,
        source="model",
        payload={
            "store_name": "Costco",
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "total_amount": 10.0,
            "line_items": [],
        },
        confirmed_sections={"date_time": True, "total": True},
    )
    db.add(twin)
    db.flush()
    return twin


def _make_candidate_set(
    db: Any,
    rid: str,
    candidates: list[dict],
    *,
    source: str = "model_topk",
    version: int = 1,
    twin_version: int | None = None,
    base_validation_version: int = 1,
) -> ReceiptCandidateSet:
    cs = ReceiptCandidateSet(
        receipt_id=rid,
        version=version,
        source=source,
        twin_version=twin_version,
        base_validation_version=base_validation_version,
        candidates=candidates,
        chosen_index=None,
    )
    db.add(cs)
    db.flush()
    return cs


SINGLE_CANDIDATES = [
    {"label": "Groceries", "rationale": "top pick", "confidence": 0.6, "category_id": "cat-1", "splits": []},
    {"label": "Household", "rationale": "alt", "confidence": 0.3, "category_id": "cat-2", "splits": []},
]


def test_choose_single_category_writes_validation(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-single-aaaa"
    receipt = _make_receipt(db_with_cache, rid)
    _add_cat2(db_with_cache, test_settings)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES)
    db_with_cache.commit()

    resp = choose_candidate(rid, 1, ChooseCandidateRequest(index=1), db_with_cache, test_settings)

    assert resp.can_sync is True
    assert resp.validation.source == "user"
    assert resp.validation.payload["category_id"] == "cat-2"
    # Money fields preserved verbatim.
    assert resp.validation.payload["account_id"] == ACCT_ID
    assert resp.validation.payload["payee_name"] == "Costco"
    assert resp.validation.payload["transaction_date"] == "2026-06-01"
    assert resp.validation.payload["total_amount"] == 10.0
    # chosen_index recorded.
    cs = db_with_cache.query(ReceiptCandidateSet).filter_by(receipt_id=rid).one()
    assert cs.chosen_index == 1


def test_choose_split_candidate_sums_to_total(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-split-bbbb"
    _add_cat2(db_with_cache, test_settings)
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    split_candidate = [
        {
            "label": "Split",
            "rationale": "groceries + household",
            "confidence": 0.5,
            "category_id": None,
            "splits": [
                {"category_id": "cat-1", "amount": 6.0, "memo": ""},
                {"category_id": "cat-2", "amount": 4.0, "memo": ""},
            ],
        }
    ]
    _make_candidate_set(db_with_cache, rid, split_candidate)
    db_with_cache.commit()

    resp = choose_candidate(rid, 1, ChooseCandidateRequest(index=0), db_with_cache, test_settings)

    assert resp.validation.payload["category_id"] is None
    splits = resp.validation.payload["splits"]
    assert len(splits) == 2
    assert sum(s["amount"] for s in splits) == 10.0
    assert resp.can_sync is True


def test_choose_twin_drift_is_stale_409(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-stale-cccc"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    _add_twin(db_with_cache, rid, version=2)  # current twin is v2
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES, twin_version=1)  # built against v1
    db_with_cache.commit()

    with pytest.raises(HTTPException) as exc:
        choose_candidate(rid, 1, ChooseCandidateRequest(index=0), db_with_cache, test_settings)
    assert exc.value.status_code == 409
    assert exc.value.detail == "candidates_stale"


def test_choose_invalid_category_is_422(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-badcat-dddd"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    bad = [{"label": "Gone", "rationale": "", "confidence": 0.5, "category_id": "cat-deleted", "splits": []}]
    _make_candidate_set(db_with_cache, rid, bad)
    db_with_cache.commit()

    with pytest.raises(HTTPException) as exc:
        choose_candidate(rid, 1, ChooseCandidateRequest(index=0), db_with_cache, test_settings)
    assert exc.value.status_code == 422
    # No validation version written for the invalid candidate.
    receipt = db_with_cache.get(Receipt, rid)
    assert receipt.latest_validation_version == 1


def test_choose_invalid_index_is_422(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-idx-eeee"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES)
    db_with_cache.commit()

    with pytest.raises(HTTPException) as exc:
        choose_candidate(rid, 1, ChooseCandidateRequest(index=5), db_with_cache, test_settings)
    assert exc.value.status_code == 422


def test_choose_missing_set_is_404(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-missing-ffff"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    db_with_cache.commit()

    with pytest.raises(HTTPException) as exc:
        choose_candidate(rid, 99, ChooseCandidateRequest(index=0), db_with_cache, test_settings)
    assert exc.value.status_code == 404


def test_choose_while_syncing_is_409(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-syncing-gggg"
    receipt = _make_receipt(db_with_cache, rid, status=ReceiptStatus.SYNCING.value)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES)
    db_with_cache.commit()

    with pytest.raises(HTTPException) as exc:
        choose_candidate(rid, 1, ChooseCandidateRequest(index=0), db_with_cache, test_settings)
    assert exc.value.status_code == 409
    assert exc.value.detail == "sync_in_progress"


def test_model_topk_accept_awards_no_water(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-nowater-hhhh"
    receipt = _make_receipt(db_with_cache, rid)
    _add_cat2(db_with_cache, test_settings)
    # model baseline = cat-1; choosing cat-2 differs but source is a generated guess.
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD), source="model")
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES, source="model_topk")
    db_with_cache.commit()

    choose_candidate(rid, 1, ChooseCandidateRequest(index=1), db_with_cache, test_settings)

    state = db_with_cache.get(GameCorrectnessState, 1)
    assert state is None or state.water_units == 0


def test_type_to_organize_accept_awards_water(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-water-iiii"
    receipt = _make_receipt(db_with_cache, rid)
    _add_cat2(db_with_cache, test_settings)
    # model baseline = cat-1; a type_to_organize edit to cat-2 is real user input.
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD), source="model")
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES, source="type_to_organize")
    db_with_cache.commit()

    choose_candidate(rid, 1, ChooseCandidateRequest(index=1), db_with_cache, test_settings)

    state = db_with_cache.get(GameCorrectnessState, 1)
    assert state is not None and state.water_units == 1


def test_choose_clears_ai_guess_date_via_twin_lock(db_with_cache: Any, test_settings: Settings) -> None:
    # Parity with save_draft: choosing applies twin locks, clearing a stale
    # date_source="ai_guess" once the twin date is confirmed so the date gate passes.
    rid = "cand-datelock-kkkk"
    _add_cat2(db_with_cache, test_settings)
    receipt = _make_receipt(db_with_cache, rid)
    payload = dict(BASE_PAYLOAD)
    payload["date_source"] = "ai_guess"  # would block the date gate...
    _add_validation(db_with_cache, receipt, payload)
    _add_twin(db_with_cache, rid)  # ...but the twin's date is confirmed
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES)
    db_with_cache.commit()

    resp = choose_candidate(rid, 1, ChooseCandidateRequest(index=1), db_with_cache, test_settings)

    assert resp.validation.payload.get("date_source") in (None, "")
    assert resp.can_sync is True


def test_detail_and_list_expose_candidates(db_with_cache: Any, test_settings: Settings) -> None:
    rid = "cand-wiring-jjjj"
    receipt = _make_receipt(db_with_cache, rid)
    _add_validation(db_with_cache, receipt, dict(BASE_PAYLOAD))
    _make_candidate_set(db_with_cache, rid, SINGLE_CANDIDATES)
    db_with_cache.commit()

    detail = get_receipt_detail(rid, db_with_cache)
    assert detail.candidate_set is not None
    assert len(detail.candidate_set.candidates) == 2
    assert detail.candidate_set.chosen_index is None

    summaries = list_receipts(status=None, sort="newest", limit=200, db=db_with_cache, settings=test_settings)
    summary = next(s for s in summaries if s.id == rid)
    assert summary.has_candidates is True

    # After choosing, the set is resolved → has_candidates flips off.
    choose_candidate(rid, 1, ChooseCandidateRequest(index=0), db_with_cache, test_settings)
    summaries = list_receipts(status=None, sort="newest", limit=200, db=db_with_cache, settings=test_settings)
    summary = next(s for s in summaries if s.id == rid)
    assert summary.has_candidates is False

    detail = get_receipt_detail(rid, db_with_cache)
    assert detail.candidate_set.chosen_index == 0
