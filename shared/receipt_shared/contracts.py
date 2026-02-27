from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GeminiSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1)
    category_name: str | None = None
    amount: float
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
    total_amount: float
    category_id: str | None = None
    splits: list[GeminiSplit] = Field(default_factory=list)
    category_ambiguity_flags: list[GeminiCategoryAmbiguityFlag] = Field(default_factory=list)

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
    receipt_language: str = "en"

    # YNAB draft fields.
    payee_name: str = ""
    account_id: str = Field(min_length=1)
    memo: str = ""
    category_id: str | None = None
    splits: list[GeminiSplit] = Field(default_factory=list)
    category_ambiguity_flags: list[GeminiCategoryAmbiguityFlag] = Field(default_factory=list)

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
    receipt_language: str = "en"


class ValidationSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1)
    amount: float
    memo: str = ""


class ValidationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payee_name: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    transaction_date: date
    transaction_time: time | None = None
    memo: str = ""
    total_amount: float
    category_id: str | None = None
    splits: list[ValidationSplit] = Field(default_factory=list)

    @field_validator("category_id", mode="before")
    @classmethod
    def normalize_category_id(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def ensure_single_or_split_mode(self) -> "ValidationPayload":
        has_category = bool(self.category_id)
        split_count = len(self.splits)

        if has_category and split_count:
            raise ValueError("Provide either category_id or splits, not both")
        if not has_category and split_count == 0:
            raise ValueError("Either category_id or splits is required")
        return self
