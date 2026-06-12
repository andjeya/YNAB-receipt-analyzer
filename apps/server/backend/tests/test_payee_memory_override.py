"""Tests for payee→category memory override in _validate_ynab_payload and _finalize_unified_success."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.enums import YNABCacheEntityType
from app.jobs.tasks import _validate_ynab_payload
from app.models import PayeeCategoryMemory, YNABCache
from app.services.payee_memory import apply_single_category_memory, apply_split_memory_to_workspace, lookup_payee_memory


BUDGET_ID = "test-budget"


def _add_category(db: Session, cat_id: str, name: str = "Groceries") -> None:
    db.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id=cat_id,
            name=name,
            group_name="Everyday",
            raw_json={"id": cat_id, "name": name},
        )
    )
    db.commit()


def _add_memory(
    db: Session,
    payee_key: str,
    *,
    category_id: str | None = None,
    template_json: dict | None = None,
) -> PayeeCategoryMemory:
    row = PayeeCategoryMemory(
        budget_id=BUDGET_ID,
        payee_key=payee_key,
        category_id=category_id,
        template_json=template_json,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


BASE_EXTRACTION = {
    "payee_name": "Test Store",
    "account_id": "acct-ai",
    "transaction_date": "2026-01-15",
    "total_amount": 25.00,
    "category_id": "cat-model",
    "splits": [],
    "card_last_four": None,
}


class TestSingleCategoryMemoryOverride:
    def test_single_fills_empty_category(self, db_session: Session):
        _add_category(db_session, "cat-1")
        _add_memory(db_session, "test store", category_id="cat-1")

        extraction = {**BASE_EXTRACTION, "category_id": ""}
        payload, is_valid, errors = _validate_ynab_payload(
            extraction,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert payload["category_id"] == "cat-1"
        assert payload.get("category_source") == "payee_memory"

    def test_single_overrides_model_guess_when_in_allowed(self, db_session: Session):
        _add_category(db_session, "cat-1")
        _add_category(db_session, "cat-model", "Model Cat")
        _add_memory(db_session, "test store", category_id="cat-1")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1", "cat-model"},
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert payload["category_id"] == "cat-1"
        assert payload.get("category_source") == "payee_memory"

    def test_stale_category_ignored_model_stands(self, db_session: Session):
        # Memory points to stale category (not in cache).
        _add_memory(db_session, "test store", category_id="cat-stale")
        _add_category(db_session, "cat-model", "Model Cat")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-model"},
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert payload["category_id"] == "cat-model"
        assert payload.get("category_source") is None

    def test_category_not_in_allowed_no_override(self, db_session: Session):
        """Memory category not in allowed_category_ids → no override."""
        _add_category(db_session, "cat-1")
        _add_category(db_session, "cat-model", "Model Cat")
        _add_memory(db_session, "test store", category_id="cat-1")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-model"},  # cat-1 NOT allowed
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert payload["category_id"] == "cat-model"
        assert payload.get("category_source") is None

    def test_no_db_no_override(self):
        """Without a db session no lookup is attempted."""
        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-model"},
            allowed_account_ids={"acct-ai"},
            db=None,
            budget_id=BUDGET_ID,
        )

        assert payload["category_id"] == "cat-model"
        assert payload.get("category_source") is None

    def test_no_budget_no_override(self, db_session: Session):
        """Without budget_id no lookup attempted."""
        _add_category(db_session, "cat-1")
        _add_memory(db_session, "test store", category_id="cat-1")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-model"},
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=None,
        )

        assert payload.get("category_source") is None


class TestSplitMemoryWorkspace:
    def test_split_preassign_and_recompute_sum_invariant(self, db_session: Session):
        """Split memory: items are pre-assigned by template, amounts recomputed,
        total milliunit sum is preserved."""
        _add_category(db_session, "cat-food")
        _add_category(db_session, "cat-drink")

        template = {
            "version": 1,
            "lanes": [{"category_id": "cat-food"}, {"category_id": "cat-drink"}],
            "dominant_category_id": "cat-food",
            "item_categories": {"milk": "cat-drink", "bread": "cat-food"},
        }
        memory = PayeeCategoryMemory(
            budget_id=BUDGET_ID,
            payee_key="test store",
            category_id=None,
            template_json=template,
        )

        # Build a workspace with two items.
        from app.services.allocation_workspace import build_initial_allocation_workspace
        from receipt_shared.money import dollars_to_milliunits

        twin_payload = {
            "line_items": [
                {"index": 0, "raw_text": "Milk", "line_total": 3.0, "item_type": "product"},
                {"index": 1, "raw_text": "Bread", "line_total": 7.0, "item_type": "product"},
            ]
        }
        payload = {
            "category_id": None,
            "splits": [
                {"category_id": "cat-food", "amount": 5.0, "memo": ""},
                {"category_id": "cat-drink", "amount": 5.0, "memo": ""},
            ],
            "total_amount": 10.0,
            "payee_name": "Test Store",
            "account_id": "acct-1",
            "transaction_date": "2026-01-15",
            "transaction_kind": "purchase",
        }
        workspace = build_initial_allocation_workspace(payload, twin_payload=twin_payload, twin_version=1)

        new_payload, new_workspace, applied = apply_split_memory_to_workspace(
            payload,
            workspace,
            memory,
            allowed_category_ids={"cat-food", "cat-drink"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert applied is True
        assert new_payload.get("category_source") == "payee_memory"

        # Check milliunit sum invariant.
        total_mu = dollars_to_milliunits(10.0, outflow=False)
        split_mu = sum(
            dollars_to_milliunits(s["amount"], outflow=False)
            for s in new_payload.get("splits", [])
        )
        assert total_mu == split_mu, f"Milliunit sum mismatch: {total_mu} != {split_mu}"

    def test_unmatched_items_go_to_dominant_lane(self, db_session: Session):
        _add_category(db_session, "cat-food")
        _add_category(db_session, "cat-drink")

        template = {
            "version": 1,
            "lanes": [{"category_id": "cat-food"}, {"category_id": "cat-drink"}],
            "dominant_category_id": "cat-food",
            "item_categories": {},  # No item mappings → all go to dominant.
        }
        memory = PayeeCategoryMemory(
            budget_id=BUDGET_ID,
            payee_key="test store",
            category_id=None,
            template_json=template,
        )

        from app.services.allocation_workspace import build_initial_allocation_workspace

        twin_payload = {
            "line_items": [
                {"index": 0, "raw_text": "Item A", "line_total": 4.0, "item_type": "product"},
                {"index": 1, "raw_text": "Item B", "line_total": 6.0, "item_type": "product"},
            ]
        }
        payload = {
            "category_id": None,
            "splits": [
                {"category_id": "cat-food", "amount": 5.0, "memo": ""},
                {"category_id": "cat-drink", "amount": 5.0, "memo": ""},
            ],
            "total_amount": 10.0,
            "payee_name": "Test Store",
            "account_id": "acct-1",
            "transaction_date": "2026-01-15",
            "transaction_kind": "purchase",
        }
        workspace = build_initial_allocation_workspace(payload, twin_payload=twin_payload, twin_version=1)

        new_payload, new_workspace, applied = apply_split_memory_to_workspace(
            payload,
            workspace,
            memory,
            allowed_category_ids={"cat-food", "cat-drink"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert applied is True
        # All unmatched items go to dominant (cat-food = split-0), so cat-food lane
        # should have more weight than cat-drink.
        splits = new_payload.get("splits", [])
        assert len(splits) == 2

    def test_stale_template_category_noop(self, db_session: Session):
        """Template with stale category → no-op, original payload unchanged."""
        # cat-food NOT in cache.
        template = {
            "version": 1,
            "lanes": [{"category_id": "cat-food"}, {"category_id": "cat-drink"}],
            "dominant_category_id": "cat-food",
            "item_categories": {},
        }
        memory = PayeeCategoryMemory(
            budget_id=BUDGET_ID,
            payee_key="test store",
            category_id=None,
            template_json=template,
        )

        from app.services.allocation_workspace import build_initial_allocation_workspace

        payload = {
            "category_id": "cat-original",
            "splits": [],
            "total_amount": 10.0,
            "payee_name": "Test Store",
            "account_id": "acct-1",
            "transaction_date": "2026-01-15",
            "transaction_kind": "purchase",
        }
        workspace = build_initial_allocation_workspace(payload, twin_payload=None, twin_version=0)

        new_payload, new_workspace, applied = apply_split_memory_to_workspace(
            payload,
            workspace,
            memory,
            allowed_category_ids={"cat-food", "cat-drink"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert applied is False
        assert new_payload is payload  # Unchanged reference.


def test_template_with_blank_category_lane_is_rejected(db_with_cache, test_settings):
    """A template lane lacking category_id must no-op the whole application."""
    from app.models import PayeeCategoryMemory
    from app.services.payee_memory import apply_split_memory_to_workspace

    db = db_with_cache
    memory = PayeeCategoryMemory(
        budget_id=test_settings.ynab_budget_id,
        payee_key="trader joes",
        category_id=None,
        template_json={
            "version": 1,
            "lanes": [{"category_id": "cat-groceries"}, {"category_id": None}],
            "dominant_category_id": "cat-groceries",
            "item_categories": {},
        },
    )
    payload = {"payee_name": "Trader Joe's", "total_amount": 10.0, "splits": []}
    workspace = {"version": 1, "twin_version": 1, "generated_at": "2026-06-12T00:00:00Z", "items": [], "lanes": [], "assignments": [], "warnings": []}
    new_payload, new_workspace, applied = apply_split_memory_to_workspace(
        payload, workspace, memory,
        allowed_category_ids={"cat-groceries"},
        db=db,
        budget_id=test_settings.ynab_budget_id,
    )
    assert applied is False
    assert new_payload == payload
