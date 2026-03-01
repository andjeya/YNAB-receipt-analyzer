# Agent Coordination Notes

This file applies to all coding agents used in this repository:

- Codex
- Claude
- GitHub Copilot

## Planning + Progress Logging

Agents must write structured markdown notes to the `plans/` folder while working.

Use this directory schema:

- `plans/[year]/[month]/month-summary.md`
- `plans/[year]/[month]/[week]/`

Path token format is required:

- `year`: `YYYY` (example: `2026`)
- `month`: `MM` (example: `03`)
- `week`: `week-01` to `week-53` (ISO-aligned week numbering)

Examples:

- `plans/2026/03/month-summary.md`
- `plans/2026/03/week-09/`

Within each week folder, create and update markdown files for:

- plan (what will be done)
- progress (what is being done / current status)
- report (what was completed, outcomes, blockers, handoff notes)
- session logs for in-progress agent work

## File Naming Convention (Collision-Resistant)

Use this required filename format for new files:

- `[type]-YYYY-MM-DD-HHMM-[agent]-[topic].md`

Tokens:

- `type`: `plan`, `progress`, `report`, or `session`
- `YYYY-MM-DD-HHMM`: local timestamp at creation time
- `agent`: lowercase agent id, for example `codex`, `claude`, `copilot`
- `topic`: 3-8 word kebab-case conversation label

Examples:

- `plan-2026-03-01-2045-codex-plan-4-1-refactor.md`
- `session-2026-03-01-2105-claude-duplicate-detection-review.md`

`session-*` files remain the preferred place for active notes.

Legacy files such as `plan.md`, `progress.md`, or `report.md` may exist; do not rename old artifacts unless explicitly requested.

## Required Markdown Structure

At minimum, each plan/progress/report/session file should use these headings when applicable:

- `## Context`
- `## Plan`
- `## Progress`
- `## Decisions`
- `## Blockers`
- `## Next Steps`
- `## Handoff`

Use `TBD` when a section is not yet populated.

## Read-First Rule

Before starting work, agents should read:

- current month `month-summary.md` (if present)
- the current week's latest `report-*` file (or `report.md` if legacy naming is used)
- the most recent `session-*` or `progress-*` file (or legacy `progress.md`)

This is required to resume context and support cross-agent continuity.

For rapid historical triage, agents may use lower-reasoning subagents to scan older plan/progress/report/session files and return concise references plus key decisions. Final implementation decisions remain with the primary agent.

## Monthly Master Summary

Each month should maintain a continuously updated, brief summary file at:

- `plans/YYYY/MM/month-summary.md`

Purpose:

- avoid re-reading many individual notes
- preserve context window by keeping summaries short
- point to source files for details

Entry guidance:

- one short bullet per meaningful update
- include date/time, 1-2 line summary, and explicit file references
- prefer reference format: `refs: plan-..., session-..., report-...`

## Update Cadence

Agents should update notes:

- after each meaningful implementation step
- when decisions or blockers change
- before ending a work session
- when a milestone should be reflected in `month-summary.md`

## Commit Policy

`plans/` files are local working artifacts and must not be committed.

Git guardrails:

- never stage files under `plans/`
- if a `plans/` file is accidentally staged, unstage it before commit
- do not include `plans/` content in pull requests

Agents may read existing plan/progress/report files to:

- summarize previous work
- resume interrupted work
- hand off context between agents

## Archive Policy

At week close, move completed week folders to:

- `plans/archive/YYYY/MM/week-XX/`

Do not auto-archive active weeks.
