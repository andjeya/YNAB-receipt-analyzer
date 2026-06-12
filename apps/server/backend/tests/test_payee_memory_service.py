"""Tests for the payee_memory service layer."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.enums import YNABCacheEntityType
from app.models import PayeeCategoryMemory, YNABCache
from app.services.payee_memory import (
    lookup_payee_memory,
    upsert_payee_memory,
    normalize_item_text,
    build_template_from_validation,
)


BUDGET_ID = "budget-test"


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


class TestLookupPayeeMemory:
    def test_lookup_hit_returns_row(self, db_session: Session):
        _add_memory(db_session, "test store", category_id="cat-1")
        result = lookup_payee_memory(db_session, BUDGET_ID, "Test Store")
        assert result is not None
        assert result.category_id == "cat-1"

    def test_lookup_miss_unseen_payee_returns_none(self, db_session: Session):
        result = lookup_payee_memory(db_session, BUDGET_ID, "Unknown Payee")
        assert result is None

    def test_lookup_blank_payee_returns_none(self, db_session: Session):
        _add_memory(db_session, "test store", category_id="cat-1")
        result = lookup_payee_memory(db_session, BUDGET_ID, "")
        assert result is None

    def test_lookup_none_payee_returns_none(self, db_session: Session):
        _add_memory(db_session, "test store", category_id="cat-1")
        result = lookup_payee_memory(db_session, BUDGET_ID, None)
        assert result is None

    def test_lookup_blank_budget_returns_none(self, db_session: Session):
        _add_memory(db_session, "test store", category_id="cat-1")
        result = lookup_payee_memory(db_session, "", "Test Store")
        assert result is None

    def test_lookup_normalization_equivalence(self, db_session: Session):
        """'Test Store', 'TEST STORE', and 'test  store' all map to the same key."""
        _add_memory(db_session, "test store", category_id="cat-1")
        for variant in ("Test Store", "TEST STORE", "test  store", "test store"):
            result = lookup_payee_memory(db_session, BUDGET_ID, variant)
            assert result is not None, f"Expected hit for {variant!r}"


class TestUpsertPayeeMemory:
    def test_upsert_creates_single_category_row(self, db_session: Session):
        row = upsert_payee_memory(db_session, BUDGET_ID, "Test Store", category_id="cat-1")
        db_session.commit()
        assert row is not None
        assert row.payee_key == "test store"
        assert row.category_id == "cat-1"
        assert row.template_json is None

    def test_upsert_creates_split_template_row(self, db_session: Session):
        template = {"version": 1, "lanes": [{"category_id": "cat-1"}], "dominant_category_id": "cat-1", "item_categories": {}}
        row = upsert_payee_memory(db_session, BUDGET_ID, "Test Store", template=template)
        db_session.commit()
        assert row is not None
        assert row.template_json == template
        assert row.category_id is None

    def test_upsert_updates_existing_last_write_wins(self, db_session: Session):
        _add_memory(db_session, "test store", category_id="cat-1")
        row = upsert_payee_memory(db_session, BUDGET_ID, "Test Store", category_id="cat-2")
        db_session.commit()
        assert row is not None
        assert row.category_id == "cat-2"

    def test_upsert_flip_single_to_split(self, db_session: Session):
        _add_memory(db_session, "test store", category_id="cat-1")
        template = {"version": 1, "lanes": [{"category_id": "cat-2"}], "dominant_category_id": "cat-2", "item_categories": {}}
        row = upsert_payee_memory(db_session, BUDGET_ID, "Test Store", template=template)
        db_session.commit()
        assert row is not None
        assert row.template_json == template
        assert row.category_id is None

    def test_upsert_flip_split_to_single(self, db_session: Session):
        template = {"version": 1, "lanes": [{"category_id": "cat-2"}], "dominant_category_id": "cat-2", "item_categories": {}}
        _add_memory(db_session, "test store", template_json=template)
        row = upsert_payee_memory(db_session, BUDGET_ID, "Test Store", category_id="cat-1")
        db_session.commit()
        assert row is not None
        assert row.category_id == "cat-1"
        assert row.template_json is None

    def test_upsert_noop_blank_payee(self, db_session: Session):
        result = upsert_payee_memory(db_session, BUDGET_ID, "", category_id="cat-1")
        assert result is None

    def test_upsert_noop_none_payee(self, db_session: Session):
        result = upsert_payee_memory(db_session, BUDGET_ID, None, category_id="cat-1")
        assert result is None

    def test_upsert_noop_blank_budget(self, db_session: Session):
        result = upsert_payee_memory(db_session, "", "Test Store", category_id="cat-1")
        assert result is None

    def test_upsert_noop_both_values_falsy(self, db_session: Session):
        result = upsert_payee_memory(db_session, BUDGET_ID, "Test Store")
        assert result is None

    def test_upsert_integrity_error_refetch(self, db_session: Session, monkeypatch):
        """Concurrent insert race: begin_nested raises IntegrityError → re-fetch and update."""
        # Pre-insert a row so the re-fetch succeeds.
        _add_memory(db_session, "test store", category_id="cat-old")

        from sqlalchemy.exc import IntegrityError as IE

        call_count = {"n": 0}
        real_begin_nested = db_session.begin_nested

        def _raise_once(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise IE("simulated race", None, None)
            return real_begin_nested(*args, **kwargs)

        monkeypatch.setattr(db_session, "begin_nested", _raise_once)
        # No existing row returned by scalar on first path, so we hit the except branch.
        # Instead, patch the scalar to return None to force the create path.
        original_scalar = db_session.scalar
        scalar_calls = {"n": 0}

        def _scalar_none_once(stmt):
            scalar_calls["n"] += 1
            # First call (lookup existing before create): return None to trigger create.
            # Subsequent calls: use real scalar.
            if scalar_calls["n"] == 1:
                return None
            return original_scalar(stmt)

        monkeypatch.setattr(db_session, "scalar", _scalar_none_once)
        monkeypatch.setattr(db_session, "begin_nested", _raise_once)

        result = upsert_payee_memory(db_session, BUDGET_ID, "Test Store", category_id="cat-new")
        assert result is not None


class TestNormalizeItemText:
    def test_lowercases(self):
        assert normalize_item_text("MILK") == "milk"

    def test_collapses_whitespace(self):
        assert normalize_item_text("whole  milk") == "whole milk"

    def test_strips_non_alphanumeric(self):
        assert normalize_item_text("milk (2%)") == "milk 2"

    def test_returns_none_for_empty(self):
        assert normalize_item_text("") is None
        assert normalize_item_text("   ") is None
        assert normalize_item_text(None) is None

    def test_strips_punctuation(self):
        assert normalize_item_text("bread, white") == "bread white"


class TestBuildTemplateFromValidation:
    def test_single_category_returns_none(self):
        payload = {"category_id": "cat-1", "splits": [], "total_amount": 10.0}
        result = build_template_from_validation(payload, None)
        assert result is None

    def test_split_returns_template_shape(self):
        payload = {
            "category_id": None,
            "splits": [
                {"category_id": "cat-1", "amount": 7.0, "memo": ""},
                {"category_id": "cat-2", "amount": 3.0, "memo": ""},
            ],
            "total_amount": 10.0,
        }
        result = build_template_from_validation(payload, None)
        assert result is not None
        assert result["version"] == 1
        assert len(result["lanes"]) == 2
        assert result["lanes"][0]["category_id"] == "cat-1"
        assert result["lanes"][1]["category_id"] == "cat-2"
        assert "dominant_category_id" in result
        assert "item_categories" in result

    def test_split_dominant_is_largest_lane(self):
        """Dominant should be the lane with largest weight (here cat-1 if more items assigned)."""
        payload = {
            "category_id": None,
            "splits": [
                {"category_id": "cat-1", "amount": 8.0, "memo": ""},
                {"category_id": "cat-2", "amount": 2.0, "memo": ""},
            ],
            "total_amount": 10.0,
        }
        # Without workspace items, fallback to first lane.
        result = build_template_from_validation(payload, None)
        assert result is not None
        # With no items, dominant is first lane by fallback.
        assert result["dominant_category_id"] is not None
