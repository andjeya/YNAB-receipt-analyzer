"""Tests for deterministic receipt-date resolution.

The year-completion rule (most-recent-past relative to ingest) must be fully
deterministic so a guessed date is explainable and a real date never reaches
sync unconfirmed.
"""

from __future__ import annotations

from datetime import date

from app.services.date_resolution import (
    AI_GUESS_SOURCE,
    date_sync_block_reason,
    resolve_receipt_date,
)

INGEST = date(2026, 6, 12)


def _resolve(structured=None, raw=None, confidence="high", note=""):
    return resolve_receipt_date(
        structured_date=structured,
        raw_text=raw,
        model_confidence=confidence,
        model_note=note,
        ingest_date=INGEST,
    )


def test_full_high_confidence_date_passes_through_trusted():
    result = _resolve(structured="2026-05-12", confidence="high")
    assert result.iso_date == "2026-05-12"
    assert result.confidence == "high"
    assert result.source is None


def test_full_date_but_low_confidence_is_flagged_for_confirm():
    # e.g. two dates detected; the model picked one but is unsure.
    result = _resolve(structured="2026-05-12", confidence="low", note="Two dates detected")
    assert result.iso_date == "2026-05-12"
    assert result.confidence == "low"
    assert result.source == AI_GUESS_SOURCE


def test_missing_year_completes_to_most_recent_past_this_year():
    # 5/12 is before ingest (6/12), so the year is the ingest year.
    result = _resolve(structured=None, raw="5/12", confidence="low")
    assert result.iso_date == "2026-05-12"
    assert result.source == AI_GUESS_SOURCE
    assert "2026" in result.note


def test_missing_year_in_future_rolls_to_last_year():
    # 12/30 is after ingest (6/12), so the most-recent-past is last year.
    result = _resolve(structured=None, raw="12/30", confidence="low")
    assert result.iso_date == "2025-12-30"
    assert result.source == AI_GUESS_SOURCE


def test_missing_year_feb_29_resolves_to_recent_leap_year():
    # 2024 is the most recent past leap year relative to 2026-06-12.
    result = _resolve(structured=None, raw="2/29", confidence="low")
    assert result.iso_date == "2024-02-29"
    assert result.source == AI_GUESS_SOURCE


def test_raw_with_two_digit_year_interpreted_as_2000s():
    result = _resolve(structured=None, raw="5/12/25", confidence="low")
    assert result.iso_date == "2025-05-12"
    assert result.source == AI_GUESS_SOURCE


def test_raw_full_date_recovered_but_flagged_low():
    result = _resolve(structured=None, raw="05/12/2026", confidence="low")
    assert result.iso_date == "2026-05-12"
    assert result.source == AI_GUESS_SOURCE


def test_no_date_anywhere_yields_none_for_manual_entry():
    result = _resolve(structured=None, raw="", confidence="low")
    assert result.iso_date is None
    assert result.source == AI_GUESS_SOURCE


def test_unparseable_raw_yields_none():
    result = _resolve(structured=None, raw="see back of envelope", confidence="low")
    assert result.iso_date is None
    assert result.source == AI_GUESS_SOURCE


def test_impossible_month_day_yields_none():
    result = _resolve(structured=None, raw="13/40", confidence="low")
    assert result.iso_date is None


def test_first_date_token_wins_when_multiple_present():
    # "Date In 5/12, Finish 5/28" — the model is told to put the best match first.
    result = _resolve(structured=None, raw="5/12, 5/28", confidence="low")
    assert result.iso_date == "2026-05-12"


# --- sync gate -------------------------------------------------------------


def test_sync_blocked_when_date_missing():
    assert date_sync_block_reason({"transaction_date": None}) is not None


def test_sync_blocked_when_date_is_unconfirmed_guess():
    payload = {"transaction_date": "2026-05-12", "date_source": AI_GUESS_SOURCE}
    assert date_sync_block_reason(payload) is not None


def test_sync_allowed_when_date_present_and_confirmed():
    payload = {"transaction_date": "2026-05-12", "date_source": None}
    assert date_sync_block_reason(payload) is None
