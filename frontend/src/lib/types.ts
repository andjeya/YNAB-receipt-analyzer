export type ReceiptStatus =
  | "ingested"
  | "extracting"
  | "needs_review"
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
  ingested_at: string;
  updated_at: string;
  correction_detected_at: string | null;
  correction_expires_at: string | null;
  correction_shade_opacity: number | null;
  correction_message: string | null;
}

export interface ExtractionRun {
  id: number;
  model_name: string;
  schema_valid: boolean;
  schema_errors: string[] | null;
  parsed_json: Record<string, unknown> | null;
  raw_output: string;
  duration_ms: number;
  created_at: string;
}

export interface Validation {
  id: number;
  version: number;
  source: string;
  payload: Record<string, unknown>;
  is_valid: boolean;
  errors: string[] | null;
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
  latest_validation: Validation | null;
  model_validation: Validation | null;
  ingested_at: string;
  extraction_started_at: string | null;
  extraction_completed_at: string | null;
  sync_started_at: string | null;
  sync_completed_at: string | null;
  has_successful_sync: boolean;
  correction_detected_at: string | null;
  correction_expires_at: string | null;
  correction_shade_opacity: number | null;
  correction_message: string | null;
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

export interface ValidationPayloadInput {
  payee_name: string;
  account_id: string;
  transaction_date: string;
  memo: string;
  total_amount: number;
  category_id: string;
  splits: ValidationSplitInput[];
}

export interface SaveDraftResponse {
  validation: Validation;
  can_sync: boolean;
}

export interface SyncEnqueueResponse {
  receipt_id: string;
  queue_name: string;
  job_id: string;
  status: ReceiptStatus;
}

export interface CacheEntity {
  entity_type: "category" | "account" | "payee";
  entity_id: string;
  name: string;
  group_name: string | null;
  raw_json: Record<string, unknown>;
  fetched_at: string;
}

export interface StatsSummary {
  status_counts: Record<string, number>;
  avg_extraction_duration_ms: number | null;
  avg_validation_duration_ms: number | null;
  avg_receipt_age_at_validation_ms: number | null;
}

export type GameWindow = "week" | "month";
export type GameDisplayState = "green" | "yellow" | "brown" | "shredded";
export type GameChallengeStatus = "completed" | "in_progress";

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
  biweekly_slots: GameBiweeklySlot[];
}

export interface GameBiweeklySlot {
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

export interface GameChallenge {
  key: string;
  title: string;
  description: string;
  status: GameChallengeStatus;
  target: number;
  current: number;
  unit: string;
  progress: number;
}

export interface GameCorrectness {
  water_units: number;
  water_capacity: number;
  bucket_capacity: number;
  buckets_filled: number;
  fire_units: number;
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
  rules: GameRules;
  momentum: GameMomentum;
  forest: GameForest;
  correctness: GameCorrectness;
  summary: GameSummary;
  challenges: GameChallenge[];
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
  run_id: number;
}

export interface GameCorrectnessRecomputeResponse {
  correction_count: number;
  water_units: number;
  fire_units: number;
  burn_count: number;
}
