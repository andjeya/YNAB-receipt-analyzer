"""A receipt Gemini reads fine but with no confident date must be recoverable.

Regression for the handwritten Miner's Den ticket: Gemini correctly returned a
null transaction_date (no year printed), but the required-date contract turned
that into a fatal error_extract with no editable draft.  The date is now
resolved deterministically (year completed) and flagged for confirm, so the
receipt lands in a valid, editable draft instead of erroring — while sync stays
blocked until the user confirms the date.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.api.receipts import _apply_twin_locks_to_payload
from app.jobs.tasks import _normalize_twin_payload
from app.services.date_resolution import AI_GUESS_SOURCE, date_sync_block_reason
from app.services.validation import build_initial_validation_payload, validate_payload

INGEST = date(2026, 6, 12)

# Mirrors what Gemini returns for the handwritten ticket: clean data, null date,
# the literal "5/12" text, low confidence.
PARSED = {
    "payee_name": "Miner's Den",
    "account_id": "acct-1",
    "transaction_date": None,
    "transaction_date_raw": "5/12",
    "date_confidence": "low",
    "date_note": "Two dates detected; 'Date In' 5/12 best matches; year not printed",
    "total_amount": 67.0,
    "category_id": "cat-1",
    "splits": [],
}


def _build():
    payload = build_initial_validation_payload(PARSED, default_account_id=None, ingest_date=INGEST)
    normalized, is_valid, errors = validate_payload(
        payload,
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
        allow_unknown_account=True,
    )
    return normalized, is_valid, errors


def test_dateless_receipt_produces_a_valid_draft_not_an_error():
    normalized, is_valid, errors = _build()
    assert is_valid, errors
    # Year completed deterministically to the ingest year.
    assert normalized["transaction_date"] == "2026-05-12"
    assert normalized["date_source"] == AI_GUESS_SOURCE
    assert normalized["date_confidence"] == "low"
    assert "2026" in normalized["date_note"]


def test_guessed_date_blocks_sync_until_confirmed():
    normalized, _, _ = _build()
    # Unconfirmed guess: sync is blocked.
    assert date_sync_block_reason(normalized) is not None
    # Confirming (or editing) the date clears the guess marker → sync unblocks.
    confirmed = {**normalized, "date_source": None}
    assert date_sync_block_reason(confirmed) is None


def test_twin_payload_resolves_date_so_user_can_confirm_it():
    twin = _normalize_twin_payload(
        {**PARSED, "line_items": [], "currency": "USD"},
        ingest_date=INGEST,
    )
    # The twin gets a concrete date to confirm (year completed) + the guess note.
    assert twin["transaction_date"] == "2026-05-12"
    assert twin["date_source"] == AI_GUESS_SOURCE
    assert "2026" in twin["date_note"]


def test_confirming_twin_date_time_clears_the_guess_and_unblocks_sync():
    # A guessed YNAB draft whose date is locked from a confirmed twin section.
    draft = {"transaction_date": "2026-05-12", "date_source": AI_GUESS_SOURCE}
    twin = SimpleNamespace(
        confirmed_sections={"date_time": True, "total": True},
        payload={"transaction_date": "2026-05-12", "transaction_time": None, "total_amount": 67.0},
    )
    locked, _warnings = _apply_twin_locks_to_payload(draft, twin)
    assert locked["date_source"] is None
    assert date_sync_block_reason(locked) is None


def test_no_date_at_all_still_yields_editable_draft_with_blocked_sync():
    parsed = {**PARSED, "transaction_date_raw": "", "date_note": ""}
    payload = build_initial_validation_payload(parsed, default_account_id=None, ingest_date=INGEST)
    normalized, is_valid, errors = validate_payload(
        payload,
        allowed_category_ids={"cat-1"},
        allowed_account_ids={"acct-1"},
        allow_unknown_account=True,
    )
    assert is_valid, errors  # editable draft, not error_extract
    assert normalized["transaction_date"] is None
    assert date_sync_block_reason(normalized) is not None  # must enter a date first
