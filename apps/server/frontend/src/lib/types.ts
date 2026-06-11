export type ReceiptStatus =
  | "ingested"
  | "extracting"
  | "needs_review"
  | "duplicate_review"
  | "syncing"
  | "synced"
  | "error_extract"
  | "error_sync";

export interface ReceiptSummary {
  id: string;
  status: ReceiptStatus;
  original_filename: string;
  display_payee_name: string | null;
  display_total_milliunits: number | null;
  display_receipt_date: string | null;
  transaction_kind: "purchase" | "refund";
  ingested_at: string;
  updated_at: string;
  correction_detected_at: string | null;
  correction_expires_at: string | null;
  correction_shade_opacity: number | null;
  correction_message: string | null;
  duplicate_of_receipt_id: string | null;
}

export interface AppConfig {
  ynab_sync_enabled: boolean;
  ynab_dry_run: boolean;
  ynab_budget_id: string | null;
  ynab_budget_name: string | null;
  new_transaction_flag_color: string;
  updated_transaction_flag_color: string;
}

export interface YNABSyncRecord {
  id: number;
  status: string;
  match_mode: string;
  raw_request: Record<string, unknown> | null;
  created_transaction_id: string | null;
  matched_transaction_id: string | null;
  completed_at: string | null;
}

export interface ExtractionRun {
  id: number;
  model_name: string;
  schema_valid: boolean;
  schema_errors: string[] | null;
  parsed_json: Record<string, unknown> | null;
  raw_output: string;
  duration_ms: number;
  attempt_kind: "unified" | "fallback_ynab" | "fallback_twin" | string;
  is_primary_result: boolean;
  parent_run_id: number | null;
  created_at: string;
}

export interface Validation {
  id: number;
  version: number;
  source: string;
  payload: Record<string, unknown>;
  allocation_workspace: AllocationWorkspace | null;
  is_valid: boolean;
  errors: string[] | null;
  created_at: string;
}

export interface ConfirmedSections {
  date_time: boolean;
  total: boolean;
}

export interface LockedFields {
  transaction_date: boolean;
  transaction_time: boolean;
  total_amount: boolean;
}

export interface ReceiptLineItem {
  index: number;
  raw_text: string;
  translated_text: string;
  quantity: number | null;
  unit_price: number | null;
  line_total: number | null;
  tax_code: string | null;
  item_type: "product" | "discount" | "tax" | "fee" | "subtotal" | "total" | "other" | string;
}

export interface ReceiptTwinPayload {
  store_name: string;
  store_address: string;
  transaction_date: string | null;
  transaction_time: string | null;
  currency: string;
  line_items: ReceiptLineItem[];
  subtotal: number | null;
  tax_total: number | null;
  total_amount: number;
  payment_method: string;
  receipt_language: string;
}

export interface ReceiptTwin {
  id: number;
  receipt_id: string;
  version: number;
  source: string;
  payload: ReceiptTwinPayload;
  confirmed_sections: ConfirmedSections;
  created_at: string;
}

export interface ReceiptDetail {
  id: string;
  status: ReceiptStatus;
  status_reason: string | null;
  original_filename: string;
  storage_key: string;
  mime_type: string;
  display_payee_name: string | null;
  display_total_milliunits: number | null;
  display_receipt_date: string | null;
  latest_extraction: ExtractionRun | null;
  extraction_primary: ExtractionRun | null;
  latest_validation: Validation | null;
  model_validation: Validation | null;
  latest_twin: ReceiptTwin | null;
  locked_fields: LockedFields;
  ingested_at: string;
  extraction_started_at: string | null;
  extraction_completed_at: string | null;
  sync_started_at: string | null;
  sync_completed_at: string | null;
  has_successful_sync: boolean;
  latest_sync: YNABSyncRecord | null;
  correction_detected_at: string | null;
  correction_expires_at: string | null;
  correction_shade_opacity: number | null;
  correction_message: string | null;
  duplicate_of_receipt_id: string | null;
  correction_history: ReceiptCorrection[];
  created_at: string;
  updated_at: string;
}

export interface ReceiptCorrection {
  id: number;
  receipt_id: string;
  ynab_transaction_id: string | null;
  synced_category_id: string | null;
  corrected_category_id: string | null;
  synced_splits_json: Array<Record<string, unknown>> | null;
  corrected_splits_json: Array<Record<string, unknown>> | null;
  detected_at: string;
  expires_at: string;
  resynced_at: string | null;
  resync_penalty_applied: boolean;
  note: string | null;
}

export interface ValidationSplitInput {
  category_id: string;
  amount: number;
  memo: string;
}

export interface AllocationItem {
  item_id: string;
  source_index: number;
  label: string;
  amount: number | null;
  tax_code: string | null;
  item_type: string;
}

export interface AllocationLane {
  lane_id: string;
  category_id: string | null;
  pinned_amount: number | null;
}

export interface AllocationAssignment {
  item_id: string;
  lane_id: string;
}

export interface AllocationWorkspace {
  version: number;
  twin_version: number;
  generated_at: string;
  items: AllocationItem[];
  lanes: AllocationLane[];
  assignments: AllocationAssignment[];
  warnings: string[];
}

export interface ValidationPayloadInput {
  payee_name: string;
  account_id: string;
  transaction_date: string;
  transaction_time?: string | null;
  memo: string;
  total_amount: number;
  transaction_kind: "purchase" | "refund";
  category_id: string;
  splits: ValidationSplitInput[];
}

export interface SaveDraftResponse {
  validation: Validation;
  can_sync: boolean;
  lock_warnings: string[];
}

export interface AllocationRecomputeResponse {
  payload: ValidationPayloadInput;
  workspace: AllocationWorkspace;
  warnings: string[];
}

export interface SaveTwinRequest {
  base_version: number;
  payload: ReceiptTwinPayload;
  source?: string;
}

export interface SaveTwinResponse {
  twin: ReceiptTwin;
  changed: boolean;
}

export interface TwinConfirmRequest {
  section: "date_time" | "total";
  confirmed: boolean;
}

export interface TwinConfirmResponse {
  twin: ReceiptTwin;
  validation: Validation | null;
}

export interface SyncEnqueueResponse {
  receipt_id: string;
  queue_name: string;
  job_id: string;
  status: ReceiptStatus;
}

export interface DuplicateConfirmResponse {
  deleted_receipt_id: string;
  kept_receipt_id: string;
}

export interface DuplicateOverrideResponse {
  receipt_id: string;
  status: ReceiptStatus;
  duplicate_of_receipt_id: string | null;
}

export interface CacheEntity {
  entity_type: "category" | "account" | "payee";
  entity_id: string;
  name: string;
  group_name: string | null;
  raw_json: Record<string, unknown>;
  fetched_at: string;
}

export interface FetchYnabUpdatesResponse {
  refreshed_at: string;
  category_count: number;
  account_count: number;
  payee_count: number;
  run_id: number;
  scanned_receipts: number;
  detected_mistakes: number;
  applied_penalties: number;
  fires_added: number;
  waters_spent: number;
  burns_triggered: number;
}

export interface StatsSummary {
  status_counts: Record<string, number>;
  avg_extraction_duration_ms: number | null;
  avg_validation_duration_ms: number | null;
  avg_receipt_age_at_validation_ms: number | null;
}

export type GameWindow = "week" | "month";
export type GameDisplayState = "green" | "yellow" | "brown" | "shredded";

export interface GameRules {
  green_hours_threshold: number;
  brown_hours_threshold: number;
  token_earn_every_greens: number;
  shred_daily_spend_cap: number;
  water_capacity: number;
  bucket_capacity: number;
  fire_burn_threshold: number;
}

export interface GameMomentum {
  current_streak: number;
  max_streak: number;
  last_green_at: string | null;
  break_reason: string | null;
  token_balance: number;
  token_earned_count: number;
  token_spent_count: number;
  token_threshold: number;
  token_progress_current: number;
  next_token_in: number;
  spendable_now: boolean;
}

export interface GameForestTile {
  receipt_id: string;
  state: "green" | "yellow" | "brown";
  display_state: GameDisplayState;
  validated_at: string;
  age_hours_at_validation: number;
  streak_group_id: number;
  shredded_at: string | null;
  is_latest: boolean;
}

export interface GameForest {
  latest_receipt_id: string | null;
  counts: Record<GameDisplayState, number>;
  receipts: GameForestTile[];
  weekly_slots: GameWeeklySlot[];
}

export interface GameWeeklySlot {
  index: number;
  start_at: string;
  end_at: string;
  is_empty: boolean;
  display_state: Exclude<GameDisplayState, "shredded"> | null;
  receipt_count: number;
}

export interface GameSummary {
  window: GameWindow;
  window_start: string;
  window_end: string;
  total_validated: number;
  green_count: number;
  yellow_count: number;
  brown_count: number;
  shredded_count: number;
  green_percent: number;
  avg_validation_age_hours: number | null;
}

export interface GameCorrectness {
  water_units: number;
  water_capacity: number;
  bucket_capacity: number;
  buckets_filled: number;
  fire_units: number;
  fires_to_burn: number;
  small_fires: number;
  medium_fires: number;
  large_fires: number;
  burn_count: number;
  last_burned_at: string | null;
  last_reconciled_at: string | null;
}

export interface GameDashboard {
  generated_at: string;
  window: GameWindow;
  debug_tools_enabled: boolean;
  rules: GameRules;
  momentum: GameMomentum;
  forest: GameForest;
  correctness: GameCorrectness;
  summary: GameSummary;
}

export interface GameShredResponse {
  receipt_id: string;
  was_shredded: boolean;
  state: GameDisplayState;
  token_balance: number;
  token_spent_count: number;
}

export interface GameRebuildResponse {
  processed_receipts: number;
  restored_shreds: number;
}

export interface GameReconcileResponse {
  scanned_receipts: number;
  detected_mistakes: number;
  applied_penalties: number;
  fires_added: number;
  waters_spent: number;
  burns_triggered: number;
  run_id: number;
}

export interface GameCorrectnessRecomputeResponse {
  correction_count: number;
  water_units: number;
  fire_units: number;
  burn_count: number;
}

export interface GameWaterSpendResponse {
  waters_spent: number;
  fires_extinguished: number;
  water_units: number;
  fire_units: number;
}

export interface GameIncident {
  id: number;
  incident_type: string;
  severity: "info" | "warning" | "critical" | string;
  title: string;
  message: string;
  details_json: Record<string, unknown> | null;
  created_at: string;
  acknowledged_at: string | null;
}

export interface GameDebugSeed {
  enabled: boolean;
  water_units: number;
  water_earned_count: number;
  water_spent_count: number;
  fire_units: number;
  fire_added_count: number;
  fire_extinguished_count: number;
  burn_count: number;
  token_balance: number;
  token_earned_count: number;
  token_spent_count: number;
  current_streak: number;
  max_streak: number;
  active_streak_group_id: number;
  break_reason: string | null;
  correctness_event_floor_id: number;
  sync_floor_unix_ms: number;
}

export interface GameDebugSeedUpdateRequest {
  enabled?: boolean;
  water_units?: number;
  water_earned_count?: number;
  water_spent_count?: number;
  fire_units?: number;
  fire_added_count?: number;
  fire_extinguished_count?: number;
  burn_count?: number;
  token_balance?: number;
  token_earned_count?: number;
  token_spent_count?: number;
  current_streak?: number;
  max_streak?: number;
  active_streak_group_id?: number;
  break_reason?: string | null;
  reset_floors_to_now?: boolean;
  apply_to_live_state?: boolean;
}
