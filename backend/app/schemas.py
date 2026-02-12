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


class GameRulesOut(BaseModel):
    green_hours_threshold: float
    brown_hours_threshold: float
    token_earn_every_greens: int
    shred_daily_spend_cap: int


class GameMomentumOut(BaseModel):
    current_streak: int
    max_streak: int
    last_green_at: datetime | None = None
    break_reason: str | None = None
    token_balance: int
    token_earned_count: int
    token_spent_count: int
    token_threshold: int
    token_progress_current: int
    next_token_in: int
    spendable_now: bool


class GameForestTileOut(BaseModel):
    receipt_id: str
    state: str
    display_state: str
    validated_at: datetime
    age_hours_at_validation: float
    streak_group_id: int
    shredded_at: datetime | None = None
    is_latest: bool


class GameForestOut(BaseModel):
    latest_receipt_id: str | None = None
    counts: dict[str, int]
    receipts: list[GameForestTileOut]


class GameSummaryOut(BaseModel):
    window: str
    window_start: datetime
    window_end: datetime
    total_validated: int
    green_count: int
    yellow_count: int
    brown_count: int
    shredded_count: int
    green_percent: float
    avg_validation_age_hours: float | None = None


class GameChallengeOut(BaseModel):
    key: str
    title: str
    description: str
    status: str
    target: float
    current: float
    unit: str
    progress: float


class GameDashboardOut(BaseModel):
    generated_at: datetime
    window: str
    rules: GameRulesOut
    momentum: GameMomentumOut
    forest: GameForestOut
    summary: GameSummaryOut
    challenges: list[GameChallengeOut]


class GameShredResponse(BaseModel):
    receipt_id: str
    was_shredded: bool
    state: str
    token_balance: int
    token_spent_count: int


class GameRebuildResponse(BaseModel):
    processed_receipts: int
    restored_shreds: int
