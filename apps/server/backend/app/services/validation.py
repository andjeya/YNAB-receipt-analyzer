from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from receipt_shared.contracts import ValidationPayload
from receipt_shared.money import dollars_to_milliunits

UNKNOWN_ACCOUNT_ID = "__unknown__"


def _to_decimal(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    """Format pydantic ValidationError messages, substituting friendly text for known fields."""
    messages: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        msg = err["msg"]
        # Provide a friendlier message when a split amount fails the ge=0 constraint.
        if (
            len(loc) >= 2
            and loc[-1] == "amount"
            and err.get("type") == "greater_than_equal"
        ):
            msg = "Split amounts must be zero or greater (direction is set by transaction kind)"
        messages.append(msg)
    return messages


def validate_payload(
    payload: dict[str, Any],
    *,
    allowed_category_ids: set[str] | None = None,
    allowed_account_ids: set[str] | None = None,
    allow_unknown_account: bool = False,
) -> tuple[dict[str, Any], bool, list[str]]:
    try:
        parsed = ValidationPayload.model_validate(payload)
    except ValidationError as exc:
        return payload, False, _format_pydantic_errors(exc)

    errors: list[str] = []
    total = _to_decimal(parsed.total_amount)

    if total <= 0:
        errors.append("Total amount must be greater than zero (use transaction_kind='refund' for returns)")

    if parsed.splits:
        split_total = sum((_to_decimal(split.amount) for split in parsed.splits), Decimal("0"))
        if abs(total - split_total) > Decimal("0.01"):
            errors.append("Split amounts must sum to total amount")
        else:
            # Exact milliunit check: sub-cent drift that passes the $0.01 gross check
            # must still be rejected to keep the YNAB payload builder invariant.
            total_mu = dollars_to_milliunits(parsed.total_amount, outflow=False)
            split_mu = sum(dollars_to_milliunits(s.amount, outflow=False) for s in parsed.splits)
            if split_mu != total_mu:
                errors.append(
                    f"Split amounts must sum to total amount in milliunits "
                    f"(splits sum to {split_mu} milliunits, total is {total_mu} milliunits)"
                )
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
        if not allow_unknown_account:
            errors.append("Account is unknown. Select a valid YNAB account before syncing")
    elif allowed_account_ids is not None:
        if not allowed_account_ids:
            errors.append("No YNAB accounts are currently cached")
        elif parsed.account_id not in allowed_account_ids:
            errors.append(f"Invalid account_id: {parsed.account_id}")

    normalized = parsed.model_dump(mode="json")
    return normalized, len(errors) == 0, errors


def normalize_payload_for_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    """Re-serialize a stored payload through the current contract.

    Payloads persisted before the contract gained a new optional field lack
    that key, while freshly normalized payloads carry it as None; comparing
    raw dicts would report a spurious change (and e.g. flip SYNCED receipts
    back to needs_review). Falls back to the raw dict for payloads that no
    longer parse under the current contract.
    """
    try:
        return ValidationPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        return payload


def payloads_equivalent(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """True when two validation payloads agree on everything the user can see.

    account_source is provenance metadata (who picked the account), not a
    money or identity field — a save that only gains/loses it must not count
    as a payload change, or no-op saves would flip SYNCED receipts back to
    needs_review.
    """
    a = dict(normalize_payload_for_comparison(old))
    b = dict(normalize_payload_for_comparison(new))
    a.pop("account_source", None)
    b.pop("account_source", None)
    return a == b


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
        "transaction_kind": parsed_extraction.get("transaction_kind") or "purchase",
        "category_id": category_id or "",
        "splits": parsed_splits if len(parsed_splits) >= 2 else [],
    }
