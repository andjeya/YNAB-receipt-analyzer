"""Deterministic resolution of a receipt's transaction date.

Gemini is asked to return a full ISO ``transaction_date`` only when it is
confident AND the year is printed.  When the year is missing (common on
handwritten tickets) or the date is otherwise low-confidence, the *year is
completed here by a deterministic rule* — never by model arithmetic — so the
result is explainable and testable.

Rule for a missing year: the most-recent-past occurrence of the month/day
relative to the receipt's ingest date.  A guessed/low-confidence date is tagged
``date_source = "ai_guess"`` so the UI can require an explicit confirm and the
sync gate can block until the user confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Provenance marker for a date the system guessed (missing year, low confidence,
# or ambiguous).  Sync is blocked while this marker is present (see
# ``date_sync_block_reason``); confirming/editing the date clears it.
AI_GUESS_SOURCE = "ai_guess"

# First date-like token in a free-text string: ISO ``YYYY-MM-DD`` or a numeric
# ``M/D`` / ``M-D`` with an optional ``/YY`` or ``/YYYY`` year.
_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_MD_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?\b")


@dataclass(frozen=True)
class ResolvedDate:
    """Outcome of date resolution.

    iso_date: "YYYY-MM-DD" or None when no date could be recovered.
    confidence: "high" only for a model-supplied full date it was sure of.
    source: None when trusted; ``AI_GUESS_SOURCE`` when guessed/low-confidence.
    note: short human explanation surfaced in the date warning bubble.
    """

    iso_date: str | None
    confidence: str
    source: str | None
    note: str


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _complete_year(month: int, day: int, ingest: date) -> date | None:
    """Most-recent-past occurrence of month/day at or before ``ingest``.

    Scans back across a 9-year window, which also lets a yearless "2/29"
    resolve to the most recent past leap year.  Returns None for an impossible
    month/day (e.g. month 13), which the caller treats as "no date".
    """
    for year in range(ingest.year, ingest.year - 9, -1):
        candidate = _safe_date(year, month, day)
        if candidate is not None and candidate <= ingest:
            return candidate
    return None


def _parse_month_day(raw: str) -> tuple[int, int, int | None] | None:
    """Parse the first date-like token from free text → (month, day, year|None).

    US convention (month-first) is assumed for numeric forms.  A two-digit year
    is interpreted as 20YY.  Returns None when nothing date-like is found.
    """
    text = str(raw or "").strip()
    if not text:
        return None

    iso = _ISO_RE.search(text)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        return month, day, year

    md = _MD_RE.search(text)
    if md:
        month, day = int(md.group(1)), int(md.group(2))
        year_token = md.group(3)
        year = None
        if year_token is not None:
            year = int(year_token)
            if year < 100:
                year += 2000
        return month, day, year

    return None


def resolve_receipt_date(
    *,
    structured_date: str | None,
    raw_text: str | None,
    model_confidence: str | None,
    model_note: str | None,
    ingest_date: date,
) -> ResolvedDate:
    """Resolve the transaction date from the model's output deterministically.

    structured_date: the model's full ISO ``transaction_date`` (or None).
    raw_text: the literal date text the model saw (``transaction_date_raw``).
    model_confidence: "high" | "low" as reported by the model.
    model_note: the model's short explanation (``date_note``).
    ingest_date: receipt ingest date, used to complete a missing year.
    """
    note = str(model_note or "").strip()
    low = str(model_confidence or "high").strip().lower() == "low"

    # The model gave a full ISO date.
    if structured_date:
        parsed = _parse_month_day(structured_date)
        if parsed is not None and parsed[2] is not None:
            resolved = _safe_date(parsed[2], parsed[0], parsed[1])
            if resolved is not None:
                if low:
                    return ResolvedDate(resolved.isoformat(), "low", AI_GUESS_SOURCE, note)
                return ResolvedDate(resolved.isoformat(), "high", None, note)

    # No usable structured date — try to recover month/day from the raw text.
    parsed = _parse_month_day(raw_text or "")
    if parsed is None:
        return ResolvedDate(
            None,
            "low",
            AI_GUESS_SOURCE,
            note or "No date detected on the receipt — please enter it.",
        )

    month, day, year = parsed
    if year is None:
        completed = _complete_year(month, day, ingest_date)
        if completed is None:
            return ResolvedDate(
                None,
                "low",
                AI_GUESS_SOURCE,
                note or "No date detected on the receipt — please enter it.",
            )
        suffix = f"assuming {completed.year}"
        full_note = f"{note} ({suffix})" if note else f"Year not printed on the receipt — {suffix}."
        return ResolvedDate(completed.isoformat(), "low", AI_GUESS_SOURCE, full_note)

    resolved = _safe_date(year, month, day)
    if resolved is None:
        return ResolvedDate(
            None,
            "low",
            AI_GUESS_SOURCE,
            note or "No date detected on the receipt — please enter it.",
        )
    return ResolvedDate(
        resolved.isoformat(),
        "low",
        AI_GUESS_SOURCE,
        note or "Date recovered from the receipt — please confirm.",
    )


def date_sync_block_reason(payload: dict) -> str | None:
    """Return a human reason why ``payload``'s date blocks sync, else None.

    Safety gate: sync must never send a missing or unconfirmed-guess date to
    YNAB.  Confirming or editing the date clears ``date_source``.
    """
    if not payload.get("transaction_date"):
        return "A transaction date is required before syncing."
    if payload.get("date_source") == AI_GUESS_SOURCE:
        return "Confirm the receipt date before syncing."
    return None
