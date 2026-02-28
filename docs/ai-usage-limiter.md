# AI Usage Limiter Architecture

## Goal

All LLM requests are routed through a single application-level gateway that performs:

- model/provider resolution from a registry
- preflight usage/cost estimation
- hard/soft limit enforcement across time windows
- durable usage logging
- post-call reconciliation using actual provider token usage when available

## Decision: model selection ownership

This project keeps **model selection at call sites** (Option A).

- Existing architecture already chooses model via `settings.gemini_model`.
- The gateway enforces that selected model IDs must exist in the registry.
- Unknown models fail fast with a helpful error.

This minimizes invasive changes while still guaranteeing requests pass through the limiter.

## Components

### 1) Gateway client (`shared/receipt_shared/ai/client.py`)

- Public interface:
  - `AIClient.generate_text(request: AIRequest)`
  - `AIClient.generate_structured(request: AIRequest, schema=...)`
- Responsibilities:
  - loads model registry and limits config
  - estimates usage/cost preflight
  - reserves budget atomically in ledger
  - invokes provider adapter
  - finalizes ledger record with actual usage/cost

### 2) Provider adapters (`shared/receipt_shared/ai/providers/`)

- Current provider: Google Gemini.
- Extracts provider usage metadata when available.
- Falls back to best-effort estimation when usage is missing.
- Provider adapters are pluggable by provider key from registry.

### 3) Model registry (`shared/receipt_shared/resources/ai_model_registry.v1.json`)

- Versioned schema.
- Defines model IDs, provider, provider model mapping, and pricing dimensions.
- Supports extending billable dimensions beyond input/output tokens.

### 4) Limits config (`config/ai_limits.v1.json`)

- Separate from registry.
- Supports global and per-model caps.
- Windows: `hourly`, `daily`, `weekly`, `monthly`.
- Dimensions: `tokens` and `usd`.
- Unlimited support via `unlimited: true` and/or null caps.

### 5) Durable ledger (`ai_usage_ledger` table in `AI_USAGE_DB_URL`)

Stored per request:

- UTC timestamp
- provider + model
- request/correlation IDs
- route/purpose
- token usage (input/output/cached/total)
- computed cost
- status (`pending`, `success`, `rejected_by_limit`, `provider_error`)
- metadata (no raw prompt/receipt content)

## Concurrency and correctness

- Reservation uses a write-lock transaction (`BEGIN IMMEDIATE` for SQLite) to make limit checks + reservation atomic.
- Limits are checked against current window totals including in-flight `pending` reservations.
- Requests are rejected if **any** applicable cap is exceeded (global or model; tokens or USD; any window).
- On provider completion, usage and cost are reconciled to actual values.

## Integration points

- `GeminiAnalyzer.analyze_file(...)` now delegates to `AIClient`.
- Background extraction jobs pass route/receipt metadata and limit behavior.
- Existing business logic remains unchanged except gateway wiring.

## Configuration

Environment variables:

- `AI_MODEL_REGISTRY_PATH` (default `./shared/receipt_shared/resources/ai_model_registry.v1.json`)
- `AI_LIMITS_CONFIG_PATH` (default `./config/ai_limits.v1.json`)
- `AI_USAGE_DB_URL` (default `sqlite:///./data/ai_usage.db`)
- `AI_LIMIT_BEHAVIOR` (`hard_fail` or `soft_fail`, default `hard_fail`)

## Operator TUI

Run:

```bash
python -m tools.ai_limits
```

Provides:

- overview of limits + current usage by window/model
- edit panel for updating caps (atomic file write)
- analytics view (daily breakdown + daily/weekly/monthly avg/max stats)

Library choice: `textual` was selected because it is a mature Python TUI framework with built-in key navigation, panel/tab layouts, and scrollable data tables, which keeps the operator UI simple while still interactive.

## Sensitive data policy

- Usage ledger does **not** store raw prompts, receipt text, or file contents.
- Only operational metadata and usage/accounting values are persisted.
