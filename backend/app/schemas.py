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
    correction_detected_at: datetime | None = None
    correction_expires_at: datetime | None = None
    correction_shade_opacity: float | None = None
    correction_message: str | None = None


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
    model_validation: ValidationOut | None = None
    ingested_at: datetime
    extraction_started_at: datetime | None = None
    extraction_completed_at: datetime | None = None
    sync_started_at: datetime | None = None
    sync_completed_at: datetime | None = None
    has_successful_sync: bool = False
    correction_detected_at: datetime | None = None
    correction_expires_at: datetime | None = None
    correction_shade_opacity: float | None = None
    correction_message: str | None = None
    correction_history: list["ReceiptCorrectionOut"] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ReceiptCorrectionOut(BaseModel):
    id: int
    receipt_id: str
    ynab_transaction_id: str | None = None
    synced_category_id: str | None = None
    corrected_category_id: str | None = None
    synced_splits_json: list[dict[str, Any]] | None = None
    corrected_splits_json: list[dict[str, Any]] | None = None
    detected_at: datetime
    expires_at: datetime
    resynced_at: datetime | None = None
    resync_penalty_applied: bool
    note: str | None = None


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
    water_capacity: int
    bucket_capacity: int
    fire_burn_threshold: int


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
    weekly_slots: list["GameWeeklySlotOut"] = Field(default_factory=list)


class GameWeeklySlotOut(BaseModel):
    index: int
    start_at: datetime
    end_at: datetime
    is_empty: bool
    display_state: str | None = None
    receipt_count: int = 0


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


class GameCorrectnessOut(BaseModel):
    water_units: int
    water_capacity: int
    bucket_capacity: int
    buckets_filled: int
    fire_units: int
    small_fires: int
    medium_fires: int
    large_fires: int
    burn_count: int
    last_burned_at: datetime | None = None
    last_reconciled_at: datetime | None = None


class GameDashboardOut(BaseModel):
    generated_at: datetime
    window: str
    rules: GameRulesOut
    momentum: GameMomentumOut
    forest: GameForestOut
    correctness: GameCorrectnessOut
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


class GameReconcileResponse(BaseModel):
    scanned_receipts: int
    detected_mistakes: int
    applied_penalties: int
    run_id: int


class GameCorrectnessRecomputeResponse(BaseModel):
    correction_count: int
    water_units: int
    fire_units: int
    burn_count: int


ReceiptDetailOut.model_rebuild()
GameForestOut.model_rebuild()
