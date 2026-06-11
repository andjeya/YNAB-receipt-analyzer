import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { extractDetailMessage, ApiError } from "./api-error.js";

// ---------------------------------------------------------------------------
// ApiError
// ---------------------------------------------------------------------------
describe("ApiError", () => {
  it("is an Error subclass", () => {
    const err = new ApiError("oops", 400);
    assert.ok(err instanceof Error);
    assert.ok(err instanceof ApiError);
  });

  it("stores status", () => {
    const err = new ApiError("not found", 404);
    assert.strictEqual(err.status, 404);
    assert.strictEqual(err.message, "not found");
  });

  it("has name ApiError", () => {
    assert.strictEqual(new ApiError("x", 500).name, "ApiError");
  });
});

// ---------------------------------------------------------------------------
// extractDetailMessage
// ---------------------------------------------------------------------------
describe("extractDetailMessage", () => {
  it("empty body → Request failed: <status>", () => {
    assert.strictEqual(extractDetailMessage("", 500), "Request failed: 500");
    assert.strictEqual(extractDetailMessage("   ", 503), "Request failed: 503");
  });

  it("plain string detail in JSON object", () => {
    assert.strictEqual(
      extractDetailMessage(JSON.stringify({ detail: "Receipt not found" }), 404),
      "Receipt not found",
    );
  });

  it("{ detail: { code, message } } → message", () => {
    const body = JSON.stringify({ detail: { code: "ynab_sync_disabled", message: "Sync is disabled" } });
    assert.strictEqual(extractDetailMessage(body, 409), "Sync is disabled");
  });

  it("FastAPI 422 list → joined msgs", () => {
    const body = JSON.stringify({
      detail: [
        { msg: "field required", type: "missing", loc: ["body", "payee_name"] },
        { msg: "value is not a valid integer", type: "type_error.integer" },
      ],
    });
    assert.strictEqual(extractDetailMessage(body, 422), "field required; value is not a valid integer");
  });

  it("non-JSON → raw text", () => {
    assert.strictEqual(extractDetailMessage("Internal Server Error", 500), "Internal Server Error");
  });

  it("plain string JSON at top level", () => {
    assert.strictEqual(extractDetailMessage(JSON.stringify("bad input"), 400), "bad input");
  });

  it("empty detail string → fallback", () => {
    assert.strictEqual(extractDetailMessage(JSON.stringify({ detail: "" }), 400), "Request failed: 400");
  });

  it("422 list with no msg fields → fallback", () => {
    const body = JSON.stringify({ detail: [{ loc: ["body"] }] });
    assert.strictEqual(extractDetailMessage(body, 422), "Request failed: 422");
  });

  it("missing detail key → fallback", () => {
    assert.strictEqual(extractDetailMessage(JSON.stringify({ other: "x" }), 500), "Request failed: 500");
  });
});
