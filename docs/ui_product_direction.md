# UI / Product Direction

**North star: "Duolingo-like delight for motivation, bank-like clarity for
confirmation."** Make the routine of clearing receipts feel rewarding, but make
the moment money leaves the machine feel precise, sober, and trustworthy.

## Two-register design principle
The app speaks in two registers and never mixes them:
- **Delight register** (Queue, Done, progress, streaks): playful, animated, encouraging.
- **Bank register** (Confirm/preview, anything showing the signed payload): plain, dense, exact, no animation, no celebration. Numbers and signs are shown verbatim.

## Screen map
- **Queue** — receipts waiting for review; progress + streak live here (delight register).
- **Review** — per-receipt extraction + line-item allocation, with **progressive disclosure** (raw extraction and advanced controls revealed on demand, not dumped at once).
- **Confirm** — pre-sync preview screen: full **signed** payload (amount + sign, account, category splits, duplicate status, dry-run/live **mode badge**) and an explicit confirm. Bank register only.
- **Done** — completion celebration (delight register).

## Gamification ethics
- Never pressure the user toward approval — gamification rewards **completion and care**, never speed or volume of synced transactions.
- Celebrate **completion, not speed**; no streak/score mechanic may nudge skipping review.
- Game state must never appear on the Confirm screen.

## Animation rules
- Animations ≤ **300ms** and **non-blocking** (never gate an action behind an animation).
- Respect `prefers-reduced-motion` (provide a static path).
- **No animation on the Confirm screen.**

## Accessibility bar
- Modals use proper **dialog semantics** (`role="dialog"`, labelled) with **focus traps** and restore-focus on close.
- Drag-and-drop allocation has a full **keyboard** path (keyboard sensor / equivalent).
- Color **contrast** meets WCAG AA; flag/status meaning never conveyed by color alone.

## UI gates (must hold before UI tasks are "done")
- Signed amounts are visible to the user (no `Math.abs` hiding signs in `formatAmount` helpers).
- A sync **confirm/preview gate** exists; the external write cannot happen without it.
- A **toast/error layer** exists; sync/autosave failures surface `onError` (no silent mutation failures).
- Read-mode shows **mismatch warnings** (duplicate, amount drift, staleness).
- Dialogs pass the a11y bar above; reduced-motion honored; Confirm screen has no animation.
