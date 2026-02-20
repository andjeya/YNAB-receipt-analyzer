from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from receipt_shared.contracts import ValidationPayload

UNKNOWN_ACCOUNT_ID = "__unknown__"


def _to_decimal(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def validate_payload(
    payload: dict[str, Any],
    *,
    allowed_category_ids: set[str] | None = None,
    allowed_account_ids: set[str] | None = None,
) -> tuple[dict[str, Any], bool, list[str]]:
    try:
        parsed = ValidationPayload.model_validate(payload)
    except ValidationError as exc:
        return payload, False, [err["msg"] for err in exc.errors()]

    errors: list[str] = []
    total = _to_decimal(parsed.total_amount)

    if total <= 0:
        errors.append("Total amount must be greater than zero")

    if parsed.splits:
        split_total = sum((_to_decimal(split.amount) for split in parsed.splits), Decimal("0"))
        if abs(total - split_total) > Decimal("0.01"):
            errors.append("Split amounts must sum to total amount")
        if allowed_category_ids is not None:
            if not allowed_category_ids:
                errors.append("No YNAB categories are currently cached")
            for split in parsed.splits:
                if split.category_id not in allowed_category_ids:
                    errors.append(f"Invalid category_id in split: {split.category_id}")
    elif allowed_category_ids is not None:
        if not allowed_category_ids:
            errors.append("No YNAB categories are currently cached")
        elif parsed.category_id not in allowed_category_ids:
            errors.append(f"Invalid category_id: {parsed.category_id}")

    if parsed.account_id == UNKNOWN_ACCOUNT_ID:
        errors.append("Account is unknown. Select a valid YNAB account before syncing")
    elif allowed_account_ids is not None:
        if not allowed_account_ids:
            errors.append("No YNAB accounts are currently cached")
        elif parsed.account_id not in allowed_account_ids:
            errors.append(f"Invalid account_id: {parsed.account_id}")

    normalized = parsed.model_dump(mode="json")
    return normalized, len(errors) == 0, errors


def build_initial_validation_payload(parsed_extraction: dict[str, Any], default_account_id: str | None) -> dict[str, Any]:
    raw_splits = parsed_extraction.get("splits", [])
    parsed_splits = [
        {
            "category_id": split.get("category_id", ""),
            "amount": split.get("amount", 0),
            "memo": split.get("memo", ""),
        }
        for split in raw_splits
        if isinstance(split, dict)
    ]

    category_id = parsed_extraction.get("category_id")
    if not category_id and len(parsed_splits) == 1:
        category_id = parsed_splits[0].get("category_id")

    payee_name = str(parsed_extraction.get("payee_name") or "").strip()
    memo = str(parsed_extraction.get("memo") or "").strip()

    return {
        "payee_name": payee_name,
        "account_id": parsed_extraction.get("account_id") or default_account_id or "",
        "transaction_date": parsed_extraction.get("transaction_date"),
        "transaction_time": parsed_extraction.get("transaction_time"),
        "memo": memo or "Imported from receipt via Gemini",
        "total_amount": parsed_extraction.get("total_amount") or 0,
        "category_id": category_id or "",
        "splits": parsed_splits if len(parsed_splits) >= 2 else [],
    }
