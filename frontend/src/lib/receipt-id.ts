const RECEIPT_ID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i;

export function extractReceiptIdFromText(rawValue: string): string | null {
  const match = rawValue.match(RECEIPT_ID_PATTERN);
  return match ? match[0].toLowerCase() : null;
}
