from __future__ import annotations

from app.services.validation import validate_payload


def _payload(account_id: str) -> dict[str, object]:
    return {
        "payee_name": "Costco",
        "account_id": account_id,
        "transaction_date": "2026-02-21",
        "transaction_time": "16:49",
        "memo": "Imported",
        "total_amount": 119.19,
        "category_id": "cat-1",
        "splits": [],
    }


def test_validate_payload_rejects_unknown_account_by_default():
    _normalized, is_valid, errors = validate_payload(
        _payload("__unknown__"),
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
    )

    assert is_valid is False
    assert "Account is unknown. Select a valid YNAB account before syncing" in errors


def test_validate_payload_allows_unknown_account_when_flag_enabled():
    _normalized, is_valid, errors = validate_payload(
        _payload("__unknown__"),
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
        allow_unknown_account=True,
    )

    assert is_valid is True
    assert errors == []
