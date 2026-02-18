from __future__ import annotations

from datetime import date

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


class GeminiReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payee_name: str = ""
    account_id: str = Field(min_length=1)
    transaction_date: date | None = None
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
