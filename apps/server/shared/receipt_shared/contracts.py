from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TRANSACTION_KINDS = ("purchase", "refund")


def normalize_card_last_four(value: str | int | float | None) -> str | None:
    """Return the trailing 4 ASCII digits of a card identifier, or None.

    Drops any trailing decimal portion first so a numeric value like 5830.0 /
    "5830.0" yields "5830" (not "8300"). Restricts to ASCII digits so non-ASCII
    digit glyphs do not produce a non-ASCII key. None if fewer than 4 digits.
    """
    if value is None:
        return None
    text = re.sub(r"\.\d+$", "", str(value).strip())  # strip a trailing decimal part
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) < 4:
        return None
    return digits[-4:]


class GeminiSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1)
    category_name: str | None = None
    amount: float = Field(ge=0)
    memo: str = ""


class GeminiCategoryAmbiguityFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_item: str = ""
    candidate_category_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    note: str = ""


class GeminiCandidateSplit(BaseModel):
    """A split inside a candidate arrangement — LENIENT on purpose.

    Parsed as part of UnifiedReceiptExtraction; a malformed candidate split must
    never fail the whole extraction. Unknown keys are ignored and values coerced.
    The worker re-distributes amounts to an exact milliunit sum and re-validates,
    dropping anything that doesn't materialize cleanly.
    """

    model_config = ConfigDict(extra="ignore")

    category_id: str = ""
    amount: float = 0.0
    memo: str = ""

    @field_validator("category_id", "memo", mode="before")
    @classmethod
    def _coerce_str(cls, value: Any) -> str:
        if value is None:
            return ""
        return value if isinstance(value, str) else str(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0


class GeminiCandidateArrangement(BaseModel):
    """One ranked alternative category/split arrangement for an uncertain receipt.

    LENIENT BY CONSTRUCTION — every field has a coercing mode="before" validator so
    NO value type can raise, and the parent's field-level sanitizer drops non-dict
    entries. A malformed arrangement must never break the core unified extraction
    (candidates are additive). The worker materializes each into a sum-to-total
    payload and validates it before storing.
    """

    model_config = ConfigDict(extra="ignore")

    label: str = ""
    rationale: str = ""
    confidence: float = 0.0
    category_id: str | None = None
    splits: list[GeminiCandidateSplit] = Field(default_factory=list)

    @field_validator("label", "rationale", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return value if isinstance(value, str) else str(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(number):
            return 0.0
        return min(max(number, 0.0), 1.0)

    @field_validator("category_id", mode="before")
    @classmethod
    def _normalize_category_id(cls, value: Any) -> str | None:
        # Coerce ANY non-string to None rather than letting the str|None type check
        # reject it and fail the whole extraction.
        if isinstance(value, str):
            return value.strip() or None
        return None

    @field_validator("splits", mode="before")
    @classmethod
    def _sanitize_splits(cls, value: Any) -> list:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]


class OrganizeProposal(BaseModel):
    """Response schema for type-to-organize: 1-3 proposed category/split
    arrangements for a user's plain-English instruction. LENIENT like the
    candidate models so a bad proposal never raises."""

    model_config = ConfigDict(extra="ignore")

    proposals: list[GeminiCandidateArrangement] = Field(default_factory=list)

    @field_validator("proposals", mode="before")
    @classmethod
    def _sanitize_proposals(cls, value: Any) -> list:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]


class ReceiptLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    raw_text: str = ""
    translated_text: str = ""
    quantity: float | None = None
    unit_price: float | None = None
    line_total: float | None = None
    tax_code: str | None = None
    item_type: str = "product"


class GeminiReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payee_name: str = ""
    account_id: str = Field(min_length=1)
    transaction_date: date | None = None
    transaction_date_raw: str = ""
    date_confidence: str = "high"
    date_note: str = ""
    transaction_time: time | None = None
    memo: str = ""
    card_last_four: str | None = None
    total_amount: float
    transaction_kind: str = Field(default="purchase")
    category_id: str | None = None
    splits: list[GeminiSplit] = Field(default_factory=list)
    category_ambiguity_flags: list[GeminiCategoryAmbiguityFlag] = Field(default_factory=list)

    @field_validator("card_last_four", mode="before")
    @classmethod
    def normalize_card_last_four_field(cls, value: str | int | None) -> str | None:
        return normalize_card_last_four(value)

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("transaction_kind", mode="before")
    @classmethod
    def normalize_transaction_kind(cls, value: str | None) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "purchase"
        normalized = str(value).strip().lower()
        if normalized not in TRANSACTION_KINDS:
            raise ValueError(f"transaction_kind must be one of {TRANSACTION_KINDS}")
        return normalized

    @model_validator(mode="after")
    def ensure_single_or_split_mode(self) -> "GeminiReceiptExtraction":
        has_category = bool(self.category_id)
        split_count = len(self.splits)

        if has_category and split_count:
            raise ValueError("Provide either category_id or splits, not both")
        if not has_category and split_count == 0:
            raise ValueError("Either category_id or splits is required")
        if split_count == 1:
            raise ValueError("Split mode requires at least two splits")
        return self


class UnifiedReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Twin / receipt reality fields.
    store_name: str = ""
    store_address: str = ""
    transaction_date: date | None = None
    transaction_date_raw: str = ""
    date_confidence: str = "high"
    date_note: str = ""
    transaction_time: time | None = None
    currency: str = "USD"
    line_items: list[ReceiptLineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax_total: float | None = None
    total_amount: float
    payment_method: str = ""
    card_last_four: str | None = None
    receipt_language: str = "en"

    # YNAB draft fields.
    payee_name: str = ""
    account_id: str = Field(min_length=1)
    memo: str = ""
    transaction_kind: str = Field(default="purchase")
    category_id: str | None = None
    splits: list[GeminiSplit] = Field(default_factory=list)
    category_ambiguity_flags: list[GeminiCategoryAmbiguityFlag] = Field(default_factory=list)
    # Up to 3 ranked alternative whole-receipt arrangements, populated when category
    # confidence is low. Additive — never affects the primary category_id/splits.
    candidate_arrangements: list[GeminiCandidateArrangement] = Field(default_factory=list)

    @field_validator("candidate_arrangements", mode="before")
    @classmethod
    def _sanitize_candidate_arrangements(cls, value: Any) -> list:
        # Drop anything that isn't a dict (and coerce a non-list to []) BEFORE
        # sub-model validation, so a malformed candidates field can never fail the
        # whole extraction — candidates are additive.
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @field_validator("card_last_four", mode="before")
    @classmethod
    def normalize_card_last_four_field(cls, value: str | int | None) -> str | None:
        return normalize_card_last_four(value)

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("transaction_kind", mode="before")
    @classmethod
    def normalize_transaction_kind(cls, value: str | None) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "purchase"
        normalized = str(value).strip().lower()
        if normalized not in TRANSACTION_KINDS:
            raise ValueError(f"transaction_kind must be one of {TRANSACTION_KINDS}")
        return normalized

    @model_validator(mode="after")
    def ensure_single_or_split_mode(self) -> "UnifiedReceiptExtraction":
        has_category = bool(self.category_id)
        split_count = len(self.splits)

        if has_category and split_count:
            raise ValueError("Provide either category_id or splits, not both")
        if not has_category and split_count == 0:
            raise ValueError("Either category_id or splits is required")
        if split_count == 1:
            raise ValueError("Split mode requires at least two splits")
        return self


class ReceiptTwinExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_name: str = ""
    store_address: str = ""
    transaction_date: date | None = None
    transaction_date_raw: str = ""
    date_confidence: str = "high"
    date_note: str = ""
    transaction_time: time | None = None
    currency: str = "USD"
    line_items: list[ReceiptLineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax_total: float | None = None
    total_amount: float
    payment_method: str = ""
    card_last_four: str | None = None
    receipt_language: str = "en"

    @field_validator("card_last_four", mode="before")
    @classmethod
    def normalize_card_last_four_field(cls, value: str | int | None) -> str | None:
        return normalize_card_last_four(value)


class AllocationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    source_index: int = Field(ge=0)
    label: str = ""
    amount: float | None = None
    tax_code: str | None = None
    item_type: str = "product"


class AllocationLane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane_id: str = Field(min_length=1)
    category_id: str | None = None
    pinned_amount: float | None = None

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class AllocationAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    lane_id: str = Field(min_length=1)


class AllocationWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, default=1)
    twin_version: int = Field(ge=0, default=0)
    generated_at: datetime
    items: list[AllocationItem] = Field(default_factory=list)
    lanes: list[AllocationLane] = Field(default_factory=list)
    assignments: list[AllocationAssignment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1)
    amount: float = Field(ge=0)
    memo: str = ""


class ValidationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty-tolerant so a receipt with no readable merchant still becomes a
    # needs_review draft (twin + every other field filled) instead of hard-failing
    # extraction.  Sync is hard-gated separately on a non-blank payee — see
    # validation.payee_sync_block_reason.  Mirrors transaction_date below.
    payee_name: str = ""
    account_id: str = Field(min_length=1)
    # Nullable so a receipt with no confident date can still exist as a draft in
    # needs_review (the user fills it in).  Sync is hard-gated separately on a
    # present, confirmed date — see date_resolution.date_sync_block_reason.
    transaction_date: date | None = None
    transaction_time: time | None = None
    memo: str = ""
    total_amount: float
    transaction_kind: str = Field(default="purchase")
    category_id: str | None = None
    splits: list[ValidationSplit] = Field(default_factory=list)
    # Provenance: set to "card_mapping" when account_id was overridden by the
    # learned card→account mapping.  Absent (None) when the account came from
    # the AI model or a user edit.
    account_source: str | None = None
    # Provenance: set to "payee_memory" when category_id/splits were pre-filled
    # from the learned payee→category memory.  Absent (None) otherwise.
    category_source: str | None = None
    # Date provenance + UI hints.  date_source == "ai_guess" means the date was
    # guessed (missing year / low confidence / ambiguous) and is UNCONFIRMED:
    # the warning bubble shows and sync is blocked until the user confirms or
    # edits the date (which clears date_source).  date_confidence / date_note
    # drive the bubble text.  Excluded from payload-equivalence comparison.
    date_source: str | None = None
    date_confidence: str | None = None
    date_note: str | None = None

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("transaction_kind", mode="before")
    @classmethod
    def normalize_transaction_kind(cls, value: str | None) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "purchase"
        normalized = str(value).strip().lower()
        if normalized not in TRANSACTION_KINDS:
            raise ValueError(f"transaction_kind must be one of {TRANSACTION_KINDS}")
        return normalized

    @model_validator(mode="after")
    def ensure_single_or_split_mode(self) -> "ValidationPayload":
        has_category = bool(self.category_id)
        split_count = len(self.splits)

        if has_category and split_count:
            raise ValueError("Provide either category_id or splits, not both")
        if not has_category and split_count == 0:
            raise ValueError("Either category_id or splits is required")
        return self
