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


def _split_payload(total: float, splits: list[dict]) -> dict[str, object]:
    return {
        "payee_name": "Costco",
        "account_id": "acct-1",
        "transaction_date": "2026-02-21",
        "transaction_time": "16:49",
        "memo": "Imported",
        "total_amount": total,
        "splits": splits,
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


# ---------------------------------------------------------------------------
# T2-05: stale/unknown category must not produce a syncable validation
# ---------------------------------------------------------------------------


def test_validate_payload_rejects_unknown_single_category():
    """A category_id not in the cached set (e.g. deleted in YNAB) is rejected,
    so a stale cache reference can never reach a YNAB write."""
    payload = _payload("acct-1")
    payload["category_id"] = "cat-deleted"

    _normalized, is_valid, errors = validate_payload(
        payload,
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
    )

    assert is_valid is False
    assert "Invalid category_id: cat-deleted" in errors


def test_validate_payload_rejects_unknown_split_category():
    """A split referencing a stale category is rejected."""
    payload = _split_payload(
        30.0,
        [
            {"category_id": "cat-1", "amount": 20.0, "memo": ""},
            {"category_id": "cat-stale", "amount": 10.0, "memo": ""},
        ],
    )

    _normalized, is_valid, errors = validate_payload(
        payload,
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
    )

    assert is_valid is False
    assert "Invalid category_id in split: cat-stale" in errors


# ---------------------------------------------------------------------------
# Finding 1: ValidationSplit.amount ge=0 produces friendly error message
# ---------------------------------------------------------------------------


def test_negative_split_amount_is_invalid_with_friendly_message():
    """Splits [12.00, -2.00] vs total 10.00 → is_valid False with clear message."""
    payload = _split_payload(
        total=10.00,
        splits=[
            {"category_id": "cat-1", "amount": 12.00, "memo": ""},
            {"category_id": "cat-2", "amount": -2.00, "memo": ""},
        ],
    )
    _normalized, is_valid, errors = validate_payload(payload)

    assert is_valid is False
    assert any(
        "Split amounts must be zero or greater" in msg for msg in errors
    ), f"Expected friendly message not found in errors: {errors}"
    # Must NOT expose raw pydantic dump text like "Input should be greater than or equal"
    assert not any("Input should be greater than or equal" in msg for msg in errors), (
        f"Raw pydantic error text leaked into errors: {errors}"
    )


def test_zero_split_amount_is_valid():
    """A split amount of exactly 0.00 must be accepted (ge=0)."""
    payload = _split_payload(
        total=10.00,
        splits=[
            {"category_id": "cat-1", "amount": 10.00, "memo": ""},
            {"category_id": "cat-2", "amount": 0.00, "memo": ""},
        ],
    )
    _normalized, is_valid, errors = validate_payload(payload)

    # split sum (10.00 + 0.00 = 10.00) matches total — only possible error would be
    # unrelated category/account; with no allowed_* sets the check is skipped.
    assert is_valid is True, f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Finding 2: exact milliunit check
# ---------------------------------------------------------------------------


def test_sub_cent_drift_fails_milliunit_check():
    """total=10.00, splits 5.004+4.999=10.003: dollar check passes (|0.003|<0.01)
    but milliunit check fails: 5004+4999=10003 != 10000."""
    payload = _split_payload(
        total=10.00,
        splits=[
            {"category_id": "cat-1", "amount": 5.004, "memo": ""},
            {"category_id": "cat-2", "amount": 4.999, "memo": ""},
        ],
    )
    _normalized, is_valid, errors = validate_payload(payload)

    assert is_valid is False, f"Expected invalid but got errors={errors}"
    milliunit_errors = [msg for msg in errors if "milliunits" in msg]
    assert milliunit_errors, f"Expected milliunit error message, got: {errors}"
    # Message must include both the split sum and the expected total in milliunits.
    assert any("10003" in msg and "10000" in msg for msg in milliunit_errors), (
        f"Milliunit error missing milliunit values: {milliunit_errors}"
    )


def test_exact_milliunit_match_passes():
    """Splits that match the total exactly in milliunits must pass."""
    # 45.74 + 73.45 = 119.19 exactly: 45740 + 73450 = 119190
    payload = _split_payload(
        total=119.19,
        splits=[
            {"category_id": "cat-1", "amount": 45.74, "memo": ""},
            {"category_id": "cat-2", "amount": 73.45, "memo": ""},
        ],
    )
    _normalized, is_valid, errors = validate_payload(payload)

    assert is_valid is True, f"Unexpected errors: {errors}"


def test_gross_mismatch_fails_dollar_check():
    """Splits that differ by more than $0.01 still fail the gross dollar check."""
    payload = _split_payload(
        total=10.00,
        splits=[
            {"category_id": "cat-1", "amount": 5.00, "memo": ""},
            {"category_id": "cat-2", "amount": 4.98, "memo": ""},
        ],
    )
    _normalized, is_valid, errors = validate_payload(payload)

    assert is_valid is False
    assert any("Split amounts must sum to total amount" in msg for msg in errors), (
        f"Expected gross mismatch error, got: {errors}"
    )
