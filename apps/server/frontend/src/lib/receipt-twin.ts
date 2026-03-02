import { ReceiptLineItem, ReceiptTwinPayload } from "@/lib/types";

function normalizeLineItems(value: unknown): ReceiptLineItem[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter((candidate): candidate is Record<string, unknown> => Boolean(candidate) && typeof candidate === "object")
    .map((item, index) => {
      const indexValue = Number(item.index);
      const quantityValue = Number(item.quantity);
      const unitPriceValue = Number(item.unit_price);
      const lineTotalValue = Number(item.line_total);
      const rawTaxCode = item.tax_code;
      const itemType = String(item.item_type ?? "").trim();
      const taxCode = typeof rawTaxCode === "string" && rawTaxCode.trim() ? rawTaxCode : null;

      return {
        index: Number.isFinite(indexValue) ? indexValue : index,
        raw_text: String(item.raw_text ?? ""),
        translated_text: String(item.translated_text ?? ""),
        quantity: Number.isFinite(quantityValue) ? quantityValue : null,
        unit_price: Number.isFinite(unitPriceValue) ? unitPriceValue : null,
        line_total: Number.isFinite(lineTotalValue) ? lineTotalValue : null,
        tax_code: taxCode,
        item_type: itemType || "product",
      };
    });
}

export function cloneTwinPayload(payload: ReceiptTwinPayload): ReceiptTwinPayload {
  return {
    ...payload,
    line_items: normalizeLineItems(payload.line_items),
  };
}

export function normalizeTwinTimeForInput(value: string | null): string {
  if (!value) return "";
  return value.slice(0, 5);
}

function toNumberOrNull(value: number | null | undefined): number | null {
  if (value == null) return null;
  if (!Number.isFinite(value)) return null;
  return value;
}

export function computeTwinEditWarnings(payload: ReceiptTwinPayload): string[] {
  const warnings: string[] = [];

  for (const item of payload.line_items) {
    const quantity = toNumberOrNull(item.quantity);
    const unitPrice = toNumberOrNull(item.unit_price);
    const lineTotal = toNumberOrNull(item.line_total);

    if (quantity == null || unitPrice == null || lineTotal == null) {
      continue;
    }

    const expected = quantity * unitPrice;
    if (Math.abs(expected - lineTotal) > 0.01) {
      warnings.push(
        `Line ${item.index + 1}: line total ${lineTotal.toFixed(2)} differs from quantity × unit price ${expected.toFixed(2)}.`,
      );
    }
  }

  let additiveTotal = 0;
  let additiveCount = 0;
  for (const item of payload.line_items) {
    if (item.line_total == null || !Number.isFinite(item.line_total)) {
      continue;
    }

    const kind = (item.item_type || "product").toLowerCase();
    if (kind === "subtotal" || kind === "total") {
      continue;
    }

    const amount = Math.abs(item.line_total);
    if (kind === "discount") {
      additiveTotal -= amount;
    } else if (kind === "product" || kind === "fee" || kind === "tax") {
      additiveTotal += amount;
    } else {
      additiveTotal += item.line_total;
    }
    additiveCount += 1;
  }

  if (additiveCount > 0 && Number.isFinite(payload.total_amount)) {
    const delta = Math.abs(additiveTotal - payload.total_amount);
    if (delta > 0.05) {
      warnings.push(
        `Line-item sum ${additiveTotal.toFixed(2)} differs from total ${payload.total_amount.toFixed(2)} by ${delta.toFixed(2)}.`,
      );
    }
  }

  return warnings;
}
