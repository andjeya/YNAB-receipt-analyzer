from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ReceiptStatus
from app.models import Receipt

_PAYEE_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")


@dataclass
class DuplicateCheckResult:
    signature: str | None
    duplicate_of_receipt_id: str | None
    match_count: int


def normalize_payee_key(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    canonical = raw.replace("’", "'").replace("`", "'").replace("'", "")
    canonical = _PAYEE_NON_ALNUM_RE.sub(" ", canonical)
    canonical = _SPACE_RE.sub(" ", canonical).strip()
    return canonical or None


def normalize_transaction_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def normalize_transaction_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"

    text = str(value).strip()
    if not text:
        return None

    match = _TIME_RE.match(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def normalize_total_cents(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except Exception:
        return None
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(quantized * 100)


def build_semantic_signature(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    payee_key = normalize_payee_key(payload.get("payee_name"))
    date_key = normalize_transaction_date(payload.get("transaction_date"))
    time_key = normalize_transaction_time(payload.get("transaction_time"))
    total_cents = normalize_total_cents(payload.get("total_amount"))
    if payee_key is None or date_key is None or time_key is None or total_cents is None:
        return None
    raw = f"{payee_key}|{date_key}|{time_key}|{total_cents}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _semantic_parts(payload: dict[str, Any] | None) -> tuple[str | None, str | None, str | None, int | None, str | None]:
    if not payload:
        return None, None, None, None, None
    payee_key = normalize_payee_key(payload.get("payee_name"))
    date_key = normalize_transaction_date(payload.get("transaction_date"))
    time_key = normalize_transaction_time(payload.get("transaction_time"))
    total_cents = normalize_total_cents(payload.get("total_amount"))
    signature = None
    if payee_key is not None and date_key is not None and time_key is not None and total_cents is not None:
        raw = f"{payee_key}|{date_key}|{time_key}|{total_cents}"
        signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return payee_key, date_key, time_key, total_cents, signature


def apply_semantic_duplicate_state(
    db: Session,
    *,
    receipt: Receipt,
    payload: dict[str, Any] | None,
) -> DuplicateCheckResult:
    payee_key, date_key, time_key, total_cents, signature = _semantic_parts(payload)

    receipt.semantic_payee_key = payee_key
    receipt.semantic_transaction_date = date.fromisoformat(date_key) if date_key else None
    receipt.semantic_transaction_time = time_key
    receipt.semantic_total_cents = total_cents
    receipt.semantic_signature = signature

    if signature is None:
        receipt.duplicate_of_receipt_id = None
        if receipt.status == ReceiptStatus.DUPLICATE_REVIEW.value:
            receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            receipt.status_reason = "Duplicate check skipped: payee/date/time/total incomplete."
        return DuplicateCheckResult(signature=None, duplicate_of_receipt_id=None, match_count=0)

    if receipt.duplicate_override_signature and receipt.duplicate_override_signature == signature:
        receipt.duplicate_of_receipt_id = None
        if receipt.status == ReceiptStatus.DUPLICATE_REVIEW.value:
            receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            receipt.status_reason = "Duplicate detection was overridden by user."
        return DuplicateCheckResult(signature=signature, duplicate_of_receipt_id=None, match_count=0)

    if receipt.duplicate_override_signature and receipt.duplicate_override_signature != signature:
        receipt.duplicate_override_signature = None

    matches = list(
        db.scalars(
            select(Receipt)
            .where(
                Receipt.semantic_signature == signature,
                Receipt.id != receipt.id,
            )
            .order_by(Receipt.ingested_at.asc(), Receipt.id.asc())
        )
    )
    if not matches:
        receipt.duplicate_of_receipt_id = None
        if receipt.status == ReceiptStatus.DUPLICATE_REVIEW.value:
            receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            receipt.status_reason = None
        return DuplicateCheckResult(signature=signature, duplicate_of_receipt_id=None, match_count=0)

    non_duplicate_review_matches = [row for row in matches if row.status != ReceiptStatus.DUPLICATE_REVIEW.value]
    if not non_duplicate_review_matches:
        receipt.duplicate_of_receipt_id = None
        if receipt.status == ReceiptStatus.DUPLICATE_REVIEW.value:
            receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        receipt.status_reason = "Duplicate check ignored: only duplicate-review matches found."
        return DuplicateCheckResult(signature=signature, duplicate_of_receipt_id=None, match_count=0)

    chosen_pool = non_duplicate_review_matches
    duplicate_of = chosen_pool[0]

    receipt.duplicate_of_receipt_id = duplicate_of.id
    receipt.status = ReceiptStatus.DUPLICATE_REVIEW.value
    if len(chosen_pool) > 1:
        receipt.status_reason = (
            f"Duplicate candidate detected against {duplicate_of.id} (and {len(chosen_pool) - 1} additional matches)."
        )
    else:
        receipt.status_reason = f"Duplicate candidate detected against receipt {duplicate_of.id}."

    return DuplicateCheckResult(
        signature=signature,
        duplicate_of_receipt_id=duplicate_of.id,
        match_count=len(chosen_pool),
    )
