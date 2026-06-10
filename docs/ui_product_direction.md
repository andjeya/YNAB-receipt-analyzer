# UI / Product Direction

**North star: "Duolingo-like delight for motivation, bank-like clarity for
confirmation."** Make the routine of clearing receipts feel rewarding, but make
the moment money leaves the machine feel precise, sober, and trustworthy.

## Audience & design brief (user-stated, 2026-06-10)

The primary user is the owner's wife, who loves Duolingo. The Duolingo
inspiration specifically means **cute characters, icons, and animations tightly
tied to the incentives being driven** — not points dashboards. Our incentives:
**accuracy** (no category/amount mistakes), **consistency** (regular validation
streaks), **timeliness** (clear receipts without delay). The experience must be
seamless and low-effort: the system does the heavy lifting, the human reviews,
and the UI **draws the eye when something might be wrong** (category mismatch,
total drift, near-duplicate, staleness).

Implications (gap analysis vs current UI, from 2026-06-10 live review):
- **Mascot:** one character ("Snappy"), ~5 SVG poses (idle/happy/celebrating/
  concerned/asleep), CSS-transform animation only. Greeting on Queue,
  celebration on empty-queue + sync-success, concerned-pointing as the
  universal "needs your eyes" marker. Never on the Confirm screen.
- **Retire jargon from primary UI:** every game stat gets a plain microcopy
  phrase ("Validate 3 in a row → earn a shredder pass"); tooltips are backup,
  not the primary explanation.
- **Event-driven micro-celebrations mapped 1:1 to incentives:** fast validation
  → tile sprouts (timeliness); clean week with no YNAB-side corrections →
  streak garland (accuracy); streak milestones → mascot reacts (consistency).
  Celebrations fire on *verified completion* (synced), never on button press.
- **One attention grammar:** a single amber "needs your eyes" treatment for
  ambiguity/mismatch/duplicate/staleness, aggregated in a status strip directly
  above the primary action (not scattered across the page).
- **Allocation board feel:** lane dollar totals that count up on drop, visible
  pin badges, subtle confidence shading on auto-assigned items.
- **Queue emotional arc:** empty queue is the best screen ("forest thriving");
  in-flight receipts show friendly progress ("Reading your Costco receipt…"),
  never raw filenames.

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
