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
  ingested_at: string;
  extraction_started_at: string | null;
  extraction_completed_at: string | null;
  sync_started_at: string | null;
  sync_completed_at: string | null;
  created_at: string;
  updated_at: string;
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
