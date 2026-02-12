import {
  CacheEntity,
  ReceiptDetail,
  ReceiptSummary,
  SaveDraftResponse,
  StatsSummary,
  SyncEnqueueResponse,
  ValidationPayloadInput,
} from "@/lib/types";

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
    throw new Error(body || `Request failed: ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function receiptFileUrl(receiptId: string): string {
  return `${API_BASE}/receipts/${receiptId}/file`;
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

export function saveDraft(receiptId: string, payload: ValidationPayloadInput) {
  return request<SaveDraftResponse>(`/receipts/${receiptId}/draft`, {
    method: "POST",
    body: JSON.stringify({ payload, source: "user" }),
  });
}

export function enqueueSync(receiptId: string, options: { force_create?: boolean; allow_update_match?: boolean }) {
  return request<SyncEnqueueResponse>(`/receipts/${receiptId}/sync`, {
    method: "POST",
    body: JSON.stringify(options),
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

export function getStatsSummary() {
  return request<StatsSummary>("/stats/summary");
}

export function triggerScan() {
  return request<{ ingested_count: number; duplicate_count: number; skipped_count: number; error_count: number }>(
    "/ingest/scan",
    { method: "POST", body: "{}" },
  );
}
