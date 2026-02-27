# Receipt Digital Twin v2 Implementation Progress

Plan source: `plans/receipt-digital-twin-2026-02-27-v2.md`
Reference: `plans/receipt-digital-twin-2026-02-27.md`

## Status Legend
- `TODO`
- `IN_PROGRESS`
- `DONE`

## Phase Tracking

### Phase 1: Backend Foundation (Data model, schemas, migration, config)
Status: `DONE`

Tasks:
- [x] Add twin + extraction attempt metadata to SQLAlchemy models
- [x] Add API/Pydantic schema fields for twin and extraction metadata
- [x] Add config flags for twin extraction and reconciliation thresholds
- [x] Add Alembic migration `0005_receipt_twins_and_extraction_attempt_metadata.py`
- [x] Validate migration imports/startup compatibility

### Phase 2: Extraction Flow + Gemini Contracts
Status: `IN_PROGRESS`

Tasks:
- [ ] Add `ReceiptLineItem`, `UnifiedReceiptExtraction`, `ReceiptTwinExtraction`
- [ ] Add unified/twin prompt builders and schema-aware Gemini analyzer
- [ ] Add tiered validation (YNAB-critical vs twin-quality)
- [ ] Add deterministic fallback A/B and disagreement metadata
- [ ] Persist twin versions from model output when available

### Phase 3: API + Lock Enforcement
Status: `TODO`

Tasks:
- [ ] Add twin endpoints (`GET/PUT/POST confirm`)
- [ ] Add twin base_version concurrency handling and no-op save semantics
- [ ] Enforce lock behavior in `POST /receipts/{id}/draft`
- [ ] Ensure `GET /receipts/{id}` returns primary extraction + lock metadata + latest twin
- [ ] Add degraded twin-unavailable behavior

### Phase 4: Backend Tests
Status: `TODO`

Tasks:
- [ ] Unified success + primary run selection
- [ ] Unified YNAB-critical fail fallback behavior
- [ ] Draft lock enforcement
- [ ] Twin save concurrency (409 on stale)
- [ ] Confirm idempotency and degraded mode

### Phase 5: Frontend Twin UX
Status: `TODO`

Tasks:
- [ ] Add twin types and API client methods
- [ ] Add `receipt-twin-viewer.tsx`
- [ ] Update receipt detail to dual-view layout and lock-aware draft fields
- [ ] Add twin edit mode + save/cancel + warnings
- [ ] Add fallback/degraded panel

### Phase 6: Frontend Tests + Verification
Status: `TODO`

Tasks:
- [ ] Add/update frontend unit tests for twin mapping and lock behavior
- [ ] Run backend tests
- [ ] Run frontend tests/lint/build checks
- [ ] Final docs/progress update with commit SHAs

## Checkpoint Commits
- Pending

## Execution Notes
- 2026-02-27: Added backend foundation for twins/attempt metadata and verified Alembic upgrade on a fresh SQLite database (`data/migration_test.db`).
