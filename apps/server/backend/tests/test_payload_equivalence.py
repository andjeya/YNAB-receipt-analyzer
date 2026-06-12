"""payloads_equivalent: provenance-only differences must not count as changes."""
from __future__ import annotations

from app.services.validation import normalize_payload_for_comparison, payloads_equivalent

BASE = {
    "payee_name": "Trader Joe's",
    "account_id": "acct-1",
    "transaction_date": "2026-06-04",
    "transaction_time": "20:33:00",
    "memo": "Groceries",
    "total_amount": 92.81,
    "transaction_kind": "purchase",
    "category_id": "cat-1",
    "splits": [],
}


def test_missing_vs_none_account_source_is_equivalent() -> None:
    old = dict(BASE)  # stored before the field existed
    new = {**BASE, "account_source": None}
    assert payloads_equivalent(old, new)


def test_card_mapping_vs_absent_account_source_is_equivalent() -> None:
    old = {**BASE, "account_source": "card_mapping"}
    new = dict(BASE)
    assert payloads_equivalent(old, new)


def test_amount_change_is_not_equivalent() -> None:
    old = {**BASE, "account_source": "card_mapping"}
    new = {**BASE, "total_amount": 93.81}
    assert not payloads_equivalent(old, new)


def test_account_change_is_not_equivalent() -> None:
    old = {**BASE, "account_source": "card_mapping"}
    new = {**BASE, "account_id": "acct-2"}
    assert not payloads_equivalent(old, new)


def test_legacy_unparseable_payload_falls_back_to_raw() -> None:
    junk = {"not_a_field": True}
    assert normalize_payload_for_comparison(junk) == junk
    assert payloads_equivalent(junk, dict(junk))
