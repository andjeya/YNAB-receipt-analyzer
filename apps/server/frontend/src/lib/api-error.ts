/**
 * api-error.ts — Pure API error parsing helpers.
 * Extracted here so they can be imported by unit tests without pulling in
 * browser-only fetch/env globals from api.ts.
 */

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Extracts a human-readable message from a raw HTTP error response body.
 *
 * Handles:
 *   - Plain string detail: "some message"
 *   - Object with string detail: { detail: "some message" }
 *   - Object with code/message detail: { detail: { code: "x", message: "y" } }
 *   - FastAPI 422 validation list: { detail: [{ msg: "...", ... }, ...] }
 *   - Empty body: "Request failed: <status>"
 *   - Non-JSON: raw text as-is
 */
export function extractDetailMessage(rawBody: string, status: number): string {
  const trimmed = rawBody.trim();
  if (!trimmed) {
    return `Request failed: ${status}`;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    // Not JSON — return raw text
    return trimmed;
  }

  // String detail at top level
  if (typeof parsed === "string") {
    return parsed || `Request failed: ${status}`;
  }

  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const obj = parsed as Record<string, unknown>;
    const detail = obj["detail"];

    // Plain string detail
    if (typeof detail === "string") {
      return detail || `Request failed: ${status}`;
    }

    // { detail: { code, message } }
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const detailObj = detail as Record<string, unknown>;
      if (typeof detailObj["message"] === "string") {
        return detailObj["message"] || `Request failed: ${status}`;
      }
    }

    // FastAPI 422: { detail: [{ msg, ... }, ...] }
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((item) => {
          if (item && typeof item === "object") {
            const itemObj = item as Record<string, unknown>;
            return typeof itemObj["msg"] === "string" ? itemObj["msg"] : null;
          }
          return null;
        })
        .filter((m): m is string => m !== null && m.length > 0);
      if (msgs.length > 0) {
        return msgs.join("; ");
      }
    }
  }

  return `Request failed: ${status}`;
}
