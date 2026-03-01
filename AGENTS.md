# Agent Coordination Notes

This file applies to all coding agents used in this repository:

- Codex
- Claude
- GitHub Copilot

## Planning + Progress Logging

Agents must write structured markdown notes to the `plans/` folder while working.

Use this directory schema:

- `plans/[year]/[month]/[week]/`

Path token format is required:

- `year`: `YYYY` (example: `2026`)
- `month`: `MM` (example: `03`)
- `week`: `week-01` to `week-53` (ISO-aligned week numbering)

Example:

- `plans/2026/03/week-09/`

Within each week folder, create and update markdown files for:

- plan (what will be done)
- progress (what is being done / current status)
- report (what was completed, outcomes, blockers, handoff notes)
- session logs for in-progress agent work

Recommended naming:

- `plan.md`
- `progress.md`
- `report.md`
- `session-YYYY-MM-DD.md` (or `session-YYYY-MM-DD-HHMM.md` when multiple sessions happen in one day)

`session-*` files are the preferred place for active agent notes to avoid collisions between agents.

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

- the current week `report.md` (if present)
- the most recent `session-*` or `progress.md`

This is required to resume context and support cross-agent continuity.

## Update Cadence

Agents should update notes:

- after each meaningful implementation step
- when decisions or blockers change
- before ending a work session

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
