from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeminiSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1)
    category_name: str | None = None
    amount: float
    memo: str = ""


class GeminiReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payee_name: str = Field(min_length=1)
    transaction_date: date
    memo: str = ""
    total_amount: float
    splits: list[GeminiSplit] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_has_splits(self) -> "GeminiReceiptExtraction":
        if not self.splits:
            raise ValueError("At least one split is required")
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
    splits: list[ValidationSplit] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_has_splits(self) -> "ValidationPayload":
        if not self.splits:
            raise ValueError("At least one split is required")
        return self
