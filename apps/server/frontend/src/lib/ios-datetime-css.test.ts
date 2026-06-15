import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Regression guard for the iOS/WebKit date & time picker overflow on the receipt
// detail page (the editable transaction date/time in receipt-twin-viewer.tsx).
//
// Root cause: <input type="date"> / <input type="time"> render a native
// ::-webkit-date-and-time-value pseudo with an intrinsic min-width that ignores
// width:100%, so the value spills past the right edge on iOS Safari. The fix
// lives in globals.css; it was lost once already in a CSS refactor and the bug
// came back. This test asserts the rules are present so they can't silently
// disappear again. (The bug isn't reproducible in headless Chromium, so a CSS
// assertion is a more reliable guard than an e2e layout check.)

const css = readFileSync(fileURLToPath(new URL("../app/globals.css", import.meta.url)), "utf8");

function ruleBlock(selectorStart: string): string {
  const idx = css.indexOf(selectorStart);
  assert.notStrictEqual(idx, -1, `expected globals.css to contain a rule for: ${selectorStart}`);
  return css.slice(idx, css.indexOf("}", idx));
}

test("date & time inputs reset native appearance and can shrink", () => {
  const block = ruleBlock('input[type="date"],');
  assert.match(block, /input\[type="time"\]/, "rule covers time inputs too");
  assert.match(block, /appearance:\s*none/, "native appearance is reset");
  assert.match(block, /min-width:\s*0/, "input can shrink below intrinsic width");
});

test("::-webkit-date-and-time-value pseudo has min-width:0 (the actual overflow fix)", () => {
  const block = ruleBlock('input[type="date"]::-webkit-date-and-time-value');
  assert.match(block, /min-width:\s*0/, "value pseudo min-width zeroed");
  assert.match(block, /text-align:\s*left/, "value pseudo left-aligned");
});

test("calendar picker indicator is pinned to the right", () => {
  const block = ruleBlock("::-webkit-calendar-picker-indicator");
  assert.match(block, /margin-left:\s*auto/);
});
