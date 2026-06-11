import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration.
 *
 * All /api/* calls are intercepted at the browser layer by page.route() in
 * each test, so the Next.js rewrite target (INTERNAL_API_ORIGIN) is pointed
 * at a TCP black hole (port 9 — the discard service).  Any request that leaks
 * through the mock layer will fail loudly rather than silently hitting the
 * real backend on :8000.
 *
 * WORKING INVOCATION (local, recommended):
 *   # 1. Start the test dev server once (port 3001, isolated from main :3000):
 *   rm -rf .next
 *   INTERNAL_API_ORIGIN=http://127.0.0.1:9 npm run dev -- --port 3001 &
 *   # Wait for it to be ready, then:
 *   npx playwright test
 *
 * IMPORTANT: After `npm run build`, the .next directory is replaced with a
 * production build, which breaks the dev server.  Always restart the dev
 * server (rm -rf .next + npm run dev) after running a production build.
 *
 * The webServer command below uses a port-check to avoid EADDRINUSE when the
 * server is already running.  reuseExistingServer=true is set so Playwright
 * will use an already-running server at :3001.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  retries: 0,
  reporter: "list",

  use: {
    baseURL: "http://localhost:3001",
    headless: true,
    viewport: { width: 1280, height: 900 },
    // Fail fast on any uncaught browser error
    actionTimeout: 10_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    /**
     * `next dev` is the most reliable for CI-less local runs — no build step
     * required and it picks up source changes immediately.
     *
     * Port 3001 is used to isolate from the main dev/prod server on :3000.
     *
     * INTERNAL_API_ORIGIN is pointed at port 9 (OS-level discard / black hole)
     * so any unmocked /api/* request that reaches the Next.js rewrite layer
     * will receive an immediate connection refused rather than silently
     * reaching the real backend on :8000.
     *
     * The command is wrapped in a port-check: if :3001 is already serving, we
     * skip starting a new instance (reuseExistingServer=true with a command
     * that exits 0 immediately if the server is already available).
     */
    command: "curl -sf http://localhost:3001 > /dev/null 2>&1 || INTERNAL_API_ORIGIN=http://127.0.0.1:9 npm run dev -- --port 3001",
    url: "http://localhost:3001",
    reuseExistingServer: true,
    timeout: 90_000,
    env: {
      INTERNAL_API_ORIGIN: "http://127.0.0.1:9",
      NODE_ENV: "test",
    },
  },
});
