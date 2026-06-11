from __future__ import annotations

import re
from datetime import date, datetime, time

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

    payee_name: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    transaction_date: date
    transaction_time: time | None = None
    memo: str = ""
    total_amount: float
    transaction_kind: str = Field(default="purchase")
    category_id: str | None = None
    splits: list[ValidationSplit] = Field(default_factory=list)

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
