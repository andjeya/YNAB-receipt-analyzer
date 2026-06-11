import {
  AppConfig,
  CacheEntity,
  FetchYnabUpdatesResponse,
  GameDebugSeed,
  GameDebugSeedUpdateRequest,
  GameCorrectnessRecomputeResponse,
  GameDashboard,
  DuplicateConfirmResponse,
  DuplicateOverrideResponse,
  GameIncident,
  GameReconcileResponse,
  GameRebuildResponse,
  GameShredResponse,
  GameWaterSpendResponse,
  GameWindow,
  ReceiptDetail,
  ReceiptSummary,
  SaveDraftResponse,
  AllocationWorkspace,
  AllocationRecomputeResponse,
  SaveTwinRequest,
  SaveTwinResponse,
  StatsSummary,
  SyncEnqueueResponse,
  TwinConfirmRequest,
  TwinConfirmResponse,
  ReceiptTwin,
  ValidationPayloadInput,
} from "@/lib/types";
import { ApiError, extractDetailMessage } from "@/lib/api-error";

export { ApiError } from "@/lib/api-error";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    const message = extractDetailMessage(body, response.status);
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function receiptFileUrl(receiptId: string, preview = true): string {
  const query = preview ? "?preview=true" : "";
  return `${API_BASE}/receipts/${receiptId}/file${query}`;
}

export function listReceipts(status?: string, sort: "newest" | "oldest" = "newest") {
  const params = new URLSearchParams();
  if (status) {
    params.set("status", status);
  }
  params.set("sort", sort);
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<ReceiptSummary[]>(`/receipts${query}`);
}

export function getReceiptDetail(receiptId: string) {
  return request<ReceiptDetail>(`/receipts/${receiptId}`);
}

export function saveDraft(
  receiptId: string,
  payload: ValidationPayloadInput,
  allocationWorkspace?: AllocationWorkspace | Record<string, unknown> | null,
) {
  return request<SaveDraftResponse>(`/receipts/${receiptId}/draft`, {
    method: "POST",
    body: JSON.stringify({ payload, allocation_workspace: allocationWorkspace ?? null, source: "user" }),
  });
}

export function recomputeAllocationWorkspace(
  receiptId: string,
  workspace: AllocationWorkspace | Record<string, unknown>,
  mode: "discard_manual_amounts" | "keep_manual_amounts" = "discard_manual_amounts",
) {
  return request<AllocationRecomputeResponse>(`/receipts/${receiptId}/allocation/recompute`, {
    method: "POST",
    body: JSON.stringify({ workspace, mode }),
  });
}

export function enqueueSync(receiptId: string) {
  return request<SyncEnqueueResponse>(`/receipts/${receiptId}/sync`, {
    method: "POST",
    body: "{}",
  });
}

export function confirmDuplicateReceipt(receiptId: string) {
  return request<DuplicateConfirmResponse>(`/receipts/${receiptId}/duplicate/confirm`, {
    method: "POST",
    body: "{}",
  });
}

export function overrideDuplicateReceipt(receiptId: string) {
  return request<DuplicateOverrideResponse>(`/receipts/${receiptId}/duplicate/override`, {
    method: "POST",
    body: JSON.stringify({ confirmed: true }),
  });
}

export function getReceiptTwin(receiptId: string) {
  return request<ReceiptTwin>(`/receipts/${receiptId}/twin`);
}

export function saveReceiptTwin(receiptId: string, payload: SaveTwinRequest) {
  return request<SaveTwinResponse>(`/receipts/${receiptId}/twin`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function confirmTwinSection(receiptId: string, payload: TwinConfirmRequest) {
  return request<TwinConfirmResponse>(`/receipts/${receiptId}/twin/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function retryTwinExtraction(receiptId: string) {
  return request<SyncEnqueueResponse>(`/receipts/${receiptId}/twin/retry-extract`, {
    method: "POST",
    body: "{}",
  });
}

export function getYnabCache(entityType?: "category" | "account" | "payee") {
  const query = entityType ? `?entity_type=${encodeURIComponent(entityType)}` : "";
  return request<CacheEntity[]>(`/ynab/cache${query}`);
}

export function refreshYnabCache() {
  return request<{ refreshed_at: string; category_count: number; account_count: number; payee_count: number }>(
    "/ynab/cache/refresh",
    { method: "POST", body: "{}" },
  );
}

export function fetchYnabUpdates() {
  return request<FetchYnabUpdatesResponse>("/ynab/updates/fetch", {
    method: "POST",
    body: "{}",
  });
}

export function getStatsSummary() {
  return request<StatsSummary>("/stats/summary");
}

export function triggerScan() {
  return request<{ ingested_count: number; duplicate_count: number; skipped_count: number; error_count: number }>(
    "/ingest/scan",
    { method: "POST", body: "{}" },
  );
}

export function getGameDashboard(window: GameWindow = "week", forestLimit = 140) {
  return request<GameDashboard>(`/game/dashboard?window=${window}&forest_limit=${forestLimit}`);
}

export function shredGameReceipt(receiptId: string) {
  return request<GameShredResponse>(`/game/receipts/${receiptId}/shred`, {
    method: "POST",
    body: "{}",
  });
}

export function rebuildGameState() {
  return request<GameRebuildResponse>("/game/rebuild", {
    method: "POST",
    body: "{}",
  });
}

export function reconcileGameState() {
  return request<GameReconcileResponse>("/game/reconcile", {
    method: "POST",
    body: "{}",
  });
}

export function recomputeCorrectnessState() {
  return request<GameCorrectnessRecomputeResponse>("/game/correctness/recompute", {
    method: "POST",
    body: "{}",
  });
}

export function spendGameWater(units: number) {
  return request<GameWaterSpendResponse>("/game/water/spend", {
    method: "POST",
    body: JSON.stringify({ units }),
  });
}

export function listGameIncidents(pendingOnly = true, limit = 30) {
  const params = new URLSearchParams();
  params.set("pending_only", pendingOnly ? "true" : "false");
  params.set("limit", String(limit));
  return request<GameIncident[]>(`/game/incidents?${params.toString()}`);
}

export function acknowledgeGameIncident(incidentId: number) {
  return request<GameIncident>(`/game/incidents/${incidentId}/ack`, {
    method: "POST",
    body: "{}",
  });
}

export function getGameDebugSeed() {
  return request<GameDebugSeed>("/game/debug-seed");
}

export function updateGameDebugSeed(payload: GameDebugSeedUpdateRequest) {
  return request<GameDebugSeed>("/game/debug-seed", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAppConfig() {
  return request<AppConfig>("/config");
}
