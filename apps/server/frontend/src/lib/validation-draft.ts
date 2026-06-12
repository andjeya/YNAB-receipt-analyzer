/**
 * validation-draft.ts
 *
 * Converts a stored validation payload (raw JSON from the API) into the
 * editable ValidationPayloadInput draft shape. Shared by the receipt detail
 * page and the quick-sync preview on the receipt list.
 */

import type { ValidationPayloadInput } from "./types.js";

export function toDraftFromPayload(payload: Record<string, unknown>, fallbackPayee: string): ValidationPayloadInput {
  const splitsSource = Array.isArray(payload.splits) ? payload.splits : [];
  const parsedSplits = splitsSource.flatMap((split) => {
    if (!split || typeof split !== "object") {
      return [];
    }
    const record = split as Record<string, unknown>;
    const amount = Number(record.amount ?? 0);
    return {
      category_id: String(record.category_id ?? ""),
      amount: Number.isFinite(amount) ? amount : 0,
      memo: String(record.memo ?? ""),
    };
  });

  let categoryId = String(payload.category_id ?? "");
  const splits = parsedSplits;

  if (splits.length > 0) {
    categoryId = "";
  }

  const transactionTimeRaw = String(payload.transaction_time ?? "").trim();
  const transactionTime = transactionTimeRaw ? transactionTimeRaw.slice(0, 5) : "";

  return {
    payee_name: String(payload.payee_name ?? fallbackPayee ?? ""),
    account_id: String(payload.account_id ?? ""),
    transaction_date: String(payload.transaction_date ?? ""),
    transaction_time: transactionTime,
    memo: String(payload.memo ?? ""),
    total_amount: Number(payload.total_amount ?? 0),
    transaction_kind: payload.transaction_kind === "refund" ? "refund" : "purchase",
    category_id: categoryId,
    splits,
    // Provenance survives saves so the "remembered from card" hint stays
    // truthful across validation versions; cleared on manual account change.
    ...(payload.account_source === "card_mapping" ? { account_source: "card_mapping" } : {}),
    // Provenance survives saves so the "remembered from store" hint stays
    // truthful across validation versions; cleared on any manual category/split change.
    ...(payload.category_source === "payee_memory" ? { category_source: "payee_memory" } : {}),
    // Date guess provenance: drives the orange "confirm the date" bubble and the
    // sync gate. Cleared when the user confirms or edits the date.
    ...(payload.date_source === "ai_guess"
      ? {
          date_source: "ai_guess",
          date_confidence: payload.date_confidence ? String(payload.date_confidence) : undefined,
          date_note: payload.date_note ? String(payload.date_note) : undefined,
        }
      : {}),
  };
}
