"""A receipt Gemini reads fine but with no readable merchant must be recoverable.

Regression for the no-payee ticket (2026-06-15 17.33.05.pdf): Gemini correctly
returned an empty payee_name (no merchant printed), but the required-payee
contract (`payee_name = Field(min_length=1)`) turned that into a fatal
error_extract with NO twin — the GUI showed a bare "Twin unavailable".

Payee now mirrors the date model: an empty payee is a valid, editable
needs_review draft (twin + every other field filled), and sync is hard-gated
separately until the user enters a payee.
"""

from __future__ import annotations

from datetime import date

from app.api.receipts import _candidate_block_hint
from app.services.validation import (
    build_initial_validation_payload,
    payee_sync_block_reason,
    validate_payload,
)

INGEST = date(2026, 6, 15)

# Mirrors what Gemini returns for a receipt with no merchant: clean data,
# empty payee.
PARSED = {
    "payee_name": "",
    "account_id": "acct-1",
    "transaction_date": "2026-06-06",
    "total_amount": 6.34,
    "category_id": "cat-1",
    "splits": [],
}


def _build(parsed=PARSED):
    payload = build_initial_validation_payload(parsed, default_account_id=None, ingest_date=INGEST)
    normalized, is_valid, errors = validate_payload(
        payload,
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
        allow_unknown_account=True,
    )
    return normalized, is_valid, errors


def test_payeeless_receipt_produces_a_valid_draft_not_an_error():
    normalized, is_valid, errors = _build()
    assert is_valid, errors  # editable needs_review draft, NOT error_extract
    assert normalized["payee_name"] == ""
    # Everything else is filled, ready for the user to just add a payee.
    assert normalized["total_amount"] == 6.34
    assert normalized["account_id"] == "acct-1"


def test_empty_payee_blocks_sync_until_filled():
    normalized, _, _ = _build()
    assert payee_sync_block_reason(normalized) is not None
    # Entering a payee clears the block.
    filled = {**normalized, "payee_name": "The Home Depot"}
    assert payee_sync_block_reason(filled) is None


def test_payee_sync_block_reason_treats_whitespace_as_empty():
    assert payee_sync_block_reason({"payee_name": "   "}) is not None
    assert payee_sync_block_reason({}) is not None
    assert payee_sync_block_reason({"payee_name": "Costco"}) is None


def test_candidate_block_hint_flags_needs_payee():
    normalized, is_valid, _ = _build()
    hint = _candidate_block_hint(
        has_validation=True,
        is_valid=is_valid,
        payload=normalized,
        twin_confirmed={"date_time": True, "total": True},
    )
    assert hint == "needs_payee"
    # With a payee, that gate clears (date is concrete + confirmed here).
    filled = {**normalized, "payee_name": "The Home Depot"}
    hint_filled = _candidate_block_hint(
        has_validation=True,
        is_valid=True,
        payload=filled,
        twin_confirmed={"date_time": True, "total": True},
    )
    assert hint_filled is None
