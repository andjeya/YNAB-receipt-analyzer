from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from receipt_shared.contracts import ValidationPayload


def _to_decimal(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool, list[str]]:
    try:
        parsed = ValidationPayload.model_validate(payload)
    except ValidationError as exc:
        return payload, False, [err["msg"] for err in exc.errors()]

    errors: list[str] = []
    total = _to_decimal(parsed.total_amount)
    split_total = sum((_to_decimal(split.amount) for split in parsed.splits), Decimal("0"))

    if total <= 0:
        errors.append("Total amount must be greater than zero")

    if abs(total - split_total) > Decimal("0.01"):
        errors.append("Split amounts must sum to total amount")

    normalized = parsed.model_dump(mode="json")
    return normalized, len(errors) == 0, errors


def build_initial_validation_payload(parsed_extraction: dict[str, Any], default_account_id: str | None) -> dict[str, Any]:
    return {
        "payee_name": parsed_extraction.get("payee_name") or "Receipt Import",
        "account_id": default_account_id or "",
        "transaction_date": parsed_extraction.get("transaction_date"),
        "memo": parsed_extraction.get("memo") or "Imported from receipt via Gemini",
        "total_amount": parsed_extraction.get("total_amount") or 0,
        "splits": [
            {
                "category_id": split.get("category_id", ""),
                "amount": split.get("amount", 0),
                "memo": split.get("memo", ""),
            }
            for split in parsed_extraction.get("splits", [])
        ],
    }
