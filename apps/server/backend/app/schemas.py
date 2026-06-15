from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class YNABSyncOut(BaseModel):
    id: int
    status: str
    match_mode: str
    raw_request: dict[str, Any] | None = None
    created_transaction_id: str | None = None
    matched_transaction_id: str | None = None
    completed_at: datetime | None = None


class ReceiptSummary(BaseModel):
    id: str
    status: str
    original_filename: str
    display_payee_name: str | None = None
    display_total_milliunits: int | None = None
    display_receipt_date: date | None = None
    transaction_kind: str = "purchase"
    ingested_at: datetime
    updated_at: datetime
    correction_detected_at: datetime | None = None
    correction_expires_at: datetime | None = None
    correction_shade_opacity: float | None = None
    correction_message: str | None = None
    duplicate_of_receipt_id: str | None = None
    sync_ready: bool = False
    # Short reason code (e.g. "ready", "needs_account", "needs_payee",
    # "confirm_date", "duplicate"). Display copy lives in the frontend. See
    # _batch_review_state.
    review_hint: str | None = None


class AppConfigOut(BaseModel):
    ynab_sync_enabled: bool
    ynab_dry_run: bool
    ynab_budget_id: str | None = None
    ynab_budget_name: str | None = None
    new_transaction_flag_color: str
    updated_transaction_flag_color: str
    debug_tools_enabled: bool = False


class ExtractionRunOut(BaseModel):
    id: int
    model_name: str
    schema_valid: bool
    schema_errors: list[str] | None = None
    parsed_json: dict[str, Any] | None = None
    raw_output: str
    duration_ms: int
    attempt_kind: str = "unified"
    is_primary_result: bool = False
    parent_run_id: int | None = None
    created_at: datetime


class ValidationOut(BaseModel):
    id: int
    version: int
    source: str
    payload: dict[str, Any]
    allocation_workspace: dict[str, Any] | None = None
    is_valid: bool
    errors: list[str] | None = None
    created_at: datetime


class LockedFieldsOut(BaseModel):
    transaction_date: bool = False
    transaction_time: bool = False
    total_amount: bool = False


class ConfirmedSectionsOut(BaseModel):
    date_time: bool = False
    total: bool = False


class ReceiptTwinOut(BaseModel):
    id: int
    receipt_id: str
    version: int
    source: str
    payload: dict[str, Any]
    confirmed_sections: ConfirmedSectionsOut
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
    extraction_primary: ExtractionRunOut | None = None
    latest_validation: ValidationOut | None = None
    model_validation: ValidationOut | None = None
    latest_twin: ReceiptTwinOut | None = None
    locked_fields: LockedFieldsOut = Field(default_factory=LockedFieldsOut)
    ingested_at: datetime
    extraction_started_at: datetime | None = None
    extraction_completed_at: datetime | None = None
    sync_started_at: datetime | None = None
    sync_completed_at: datetime | None = None
    has_successful_sync: bool = False
    latest_sync: "YNABSyncOut | None" = None
    correction_detected_at: datetime | None = None
    correction_expires_at: datetime | None = None
    correction_shade_opacity: float | None = None
    correction_message: str | None = None
    duplicate_of_receipt_id: str | None = None
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
    allocation_workspace: dict[str, Any] | None = None
    source: str = Field(default="user")


class SaveDraftResponse(BaseModel):
    validation: ValidationOut
    can_sync: bool
    lock_warnings: list[str] = Field(default_factory=list)


class AllocationRecomputeRequest(BaseModel):
    workspace: dict[str, Any]
    mode: Literal["discard_manual_amounts", "keep_manual_amounts"] = "discard_manual_amounts"


class AllocationRecomputeResponse(BaseModel):
    payload: dict[str, Any]
    workspace: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class SyncRequest(BaseModel):
    force_create: bool = False
    allow_update_match: bool = True


class SyncEnqueueResponse(BaseModel):
    receipt_id: str
    queue_name: str
    job_id: str
    status: str


class DuplicateConfirmResponse(BaseModel):
    deleted_receipt_id: str
    kept_receipt_id: str


class DeleteReceiptResponse(BaseModel):
    receipt_id: str
    deleted: bool = True


class RestoreReceiptResponse(BaseModel):
    receipt_id: str
    status: str


class DuplicateOverrideRequest(BaseModel):
    confirmed: bool = False


class DuplicateOverrideResponse(BaseModel):
    receipt_id: str
    status: str
    duplicate_of_receipt_id: str | None = None


class SaveTwinRequest(BaseModel):
    base_version: int = Field(ge=0)
    payload: dict[str, Any]
    source: str = Field(default="user")


class SaveTwinResponse(BaseModel):
    twin: ReceiptTwinOut
    changed: bool


class TwinConfirmRequest(BaseModel):
    section: Literal["date_time", "total"]
    confirmed: bool


class TwinConfirmResponse(BaseModel):
    twin: ReceiptTwinOut
    validation: ValidationOut | None = None


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


class FetchYnabUpdatesResponse(BaseModel):
    refreshed_at: datetime
    category_count: int
    account_count: int
    payee_count: int
    run_id: int
    scanned_receipts: int
    detected_mistakes: int
    applied_penalties: int
    fires_added: int
    waters_spent: int
    burns_triggered: int


class StatsSummary(BaseModel):
    status_counts: dict[str, int]
    avg_extraction_duration_ms: float | None = None
    avg_validation_duration_ms: float | None = None
    avg_receipt_age_at_validation_ms: float | None = None


class GameRulesOut(BaseModel):
    green_hours_threshold: float
    brown_hours_threshold: float
    shred_daily_spend_cap: int
    water_capacity: int
    fire_burn_threshold: int
    pass_every_green_weeks: int
    timezone: str


class GameMomentumOut(BaseModel):
    current_streak: int
    max_streak: int
    token_balance: int
    token_earned_count: int
    token_spent_count: int
    pass_every_green_weeks: int
    next_pass_in_weeks: int
    spendable_now: bool
    shred_window_weeks: int


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
    flames: int = 0
    burnt: bool = False


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
    last_reconciled_at: datetime | None = None
    total_active_flames: int = 0
    burnt_week_count: int = 0


class GameDashboardOut(BaseModel):
    generated_at: datetime
    window: str
    debug_tools_enabled: bool = False
    user_name: str | None = None
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
    fires_added: int = 0
    waters_spent: int = 0
    burns_triggered: int = 0
    run_id: int


class GameCorrectnessRecomputeResponse(BaseModel):
    correction_count: int
    water_units: int
    total_active_flames: int = 0
    burnt_week_count: int = 0


class GameWaterSpendRequest(BaseModel):
    units: int = Field(ge=1, default=1)
    week_start_at: datetime


class GameWaterSpendResponse(BaseModel):
    waters_spent: int
    fires_extinguished: int
    water_units: int
    week_flames_active: int


class GameIncidentOut(BaseModel):
    id: int
    incident_type: str
    severity: str
    title: str
    message: str
    details_json: dict[str, Any] | None = None
    created_at: datetime
    acknowledged_at: datetime | None = None


class GameDebugSeedOut(BaseModel):
    enabled: bool
    water_units: int
    water_earned_count: int
    water_spent_count: int
    token_balance: int
    token_earned_count: int
    token_spent_count: int
    current_week_flames: int = 0
    correctness_event_floor_id: int
    sync_floor_unix_ms: int


class GameSettingsOut(BaseModel):
    user_name: str | None = None
    green_hours_threshold: float = 24.0
    brown_hours_threshold: float = 72.0
    shred_window_weeks: int = 2


class GameSettingsUpdateRequest(BaseModel):
    user_name: str | None = None
    green_hours_threshold: float | None = None
    brown_hours_threshold: float | None = None
    shred_window_weeks: int | None = None


class GameDebugSeedUpdateRequest(BaseModel):
    enabled: bool | None = None
    water_units: int | None = None
    water_earned_count: int | None = None
    water_spent_count: int | None = None
    token_balance: int | None = None
    token_earned_count: int | None = None
    token_spent_count: int | None = None
    current_week_flames: int | None = None
    reset_floors_to_now: bool = False
    apply_to_live_state: bool = True


class CardMappingOut(BaseModel):
    id: int
    card_last_four: str
    account_id: str
    account_name: str | None = None


class CardMappingListOut(BaseModel):
    items: list[CardMappingOut]


class CardMappingUpsertRequest(BaseModel):
    card_last_four: str
    account_id: str


ReceiptDetailOut.model_rebuild()
GameForestOut.model_rebuild()
