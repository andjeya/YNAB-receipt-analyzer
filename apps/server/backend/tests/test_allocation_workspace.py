from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.receipts import save_draft
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import Base, Receipt, Validation, YNABCache
from app.schemas import SaveDraftRequest
from app.services.allocation_workspace import (
    build_initial_allocation_workspace,
    reconcile_allocation_workspace,
    recompute_payload_from_workspace,
)


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


def _split_payload() -> dict[str, object]:
    return {
        "payee_name": "Test Merchant",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "Imported",
        "total_amount": 30.0,
        "category_id": None,
        "splits": [
            {"category_id": "cat-1", "amount": 20.0, "memo": "a"},
            {"category_id": "cat-2", "amount": 10.0, "memo": "b"},
        ],
    }


def _twin_payload() -> dict[str, object]:
    return {
        "line_items": [
            {"index": 0, "raw_text": "A", "translated_text": "", "line_total": 20.0, "tax_code": None, "item_type": "product"},
            {"index": 1, "raw_text": "B", "translated_text": "", "line_total": 10.0, "tax_code": None, "item_type": "product"},
            {"index": 2, "raw_text": "TOTAL", "translated_text": "", "line_total": 30.0, "tax_code": None, "item_type": "total"},
        ]
    }


def test_workspace_recompute_keep_and_discard_modes():
    payload = _split_payload()
    workspace = build_initial_allocation_workspace(
        payload,
        twin_payload=_twin_payload(),
        twin_version=3,
    )

    # Pin only split-0 to force "keep manual" behavior.
    for lane in workspace["lanes"]:
        if lane["lane_id"] == "split-0":
            lane["pinned_amount"] = 25.0
        elif lane["lane_id"] == "split-1":
            lane["pinned_amount"] = None

    keep_payload, _, _ = recompute_payload_from_workspace(
        payload,
        workspace,
        mode="keep_manual_amounts",
    )
    assert keep_payload["splits"][0]["amount"] == 25.0
    assert keep_payload["splits"][1]["amount"] == 5.0

    discard_payload, _, _ = recompute_payload_from_workspace(
        payload,
        workspace,
        mode="discard_manual_amounts",
    )
    assert discard_payload["splits"][0]["amount"] == 20.0
    assert discard_payload["splits"][1]["amount"] == 10.0


# ---------------------------------------------------------------------------
# FIX A — stale pinned total must never resurrect an old total
# ---------------------------------------------------------------------------

def _single_category_payload(total: float = 30.0) -> dict:
    """Minimal single-category (no splits) receipt payload."""
    return {
        "payee_name": "Test Merchant",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "Imported",
        "total_amount": total,
        "category_id": "cat-1",
        "splits": [],
    }


def _single_item_twin(total: float = 30.0) -> dict:
    return {
        "line_items": [
            {"index": 0, "raw_text": "Widget", "translated_text": "", "line_total": total, "tax_code": None, "item_type": "product"},
        ]
    }


def test_stale_pin_does_not_resurrect_old_total():
    """
    Scenario: receipt built with total=30, later corrected to total=25.

    1. Build initial workspace with total=30 → main lane gets pinned_amount=30
    2. Reconcile against new payload with total=25 → stale pin must be cleared
    3. recompute must NOT write 30.0 back to total_amount; total stays 25.
    """
    payload_30 = _single_category_payload(total=30.0)
    workspace = build_initial_allocation_workspace(
        payload_30, twin_payload=_single_item_twin(30.0), twin_version=1
    )

    # Confirm initial workspace carries the defaulted pin = 30
    main_lane = next(l for l in workspace["lanes"] if l["lane_id"] == "main")
    assert main_lane["pinned_amount"] == 30.0

    # Correct the total to 25
    payload_25 = _single_category_payload(total=25.0)
    reconciled = reconcile_allocation_workspace(
        payload_25, workspace, twin_payload=_single_item_twin(25.0), twin_version=1
    )

    # After reconcile the stale pin (30) must have been cleared
    rec_main_lane = next(l for l in reconciled["lanes"] if l["lane_id"] == "main")
    assert rec_main_lane["pinned_amount"] is None, (
        f"Stale pin should have been cleared; got {rec_main_lane['pinned_amount']}"
    )

    # recompute must NOT write 30 back to total_amount
    result_payload, _, _ = recompute_payload_from_workspace(
        payload_25, reconciled, mode="keep_manual_amounts"
    )
    assert result_payload["total_amount"] == 25.0, (
        f"total_amount must stay 25.0, not be overwritten; got {result_payload['total_amount']}"
    )


def test_recompute_never_mutates_total_amount_single_category():
    """
    For single-category receipts, recompute must not alter total_amount in
    either mode.  This is a direct FIX A regression guard.
    """
    payload = _single_category_payload(total=42.99)
    workspace = build_initial_allocation_workspace(
        payload, twin_payload=_single_item_twin(42.99), twin_version=1
    )

    for mode in ("keep_manual_amounts", "discard_manual_amounts"):
        result_payload, _, _ = recompute_payload_from_workspace(
            payload, workspace, mode=mode
        )
        assert result_payload["total_amount"] == 42.99, (
            f"[{mode}] total_amount mutated; got {result_payload['total_amount']}"
        )


# ---------------------------------------------------------------------------
# FIX B — all-pinned shortfall must warn
# ---------------------------------------------------------------------------

def test_all_pinned_shortfall_emits_warning():
    """
    When every lane is pinned and the pin sum < total, a warning must be
    emitted with exact figures for the pinned sum, total, and difference.
    """
    payload = _split_payload()  # total=30, two splits
    workspace = build_initial_allocation_workspace(
        payload, twin_payload=_twin_payload(), twin_version=1
    )

    # Pin BOTH lanes below the total: 10 + 15 = 25 < 30  →  shortfall 5
    for lane in workspace["lanes"]:
        if lane["lane_id"] == "split-0":
            lane["pinned_amount"] = 10.0
        elif lane["lane_id"] == "split-1":
            lane["pinned_amount"] = 15.0

    _, _, warnings = recompute_payload_from_workspace(
        payload, workspace, mode="keep_manual_amounts"
    )

    shortfall_warnings = [w for w in warnings if "unallocated" in w.lower()]
    assert len(shortfall_warnings) == 1, (
        f"Expected exactly one unallocated shortfall warning; got {warnings}"
    )
    warn = shortfall_warnings[0]
    # The warning must mention the pinned sum, the total, and the difference.
    assert "25" in warn, f"Warning should contain pinned sum 25; got: {warn}"
    assert "30" in warn, f"Warning should contain total 30; got: {warn}"
    assert "5" in warn, f"Warning should contain difference 5; got: {warn}"


def test_no_shortfall_warning_when_pins_match_total():
    """No spurious warning when all pins exactly cover the total."""
    payload = _split_payload()  # total=30
    workspace = build_initial_allocation_workspace(
        payload, twin_payload=_twin_payload(), twin_version=1
    )

    # Pin both lanes to exactly 30: 20 + 10 = 30
    for lane in workspace["lanes"]:
        if lane["lane_id"] == "split-0":
            lane["pinned_amount"] = 20.0
        elif lane["lane_id"] == "split-1":
            lane["pinned_amount"] = 10.0

    _, _, warnings = recompute_payload_from_workspace(
        payload, workspace, mode="keep_manual_amounts"
    )

    shortfall_warnings = [w for w in warnings if "unallocated" in w.lower()]
    assert len(shortfall_warnings) == 0, (
        f"No shortfall warning expected when pins match total; got {warnings}"
    )


# ---------------------------------------------------------------------------
# FIX C — discounts must subtract from lane weights
# ---------------------------------------------------------------------------

def test_discount_item_reduces_lane_weight():
    """
    Two-lane scenario with a discount in lane 0:
      Lane 0 items: +10.00, -2.00 (discount)  → net weight 8.00
      Lane 1 items: +12.00                     → net weight 12.00
      Total: 18.00

    Expected split (largest-remainder on weights 8 and 12):
      Lane 0: 18 * 8/20 = 7.20  → split-0 = 7.20
      Lane 1: 18 * 12/20 = 10.80 → split-1 = 10.80
    """
    payload = {
        "payee_name": "Test Merchant",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "",
        "total_amount": 18.0,
        "category_id": None,
        "splits": [
            {"category_id": "cat-1", "amount": 8.0, "memo": "a"},
            {"category_id": "cat-2", "amount": 10.0, "memo": "b"},
        ],
    }
    # Build workspace manually to control item→lane assignment
    workspace = build_initial_allocation_workspace(payload, twin_payload=None, twin_version=1)

    # Inject items directly so we control which item goes to which lane
    item_a = {"item_id": str(uuid4()), "source_index": 0, "label": "Product A", "amount": 10.0, "tax_code": None, "item_type": "product"}
    item_d = {"item_id": str(uuid4()), "source_index": 1, "label": "Discount", "amount": -2.0, "tax_code": None, "item_type": "discount"}
    item_b = {"item_id": str(uuid4()), "source_index": 2, "label": "Product B", "amount": 12.0, "tax_code": None, "item_type": "product"}
    workspace["items"] = [item_a, item_d, item_b]
    workspace["assignments"] = [
        {"item_id": item_a["item_id"], "lane_id": "split-0"},
        {"item_id": item_d["item_id"], "lane_id": "split-0"},  # discount in lane 0
        {"item_id": item_b["item_id"], "lane_id": "split-1"},
    ]
    # Clear all pins so weights drive allocation
    for lane in workspace["lanes"]:
        lane["pinned_amount"] = None

    result_payload, _, _ = recompute_payload_from_workspace(
        payload, workspace, mode="discard_manual_amounts"
    )

    splits = result_payload["splits"]
    assert len(splits) == 2
    # weights: 8 and 12; total 18
    # 18 * 8/20 = 7.20;  18 * 12/20 = 10.80
    assert splits[0]["amount"] == pytest.approx(7.20, abs=0.005), f"Lane 0 expected 7.20, got {splits[0]['amount']}"
    assert splits[1]["amount"] == pytest.approx(10.80, abs=0.005), f"Lane 1 expected 10.80, got {splits[1]['amount']}"
    # Exact cent sum must equal total
    total_cents = round(sum(s["amount"] for s in splits) * 100)
    assert total_cents == round(18.0 * 100), f"Split sum mismatch: {splits}"


def test_discount_only_lane_collapses_to_equal_fallback():
    """
    If all items in a lane are discounts its net weight is 0 (floored).
    With a second lane having positive weight the all-zero fallback must
    not be triggered; the discount-only lane gets 0 and the other lane
    gets the full total.
    """
    payload = {
        "payee_name": "Test Merchant",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "",
        "total_amount": 20.0,
        "category_id": None,
        "splits": [
            {"category_id": "cat-1", "amount": 5.0, "memo": "a"},
            {"category_id": "cat-2", "amount": 15.0, "memo": "b"},
        ],
    }
    workspace = build_initial_allocation_workspace(payload, twin_payload=None, twin_version=1)

    item_d = {"item_id": str(uuid4()), "source_index": 0, "label": "Coupon", "amount": -5.0, "tax_code": None, "item_type": "discount"}
    item_b = {"item_id": str(uuid4()), "source_index": 1, "label": "Product B", "amount": 20.0, "tax_code": None, "item_type": "product"}
    workspace["items"] = [item_d, item_b]
    workspace["assignments"] = [
        {"item_id": item_d["item_id"], "lane_id": "split-0"},  # net weight 0 after floor
        {"item_id": item_b["item_id"], "lane_id": "split-1"},  # net weight 20
    ]
    for lane in workspace["lanes"]:
        lane["pinned_amount"] = None

    result_payload, _, _ = recompute_payload_from_workspace(
        payload, workspace, mode="discard_manual_amounts"
    )
    splits = result_payload["splits"]
    assert len(splits) == 2
    # weights: [0, 20] → total 20 * 0/20 = 0 for lane0, 20 for lane1
    # but weight_sum=20>0 so the positive-weight path is taken, not equal split
    assert splits[0]["amount"] == pytest.approx(0.0, abs=0.005)
    assert splits[1]["amount"] == pytest.approx(20.0, abs=0.005)


# ---------------------------------------------------------------------------
# Property-style tests: splits always sum to total, total_amount never mutated
# ---------------------------------------------------------------------------

def test_splits_always_sum_to_total_no_pins():
    """
    For several fixed weight/total combinations, verify that:
    - splits sum exactly to total_amount (in cents, integer equality)
    - total_amount in the returned payload is never mutated
    """
    # Each case: (total, lane_amounts_list)
    cases = [
        (10.00, [3.0, 7.0]),
        (100.00, [33.33, 33.33, 33.34]),
        (9.99, [5.0, 4.99]),
        (0.01, [0.005, 0.005]),
        (50.00, [10.0, 15.0, 25.0]),
        (123.45, [40.0, 40.0, 43.45]),
    ]

    for total, weights in cases:
        splits_def = [
            {"category_id": f"cat-{i}", "amount": w, "memo": ""} for i, w in enumerate(weights)
        ]
        payload = {
            "payee_name": "Prop Merchant",
            "account_id": "acct-1",
            "transaction_date": "2026-01-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": total,
            "category_id": None,
            "splits": splits_def,
        }
        workspace = build_initial_allocation_workspace(payload, twin_payload=None, twin_version=1)
        for lane in workspace["lanes"]:
            lane["pinned_amount"] = None

        result_payload, _, _ = recompute_payload_from_workspace(
            payload, workspace, mode="discard_manual_amounts"
        )

        # total_amount must not be mutated
        assert result_payload["total_amount"] == total, (
            f"total={total} weights={weights}: total_amount mutated to {result_payload['total_amount']}"
        )

        # cents-exact sum
        result_splits = result_payload.get("splits", [])
        split_cents = sum(round(s["amount"] * 100) for s in result_splits)
        total_cents = round(total * 100)
        assert split_cents == total_cents, (
            f"total={total} weights={weights}: split cent sum {split_cents} != {total_cents}"
        )


def test_recompute_never_mutates_total_amount_any_mode():
    """
    Guard: recompute must not mutate total_amount for split receipts either,
    in both keep_manual_amounts and discard_manual_amounts modes.
    """
    payload = _split_payload()  # total=30
    workspace = build_initial_allocation_workspace(
        payload, twin_payload=_twin_payload(), twin_version=1
    )
    for lane in workspace["lanes"]:
        lane["pinned_amount"] = None

    for mode in ("keep_manual_amounts", "discard_manual_amounts"):
        result_payload, _, _ = recompute_payload_from_workspace(payload, workspace, mode=mode)
        assert result_payload["total_amount"] == 30.0, (
            f"[{mode}] total_amount mutated; got {result_payload['total_amount']}"
        )


def test_save_draft_persists_allocation_workspace():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = Receipt(
            id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            storage_key="receipts/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-workspace",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=1234,
            status=ReceiptStatus.NEEDS_REVIEW.value,
            latest_validation_version=0,
            extraction_completed_at=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
        )
        db.add(receipt)
        db.commit()

        payload = _split_payload()
        workspace = build_initial_allocation_workspace(
            payload,
            twin_payload=_twin_payload(),
            twin_version=2,
        )
        response = save_draft(
            receipt_id=receipt.id,
            request=SaveDraftRequest(payload=payload, source="user", allocation_workspace=workspace),
            db=db,
            settings=settings,
        )

        latest = db.scalar(
            select(Validation)
            .where(Validation.receipt_id == receipt.id)
            .order_by(Validation.version.desc())
            .limit(1)
        )
        assert response.validation.allocation_workspace is not None
        assert latest is not None
        assert latest.allocation_workspace is not None
        assert latest.allocation_workspace.get("version") == 1
        assert len(latest.allocation_workspace.get("lanes", [])) >= 2
        assert len(latest.allocation_workspace.get("assignments", [])) >= 1
