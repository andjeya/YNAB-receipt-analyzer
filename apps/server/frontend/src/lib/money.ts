/**
 * money.ts — Signed amount formatting and conversion helpers.
 *
 * Convention used throughout the app:
 *   - `display_total_milliunits` is stored as a POSITIVE magnitude (outflow=False).
 *   - The transaction_kind ("purchase" | "refund") determines the accounting sign.
 *   - On display: purchases show as −$N.NN (outflow), refunds as +$N.NN (inflow).
 */

export type TransactionKind = "purchase" | "refund";

/**
 * Returns a signed dollar value from a positive-magnitude dollar amount and kind.
 * - refund  → positive (inflow)
 * - purchase → negative (outflow)
 */
export function signedDollars(amount: number, kind: TransactionKind): number {
  const magnitude = Math.abs(amount);
  return kind === "refund" ? magnitude : -magnitude;
}

/**
 * Formats a signed dollar value.
 * - Zero → "$0.00" (no sign)
 * - Negative → "−$25.62" (U+2212 minus sign)
 * - Positive → "+$12.40"
 */
export function formatSignedDollars(dollars: number): string {
  if (dollars === 0 || Object.is(dollars, -0)) {
    return "$0.00";
  }
  const absolute = Math.abs(dollars).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (dollars < 0) {
    return `−$${absolute}`;
  }
  return `+$${absolute}`;
}

/**
 * Formats milliunits (may be null).
 * - null → "--"
 * - Preserves the stored sign (positive magnitude stored → caller determines sign context)
 *
 * Note: display_total_milliunits is stored positive. This function shows the raw magnitude
 * prefixed by "−" for typical outflow display contexts where we don't have transaction_kind.
 * When transaction_kind is available, use signedDollars + formatSignedDollars instead.
 */
export function formatSignedDollarsFromMilliunits(milliunits: number | null): string {
  if (milliunits == null) return "--";
  const dollars = milliunits / 1000;
  return formatSignedDollars(dollars);
}

/**
 * Formats a signed dollar value with a direction label appended.
 * - Positive (inflow/refund) → "+$12.40 (inflow)"
 * - Negative (outflow/purchase) → "−$25.62 (outflow)"
 * - Zero → "$0.00"
 */
export function formatSignedDollarsWithDirection(dollars: number, kind: TransactionKind): string {
  const base = formatSignedDollars(dollars);
  if (dollars === 0 || Object.is(dollars, -0)) return base;
  const label = kind === "refund" ? "(inflow)" : "(outflow)";
  return `${base} ${label}`;
}

/**
 * Formats a milliunit amount as a plain dollar magnitude (no sign prefix).
 * Use in contexts where the sign is already implied (e.g. duplicate comparison cards).
 * - null → "--"
 * - 25620 → "$25.62"
 * - 0 → "$0.00"
 */
export function formatDollarsMagnitude(milliunits: number | null): string {
  if (milliunits == null) return "--";
  const dollars = Math.abs(milliunits) / 1000;
  return `$${dollars.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * Converts a dollar amount to milliunits using ROUND_HALF_UP on the absolute value.
 * Throws if the input is negative (caller must pass absolute values).
 * The outflow flag determines the sign of the result (negative = outflow).
 *
 * Mirrors Python money.py exactly:
 *   Decimal(str(amount)).quantize(Decimal("0.001"), ROUND_HALF_UP) * 1000
 *
 * Uses string-based parsing to avoid float multiplication artifacts
 * (e.g. 0.5005 * 1000 = 500.4999... in IEEE 754 → wrong with Math.round).
 */
export function dollarsToMilliunits(amount: number, outflow: boolean): number {
  if (amount < 0) {
    throw new Error(`dollarsToMilliunits: negative input not allowed (got ${amount})`);
  }
  // Work from the decimal string representation to avoid float artifacts.
  // Split at the decimal point and operate on digit strings.
  const str = String(amount);
  const dotIndex = str.indexOf(".");
  const intPart = dotIndex === -1 ? str : str.slice(0, dotIndex);
  const fracPart = dotIndex === -1 ? "" : str.slice(dotIndex + 1);

  // Pad / truncate fracPart to 4 digits so we can inspect the rounding digit
  const d1 = Number(fracPart[0] ?? "0");
  const d2 = Number(fracPart[1] ?? "0");
  const d3 = Number(fracPart[2] ?? "0");
  const d4 = Number(fracPart[3] ?? "0"); // rounding digit for 3rd decimal place

  // Compute the integer milliunit count = intPart * 1000 + d1 * 100 + d2 * 10 + d3
  const rawMillis = Number(intPart) * 1000 + d1 * 100 + d2 * 10 + d3;

  // ROUND_HALF_UP: increment if 4th decimal digit >= 5
  const millis = d4 >= 5 ? rawMillis + 1 : rawMillis;

  return outflow ? -millis : millis;
}
