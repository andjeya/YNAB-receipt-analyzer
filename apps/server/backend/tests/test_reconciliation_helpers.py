from __future__ import annotations

from app.services.reconciliation import _build_corrected_payload, _sync_payload_signature, _ynab_transaction_signature


def test_sync_and_ynab_signatures_match_for_equivalent_single_category():
    sync_payload = {
        "category_id": "cat-1",
        "subtransactions": [],
    }
    ynab_transaction = {
        "category_id": "cat-1",
        "subtransactions": [],
    }

    assert _sync_payload_signature(sync_payload) == _ynab_transaction_signature(ynab_transaction)


def test_sync_and_ynab_signatures_match_for_equivalent_split_even_with_parent_category_and_memo_diff():
    sync_payload = {
        "category_id": None,
        "subtransactions": [
            {"amount": -85210, "category_id": "cat-a", "memo": "model memo a"},
            {"amount": -12710, "category_id": "cat-b", "memo": "model memo b"},
        ],
    }
    ynab_transaction = {
        "category_id": "ynab-parent-category",
        "subtransactions": [
            {"amount": -12710, "category_id": "cat-b", "memo": "user changed memo b"},
            {"amount": -85210, "category_id": "cat-a", "memo": "user changed memo a"},
        ],
    }

    assert _sync_payload_signature(sync_payload) == _ynab_transaction_signature(ynab_transaction)


def test_sync_and_ynab_signatures_differ_when_split_category_changes():
    sync_payload = {
        "category_id": None,
        "subtransactions": [
            {"amount": -85210, "category_id": "cat-a"},
            {"amount": -12710, "category_id": "cat-b"},
        ],
    }
    ynab_transaction = {
        "category_id": "ynab-parent-category",
        "subtransactions": [
            {"amount": -85210, "category_id": "cat-a"},
            {"amount": -12710, "category_id": "cat-c"},
        ],
    }

    assert _sync_payload_signature(sync_payload) != _ynab_transaction_signature(ynab_transaction)


def test_sync_and_ynab_signatures_match_for_single_category_vs_equivalent_one_split():
    sync_payload = {
        "amount": -23040,
        "category_id": "cat-a",
        "subtransactions": [],
    }
    ynab_transaction = {
        "amount": -23040,
        "category_id": "cat-a",
        "subtransactions": [
            {"amount": -23040, "category_id": "cat-a", "memo": ""},
        ],
    }

    assert _sync_payload_signature(sync_payload) == _ynab_transaction_signature(ynab_transaction)


def test_build_corrected_payload_switches_to_split_mode():
    prior_payload = {
        "payee_name": "Store",
        "account_id": "acct-1",
        "transaction_date": "2026-02-01",
        "memo": "memo",
        "total_amount": 30,
        "category_id": "cat-old",
        "splits": [],
    }
    ynab_transaction = {
        "payee_name": "Store",
        "date": "2026-02-01",
        "memo": "memo",
        "amount": -30000,
        "subtransactions": [
            {"amount": -10000, "category_id": "cat-a", "memo": "a"},
            {"amount": -20000, "category_id": "cat-b", "memo": "b"},
        ],
    }

    payload = _build_corrected_payload(prior_payload, ynab_transaction)

    assert payload["category_id"] is None
    assert len(payload["splits"]) == 2
    assert payload["splits"][0]["category_id"] == "cat-a"
    assert payload["total_amount"] == 30
