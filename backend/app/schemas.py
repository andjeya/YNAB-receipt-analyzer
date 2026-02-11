from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class ReceiptSummary(BaseModel):
    id: str
    status: str
    original_filename: str
    display_payee_name: str | None = None
    display_total_milliunits: int | None = None
    display_receipt_date: date | None = None
    ingested_at: datetime
    updated_at: datetime


class ExtractionRunOut(BaseModel):
    id: int
    model_name: str
    schema_valid: bool
    schema_errors: list[str] | None = None
    parsed_json: dict[str, Any] | None = None
    raw_output: str
    duration_ms: int
    created_at: datetime


class ValidationOut(BaseModel):
    id: int
    version: int
    source: str
    payload: dict[str, Any]
    is_valid: bool
    errors: list[str] | None = None
    created_at: datetime


class ReceiptDetailOut(BaseModel):
    id: str
    status: str
    status_reason: str | None = None
    original_filename: str
    storage_key: str
    mime_type: str
    display_payee_name: str | None = None
    display_total_milliunits: int | None = None
    display_receipt_date: date | None = None
    latest_extraction: ExtractionRunOut | None = None
    latest_validation: ValidationOut | None = None
    ingested_at: datetime
    extraction_started_at: datetime | None = None
    extraction_completed_at: datetime | None = None
    sync_started_at: datetime | None = None
    sync_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SaveDraftRequest(BaseModel):
    payload: dict[str, Any]
    source: str = Field(default="user")


class SaveDraftResponse(BaseModel):
    validation: ValidationOut
    can_sync: bool


class SyncRequest(BaseModel):
    force_create: bool = False
    allow_update_match: bool = True


class SyncEnqueueResponse(BaseModel):
    receipt_id: str
    queue_name: str
    job_id: str
    status: str


class CacheEntityOut(BaseModel):
    entity_type: str
    entity_id: str
    name: str
    group_name: str | None = None
    raw_json: dict[str, Any]
    fetched_at: datetime


class CacheRefreshResponse(BaseModel):
    refreshed_at: datetime
    category_count: int
    account_count: int
    payee_count: int


class StatsSummary(BaseModel):
    status_counts: dict[str, int]
    avg_extraction_duration_ms: float | None = None
    avg_validation_duration_ms: float | None = None
    avg_receipt_age_at_validation_ms: float | None = None
