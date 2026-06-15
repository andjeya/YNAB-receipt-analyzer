"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Droplets,
  Flame,
  HelpCircle,
  Scissors,
  Receipt,
  Search,
  Sparkles,
  Trash2,
  Zap,
  Wrench,
} from "lucide-react";

import {
  acknowledgeGameIncident,
  deleteReceipt,
  enqueueSync,
  fetchYnabUpdates,
  getAppConfig,
  getGameDebugSeed,
  getGameDashboard,
  getGameSettings,
  getReceiptDetail,
  getYnabCache,
  listGameIncidents,
  listReceipts,
  rebuildGameState,
  recomputeCorrectnessState,
  restoreReceipt,
  shredGameReceipt,
  spendGameWater,
  triggerScan,
  updateGameDebugSeed,
  updateGameSettings,
} from "@/lib/api";
import { GameForestTile, GameIncident, GameWeeklySlot } from "@/lib/types";
import { cn } from "@/lib/utils";
import { formatSignedDollars, signedDollars } from "@/lib/money";
import { deriveSnappyPose, isStreakMilestone } from "@/lib/snappy-pose";
import { useToast } from "@/components/ui/toast";
import { extractReceiptIdFromText } from "@/lib/receipt-id";
import { isProcessingStatus, partitionReceipts, type ReceiptBucket } from "@/lib/receipt-buckets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { StatusBadge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ReceiptStateIcon, type ReceiptIconState } from "@/components/receipt-state-icon";
import { Snappy } from "@/components/snappy/snappy";
import { CardMappingPanel } from "@/components/card-mapping-panel";
import { SyncPreviewDialog } from "@/components/sync-preview-dialog";
import { toDraftFromPayload } from "@/lib/validation-draft";
import { formatWeekRange, formatWeekStart, parseApiDate } from "@/lib/dates";
import { notifyDevtoolsChange } from "@/components/providers";

// ---------------------------------------------------------------------------
// Query-devtools toggle helpers (mirrors providers.tsx; read here so DebugPanel
// can show the current state and toggle it live without a reload).
// ---------------------------------------------------------------------------

const DEVTOOLS_KEY = "snappy_query_devtools";

function readDevtoolsPref(): boolean {
  try { return localStorage.getItem(DEVTOOLS_KEY) === "1"; } catch { return false; }
}

// Shared subscriber set with providers.tsx (same in-memory module singleton).
// We re-use notifyDevtoolsChange() to fire both sides.
const devtoolsPrefSubscribers = new Set<() => void>();
export function subscribeDevtoolsPref(cb: () => void): () => void {
  devtoolsPrefSubscribers.add(cb);
  return () => { devtoolsPrefSubscribers.delete(cb); };
}

function setDevtoolsPref(enabled: boolean): void {
  try { localStorage.setItem(DEVTOOLS_KEY, enabled ? "1" : "0"); } catch { /* ignore */ }
  // Notify the providers.tsx gate (DevtoolsGate) and this panel's useSyncExternalStore.
  notifyDevtoolsChange();
  for (const cb of devtoolsPrefSubscribers) cb();
}

// Tab definitions — each maps to a ReceiptBucket (see src/lib/receipt-buckets.ts).
// Processing receipts live at the bottom of To Review with a "working" label,
// so there are only two places to look: what needs you, and what's done.
const TABS: Array<{ label: string; bucket: ReceiptBucket; testid: string }> = [
  { label: "Needs review", bucket: "review", testid: "tab-review" },
  { label: "Synced",       bucket: "done",   testid: "tab-done"   },
];

// review_hint → short plain-language line shown on the card. Codes are produced
// by _batch_review_state in apps/server/backend/app/api/receipts.py; keep the
// two lists in sync. Tone drives the chip colour (and matches StatusBadge).
const REVIEW_HINT: Record<string, { label: string; tone: "amber" | "emerald" | "orange" | "red" }> = {
  ready:          { label: "Ready to sync",         tone: "emerald" },
  needs_account:  { label: "Needs an account",      tone: "amber" },
  category_issue: { label: "Category needs a look", tone: "amber" },
  confirm_date:   { label: "Confirm the date",      tone: "amber" },
  confirm_total:  { label: "Confirm the total",     tone: "amber" },
  duplicate:      { label: "Possible duplicate",    tone: "orange" },
  review:         { label: "Needs review",          tone: "amber" },
  import_failed:  { label: "Couldn't read it",      tone: "red" },
  sync_failed:    { label: "Sync hiccup",           tone: "red" },
};

const HINT_TONE: Record<string, { chip: string; dot: string }> = {
  amber:   { chip: "bg-amber-100 text-amber-800 ring-amber-600/20",     dot: "bg-amber-500" },
  emerald: { chip: "bg-emerald-100 text-emerald-800 ring-emerald-600/20", dot: "bg-emerald-500" },
  orange:  { chip: "bg-orange-100 text-orange-800 ring-orange-600/20",  dot: "bg-orange-500" },
  red:     { chip: "bg-red-100 text-red-700 ring-red-600/20",           dot: "bg-red-500" },
};

/**
 * ReviewHintBadge — the specific "why this needs you" chip on a list card
 * (e.g. "Needs an account", "Confirm the date", "Ready to sync"). It replaces
 * the generic status badge for actionable receipts so the reason is explicit,
 * and resolves the "Needs review vs Quick sync" contradiction: a sync-ready
 * card reads "Ready to sync" next to its green button. Falls back to the raw
 * status badge for codes it doesn't recognise.
 */
function ReviewHintBadge({ hint }: { hint: string }) {
  const entry = REVIEW_HINT[hint];
  if (!entry) return <StatusBadge status={hint} />;
  const tone = HINT_TONE[entry.tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset",
        tone.chip,
      )}
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", tone.dot)} aria-hidden="true" />
      {entry.label}
    </span>
  );
}

// Whole, friendly units — "7 days", not "7.4d" (dev shorthand reads as jargon).
function formatWaitTime(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1) {
    const minutes = Math.max(Math.round(value * 60), 1);
    return `${minutes} min`;
  }
  if (value < 24) {
    const hours = Math.round(value);
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  if (value > 24 * 30) {
    const months = Math.round(value / 24 / 30);
    return `~${months} month${months === 1 ? "" : "s"}`;
  }
  const days = Math.round(value / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

/**
 * Formats a receipt's purchase date as a calm, grey-text label ("Jun 12", or
 * "Jun 12, 2025" when it isn't the current year). Prefers the date printed on
 * the receipt; falls back to when it was ingested if that's missing.
 */
function formatReceiptDate(displayReceiptDate: string | null | undefined, ingestedAt: string): string {
  const source = displayReceiptDate ?? ingestedAt;
  const date = parseApiDate(source);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

function deriveIconState(tile: GameForestTile | undefined): ReceiptIconState | null {
  const ds = tile?.display_state;
  if (ds === "green" || ds === "yellow" || ds === "brown" || ds === "shredded" || ds === "burnt") {
    return ds;
  }
  return null;
}

function isWithinSlot(isoTimestamp: string, slotStart: string, slotEnd: string): boolean {
  const ts = parseApiDate(isoTimestamp).getTime();
  return ts >= parseApiDate(slotStart).getTime() && ts < parseApiDate(slotEnd).getTime();
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

// A receipt is shreddable if it was validated within `windowWeeks` trailing
// weeks (including the current one). windowWeeks=1 is current-week-only; the
// effective value comes from the backend (debug-panel adjustable).
function isWithinShredWindow(
  isoTimestamp: string,
  currentWeekStart: string,
  currentWeekEnd: string,
  windowWeeks: number,
): boolean {
  const ts = parseApiDate(isoTimestamp).getTime();
  const windowStart = parseApiDate(currentWeekStart).getTime() - Math.max(windowWeeks - 1, 0) * WEEK_MS;
  return ts >= windowStart && ts < parseApiDate(currentWeekEnd).getTime();
}

function severityClass(incident: GameIncident): string {
  if (incident.severity === "critical") return "border-red-500 bg-red-50";
  if (incident.severity === "warning") return "border-amber-400 bg-amber-50";
  return "border-sky-300 bg-sky-50";
}

function toInt(value: unknown): number {
  const next = Number(value);
  return Number.isFinite(next) ? next : 0;
}

type DebugSeedForm = {
  enabled: boolean;
  water_units: number;
  current_week_flames: number;
  token_balance: number;
  token_earned_count: number;
  token_spent_count: number;
};

/**
 * ReceiptLookup — the only always-visible utility control (a search icon in
 * the tab row). Opens a small popover to jump to a receipt by pasting its ID
 * or the memo line copied from YNAB. Everything maintenance-flavored lives in
 * the debug panel instead.
 */
function ReceiptLookup({ onNavigate }: { onNavigate: (path: string) => void }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const openReceipt = () => {
    const parsedId = extractReceiptIdFromText(value.trim());
    if (!parsedId) {
      setError("Hmm, that doesn't look like a receipt ID. Paste the whole memo line from YNAB and I'll find it.");
      return;
    }
    setError(null);
    setOpen(false);
    setValue("");
    onNavigate(`/receipts/${parsedId}`);
  };

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <button
        type="button"
        data-testid="receipt-lookup-toggle"
        className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-full px-2.5 text-ink/60 transition hover:bg-ink/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label="Find a receipt"
        title="Find a receipt"
      >
        <Search className="h-5 w-5" aria-hidden="true" />
        <span className="hidden text-sm font-semibold sm:inline">Search</span>
      </button>
      {open ? (
        <Card className="absolute right-0 top-full z-30 mt-2 w-[20rem] rounded-2xl p-3">
          <p className="text-xs font-semibold text-ink">Find a receipt</p>
          <p className="mt-0.5 text-[11px] text-ink/60">
            Paste the memo line from a YNAB transaction (or a receipt ID).
          </p>
          <div className="mt-2 flex gap-2">
            <Input
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); openReceipt(); }
              }}
              placeholder="e.g. [receipt_id:62da5ad1…]"
              className="h-10"
              aria-label="Receipt ID or YNAB memo line"
            />
            <Button size="sm" onClick={openReceipt}>Open</Button>
          </div>
          {error ? <p className="mt-1 text-xs text-red-700">{error}</p> : null}
        </Card>
      ) : null}
    </div>
  );
}

// Tile IDs for the tap-popover system
type StatTileId = "streak" | "droplets" | "shred";

function StatTilePopover({ text, id }: { text: string; id: string; align?: "left" | "right" }) {
  // Anchored to the trigger's left edge, then nudged horizontally so it never
  // spills past either viewport edge — a static left/right anchor can't keep a
  // mid-row chip's popover on screen at 320px (the chip's position is unknown
  // until layout).
  const ref = useRef<HTMLDivElement | null>(null);
  const measuredRef = useRef(false);
  const [shiftX, setShiftX] = useState(0);
  useEffect(() => {
    // Measure exactly once per mount, while the transform is still translateX(0),
    // so the rect is the element's natural position. A ref guard makes this
    // idempotent under React Strict Mode's double effect-invoke (a functional
    // accumulator would otherwise double the shift).
    if (measuredRef.current) return;
    const el = ref.current;
    if (!el) return;
    measuredRef.current = true;
    const margin = 8;
    const viewportWidth = document.documentElement.clientWidth;
    const rect = el.getBoundingClientRect();
    if (rect.left < margin) setShiftX(margin - rect.left);
    else if (rect.right > viewportWidth - margin) setShiftX(viewportWidth - margin - rect.right);
  }, [text]);
  return (
    <div
      ref={ref}
      role="status"
      id={id}
      style={{ transform: `translateX(${shiftX}px)` }}
      className="absolute left-0 top-full z-20 mt-1 w-max max-w-[min(16rem,calc(100vw-1rem))] rounded-xl bg-ink/95 px-3 py-2 text-[11px] leading-relaxed text-sand shadow-float"
    >
      {text}
    </div>
  );
}

/**
 * StatChip — one game-stat tile in the header (streak / droplets / shred).
 * Every chip shares ONE internal structure — leading icon, then a stacked
 * number + short label — so all visible chips sit on the same baseline and at
 * the same height regardless of which ones are conditionally shown. The label
 * is always present (an unlabeled "2" is a mystery); tapping still opens the
 * fuller explanation in a popover.
 */
function StatChip({
  icon, value, label, gradient, popoverId, popoverText, ariaLabel, testid, open, onToggle,
}: {
  icon: React.ReactNode;
  value: number;
  label: string;
  gradient: string;
  popoverId: string;
  popoverText: string;
  ariaLabel: string;
  testid: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        aria-describedby={open ? popoverId : undefined}
        data-testid={testid}
        aria-label={ariaLabel}
        className="flex items-center gap-2.5 rounded-2xl px-3 py-2 text-left transition hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
        style={{ background: gradient }}
      >
        <span className="shrink-0" aria-hidden="true">{icon}</span>
        <span className="flex min-w-0 flex-col leading-none">
          <span className="text-lg font-bold text-white">{value}</span>
          <span className="mt-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-white/75">
            {label}
          </span>
        </span>
      </button>
      {open ? <StatTilePopover id={popoverId} text={popoverText} /> : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Help dialog — one place for help, split into "Using the app" and "The game".
// (Previously this was a game-only "How to play" dialog; app guidance now lives
// alongside it under its own tab so there's a single help destination.)
// ─────────────────────────────────────────────────────────────────────────────

type HelpTab = "app" | "game";

function HelpDialog({
  open,
  onClose,
  avgValidationAgeHours,
  rules,
}: {
  open: boolean;
  onClose: () => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  avgValidationAgeHours: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rules: any;
}) {
  const waterCap = rules?.water_capacity ?? 5;
  const passEvery = rules?.pass_every_green_weeks ?? 4;
  const greenHours = rules?.green_hours_threshold ?? 24;
  const brownHours = rules?.brown_hours_threshold ?? 72;

  const [tab, setTab] = useState<HelpTab>("app");
  // Always land on "Using the app" each time it's opened — the app guidance is
  // the general-purpose help; the game is the optional extra.
  useEffect(() => {
    if (open) setTab("app");
  }, [open]);

  const TAB_DEFS: Array<{ id: HelpTab; label: string }> = [
    { id: "app", label: "Using the app" },
    { id: "game", label: "The game" },
  ];

  return (
    <Dialog open={open} onClose={onClose} labelledById="help-heading">
      <Card className="w-full max-w-sm space-y-4 border-0 shadow-none">
        <h2 id="help-heading" className="text-base font-semibold">Help</h2>

        {/* Tab switcher — one help destination, separated by app vs game. */}
        <div role="tablist" aria-label="Help topics" className="flex gap-2 rounded-2xl bg-ink/5 p-1">
          {TAB_DEFS.map((t) => {
            const isActive = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                data-testid={`help-tab-${t.id}`}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex-1 rounded-xl px-3 py-1.5 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70",
                  isActive ? "bg-surface text-ink shadow-soft" : "text-ink/55 hover:text-ink",
                )}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {tab === "app" ? (
          <div className="space-y-4">
            <section className="space-y-2 text-sm text-ink/80">
              <p className="font-semibold text-ink">What Snappy does</p>
              <p>
                Snappy reads each receipt and drafts a YNAB transaction from it &mdash; payee,
                amount, date, and category. You check the draft, then send it to YNAB. Nothing
                reaches YNAB until you say so.
              </p>
            </section>

            <section className="space-y-2 text-sm text-ink/80">
              <p className="font-semibold text-ink">Reviewing a receipt</p>
              <p>
                Tap any card to open it. Confirm the payee, amount, and date, pick or fix the
                category, then sync. Receipts Snappy isn&apos;t sure about are marked{" "}
                <span className="font-semibold text-ink">Needs review</span> so they rise to the
                top of your list.
              </p>
            </section>

            <section className="space-y-2 rounded-xl bg-emerald-50 px-3 py-2.5 text-sm text-ink/80 ring-1 ring-inset ring-emerald-600/15">
              <p className="flex items-center gap-1.5 font-semibold text-emerald-800">
                <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                The Quick sync button
              </p>
              <p>
                The green <span className="font-semibold">Quick sync</span> button only appears on
                receipts Snappy is highly confident about &mdash; where the amount, date, and payee
                came through cleanly with nothing ambiguous. One tap sends it to YNAB without opening
                it (you still get a quick confirm first). For anything Snappy is unsure about, the
                button stays hidden so you review it yourself before it syncs.
              </p>
            </section>

            <section className="space-y-2 text-sm text-ink/80">
              <p className="font-semibold text-ink">Reading a card</p>
              <p>
                The amount sits top-right &mdash; dark for money spent, green for money coming back
                (a refund). The grey line shows the receipt&apos;s date and where it is in the
                pipeline.
              </p>
            </section>
          </div>
        ) : (
          <div className="space-y-4">
            <section className="space-y-2 text-sm text-ink/80">
              <p className="font-semibold text-ink">Keep your receipts crisp</p>
              <p>
                Each week shows that week&apos;s slowest receipt, aging by how long it took to reach YNAB
                &mdash; measured from the date on the receipt to when it synced. Within{" "}
                {formatWaitTime(greenHours)} &rarr; crisp and green; within {formatWaitTime(brownHours)}{" "}
                &rarr; dog-eared and yellow; longer &rarr; crumpled and brown. Weeks with no receipts
                don&apos;t count for &mdash; or against &mdash; you.
              </p>
            </section>

            <section className="space-y-2 text-sm text-ink/80">
              <p className="font-semibold text-ink">Droplets &amp; fires</p>
              <p>
                Catch one of Snappy&apos;s category mistakes while reviewing and you earn a droplet
                (you can hold {waterCap}). If a synced transaction has to be fixed in YNAB later,
                a fire lands on that receipt&apos;s week. Keep your droplets up: while you have more
                droplets than fires you&apos;re safe, and Snappy will auto-spend one to smother a fire
                you can&apos;t cover. But if a fire breaks out while you&apos;re out of droplets, your
                worst week (the one with the most fires) burns to ash for good. Tap a flaming week to
                douse it, one droplet per fire.
              </p>
              {/* Full trail legend (the row only shows the timeliness states). */}
              <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1.5 rounded-xl bg-ink/5 px-3 py-2.5 text-xs text-ink/75">
                <span className="flex items-center gap-1.5">
                  <ReceiptStateIcon state="green" className="h-4 w-4" /> on time
                </span>
                <span className="flex items-center gap-1.5">
                  <ReceiptStateIcon state="yellow" className="h-4 w-4" /> a bit late
                </span>
                <span className="flex items-center gap-1.5">
                  <ReceiptStateIcon state="brown" className="h-4 w-4" /> very late
                </span>
                <span className="flex items-center gap-1.5">
                  <Flame className="h-3.5 w-3.5 text-orange-500" aria-hidden="true" /> fire (fix needed in YNAB)
                </span>
                <span className="flex items-center gap-1.5">
                  <ReceiptStateIcon state="burnt" className="h-4 w-4" /> burned to ash
                </span>
              </div>
            </section>

            <section className="space-y-2 text-sm text-ink/80">
              <p className="font-semibold text-ink">Streak &amp; shred tokens</p>
              <p>
                Green, flame-free weeks in a row build your streak; an active flame pauses it until
                you douse it. Every {passEvery} streak weeks earns a shred token &mdash; shred a late receipt
                and it won&apos;t count against its week.
              </p>
            </section>

            <section className="rounded-xl bg-ink/5 px-3 py-2 text-sm">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink/50">Your average time to YNAB</p>
              <p className="mt-1 text-lg font-bold text-ink">
                {avgValidationAgeHours == null ? "Nothing synced yet" : formatWaitTime(avgValidationAgeHours)}
              </p>
              <p className="text-xs text-ink/60">Average time from a receipt&apos;s purchase date to landing in YNAB.</p>
            </section>
          </div>
        )}

        <div className="flex justify-end">
          <Button variant="outline" onClick={onClose}>Got it</Button>
        </div>
      </Card>
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Week trail
// ─────────────────────────────────────────────────────────────────────────────

/** A faint dashed receipt outline for the current week before any receipts land. */
function BlankReceipt({ className }: { className?: string }) {
  return (
    <svg viewBox="4.5 1 15 20" className={className} aria-hidden="true">
      <path
        d="M6 2.5h12a1 1 0 0 1 1 1v16l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3v-16a1 1 0 0 1 1-1Z"
        fill="none"
        stroke="#6ee7b7"
        strokeWidth="1.4"
        strokeDasharray="2.5 2"
      />
    </svg>
  );
}

/**
 * WeekTrail — one row of uniform week cells that fills the card width.
 * - Every slot (past + hero) shares the same column structure: a neutral cell
 *   holding a receipt in its state, with a tiny date label below.
 * - HERO cell (last slot): slightly taller, mint ring, labeled "This week".
 * - PAST cells: neutral rounded slots; the receipt's own tint + wear shows the
 *   state (crisp green / dog-eared yellow / crumpled brown / ash burnt).
 *   - Mobile: show last 4 past cells (hidden sm:flex for older 4)
 *   - EXCEPTION: any hidden-on-mobile slot with flames or burnt gets forced-shown
 * - Flames: a small corner badge (with a count) on flaming weeks
 * - Tap popover explains what the week's state means + Douse action
 * - One-line micro-legend below the row
 */
function WeekTrail({
  slots,
  waterUnits,
  isDousingPending,
  onDouse,
  gameTimezone,
  greenHours,
  brownHours,
}: {
  slots: GameWeeklySlot[];
  waterUnits: number;
  isDousingPending: boolean;
  onDouse: (slot: GameWeeklySlot) => void;
  gameTimezone: string;
  greenHours: number;
  brownHours: number;
}) {
  const [openNode, setOpenNode] = useState<number | null>(null);

  // Close on Escape
  useEffect(() => {
    if (openNode === null) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") setOpenNode(null); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [openNode]);

  // Close on outside click
  const trailRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (openNode === null) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (trailRef.current && !trailRef.current.contains(e.target as Node)) setOpenNode(null);
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [openNode]);

  if (slots.length === 0) return null;

  const heroSlot = slots[slots.length - 1];
  // Reading order: newest (this week / hero) on the LEFT, oldest on the RIGHT.
  // `slots` is oldest→newest, so reverse the past weeks: the most recent sits next
  // to the hero and the oldest trails off to the right (hidden first on mobile).
  const reversedPast = slots.slice(0, -1).reverse(); // index 0 = most recent past

  // Mobile shows the hero + the 4 most recent past cells; older cells (further to
  // the right) collapse unless they carry flames or are burnt.
  const MOBILE_PAST_VISIBLE = 4;
  function isHiddenOnMobile(reversedIndex: number, slot: GameWeeklySlot): boolean {
    if (reversedIndex < MOBILE_PAST_VISIBLE) return false;
    return !(slot.flames > 0 || slot.burnt);
  }

  function slotLabel(slot: GameWeeklySlot, isCurrent: boolean): string {
    const receiptsPart = slot.receipt_count === 0 ? "no receipts yet" : `${slot.receipt_count} receipt${slot.receipt_count === 1 ? "" : "s"} scored`;
    const flamePart = slot.flames > 0 ? `, ${slot.flames} flame${slot.flames === 1 ? "" : "s"}` : "";
    const burntPart = slot.burnt ? ", burnt" : "";
    return `${isCurrent ? "This week — " : ""}${formatWeekRange(slot.start_at, slot.end_at, gameTimezone)} · ${receiptsPart}${flamePart}${burntPart}`;
  }

  /** Plain-English explanation of the slot's state, shown in the tap popover. */
  function slotMeaning(slot: GameWeeklySlot): string | null {
    if (slot.burnt) return "Fires outnumbered your droplets, so this week burned to ash. It can't be brought back.";
    if (slot.flames > 0) return "A flame means a synced receipt had to be corrected in YNAB afterwards. Douse it with a droplet before fires outnumber your droplets.";
    if (slot.display_state === "green") return `Every receipt reached YNAB within ${formatWaitTime(greenHours)} of its purchase. Crisp.`;
    if (slot.display_state === "yellow") return `The slowest receipt took up to ${formatWaitTime(brownHours)} to reach YNAB — a little dog-eared.`;
    if (slot.display_state === "brown") return `The slowest receipt took more than ${formatWaitTime(brownHours)} to reach YNAB — crumpled.`;
    if (slot.receipt_count === 0) return "No receipts — this week doesn't count for or against you.";
    return null;
  }

  /** The receipt glyph for a week — its tint + wear shows the state; ash when burnt. */
  function slotGlyph(slot: GameWeeklySlot, sizeClass: string) {
    if (slot.burnt) return <ReceiptStateIcon state="burnt" className={sizeClass} />;
    const state = slot.display_state;
    if (state === "green" || state === "yellow" || state === "brown") {
      return <ReceiptStateIcon state={state} className={sizeClass} />;
    }
    return null;
  }

  /** A small corner badge showing the fire count — never overflows the cell. */
  function renderFlameBadge(count: number, large: boolean) {
    return (
      <span
        className="absolute -right-1.5 -top-1.5 z-10 flex items-center gap-0.5 rounded-full bg-stone-900/90 px-1 py-0.5 shadow-sm animate-fire-fade"
        aria-hidden="true"
      >
        <Flame className={cn("text-orange-400", large ? "h-3 w-3" : "h-2.5 w-2.5")} />
        {count > 1 ? (
          <span className={cn("font-bold leading-none text-orange-200", large ? "text-[9px]" : "text-[8px]")}>{count}</span>
        ) : null}
      </span>
    );
  }

  function PastStamp({ slot, reversedIndex }: { slot: GameWeeklySlot; reversedIndex: number }) {
    const isOpen = openNode === slot.index;
    const label = slotLabel(slot, false);
    const meaning = slotMeaning(slot);
    const canDouse = slot.flames > 0 && !slot.burnt && waterUnits > 0;
    const hiddenOnMobile = isHiddenOnMobile(reversedIndex, slot);
    // Cells near the left anchor their popover on the left edge; the rest on the
    // right, so popovers never run off the far side of the row.
    const alignLeft = reversedIndex <= 1;

    const buttonEl = (
      <div className={cn("relative min-w-0 flex-1 flex-col items-center gap-1", hiddenOnMobile ? "hidden sm:flex" : "flex")}>
        <button
          type="button"
          data-testid={`trail-week-${slot.index}`}
          onClick={() => setOpenNode((prev) => (prev === slot.index ? null : slot.index))}
          aria-describedby={isOpen ? `trail-popover-${slot.index}` : undefined}
          className={cn(
            "relative flex h-12 w-full max-w-[4.5rem] items-center justify-center rounded-xl border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink",
            slot.burnt
              ? "border-white/5 bg-stone-900/60"
              : slot.display_state
                ? "border-white/10 bg-white/5"
                : "border-dashed border-sand/25 bg-white/[0.03]",
          )}
          aria-label={label}
          title={label}
        >
          {slotGlyph(slot, "h-9 w-9")}
          {slot.flames > 0 && !slot.burnt ? renderFlameBadge(slot.flames, false) : null}
        </button>
        <p className="text-[10px] text-sand/70 whitespace-nowrap">{formatWeekStart(slot.start_at, gameTimezone)}</p>

        {/* Popover — anchored on whichever edge keeps it on-screen */}
        {isOpen ? (
          <div
            role="status"
            id={`trail-popover-${slot.index}`}
            className={cn(
              "absolute bottom-full z-20 mb-2 w-max max-w-[min(14rem,calc(100vw-2rem))] rounded-xl bg-ink/95 px-3 py-2 text-[11px] leading-relaxed text-sand shadow-float",
              alignLeft ? "left-0" : "right-0",
            )}
          >
            <p>{label}</p>
            {meaning ? <p className="mt-1 text-[10px] text-sand/70">{meaning}</p> : null}
            {canDouse ? (
              <button
                type="button"
                data-testid="douse-button"
                className="mt-2 flex items-center gap-1 rounded-lg bg-sky-500 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-sky-400 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70"
                onClick={(e) => { e.stopPropagation(); setOpenNode(null); onDouse(slot); }}
                disabled={isDousingPending}
              >
                <Droplets className="h-3 w-3" />
                {isDousingPending ? "Dousing…" : `Douse (${slot.flames} droplet${slot.flames === 1 ? "" : "s"})`}
              </button>
            ) : slot.flames > 0 && !slot.burnt ? (
              <p className="mt-1 text-[10px] text-sand/75">No droplets to douse with.</p>
            ) : null}
          </div>
        ) : null}
      </div>
    );

    return buttonEl;
  }

  // Hero tile (current week)
  const heroHasState = heroSlot.display_state !== null || heroSlot.burnt;
  const heroIsOpen = openNode === heroSlot.index;
  const heroLabel = slotLabel(heroSlot, true);
  const heroMeaning = slotMeaning(heroSlot);
  const heroCanDouse = heroSlot.flames > 0 && !heroSlot.burnt && waterUnits > 0;

  return (
    <div ref={trailRef} className="relative">
      <div className="relative flex items-end gap-1 sm:gap-1.5">
        {/* Hero tile (current week) FIRST — newest on the left (reading order).
            Same size as every other cell; distinguished only by its pulsing mint
            outline. */}
        <div className="relative flex min-w-0 flex-1 flex-col items-center gap-1">
          <button
            type="button"
            data-testid={`trail-week-${heroSlot.index}`}
            onClick={() => setOpenNode((prev) => (prev === heroSlot.index ? null : heroSlot.index))}
            aria-describedby={heroIsOpen ? `trail-popover-${heroSlot.index}` : undefined}
            className={cn(
              "relative flex h-12 w-full max-w-[4.5rem] items-center justify-center rounded-xl border ring-2 ring-mint/60 ring-offset-2 ring-offset-ink animate-current-week-pulse transition focus-visible:outline-none focus-visible:ring-mint",
              heroSlot.burnt
                ? "border-white/5 bg-stone-900/60"
                : heroHasState
                  ? "border-white/10 bg-white/5"
                  : "border-dashed border-mint/40 bg-white/5",
            )}
            aria-label={heroLabel}
            title={heroLabel}
          >
            {heroHasState ? (
              slotGlyph(heroSlot, "h-9 w-9")
            ) : (
              // Empty / no receipts yet: a fresh, blank receipt waiting to be filled
              <BlankReceipt className="h-9 w-9 animate-snappy-sprout opacity-50" />
            )}
            {heroSlot.flames > 0 && !heroSlot.burnt ? renderFlameBadge(heroSlot.flames, true) : null}
          </button>
          <p className="text-[10px] font-semibold text-sand/80 whitespace-nowrap">This week</p>

          {/* Hero popover — left-aligned (hero is the leftmost cell). */}
          {heroIsOpen ? (
            <div
              role="status"
              id={`trail-popover-${heroSlot.index}`}
              className="absolute bottom-full left-0 z-20 mb-2 w-max max-w-[min(15rem,calc(100vw-2rem))] rounded-xl bg-ink/95 px-3 py-2 text-[11px] leading-relaxed text-sand shadow-float"
            >
              <p>{heroLabel}</p>
              {heroMeaning ? <p className="mt-1 text-[10px] text-sand/70">{heroMeaning}</p> : null}
              {heroCanDouse ? (
                <button
                  type="button"
                  data-testid="douse-button"
                  className="mt-2 flex items-center gap-1 rounded-lg bg-sky-500 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-sky-400 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70"
                  onClick={(e) => { e.stopPropagation(); setOpenNode(null); onDouse(heroSlot); }}
                  disabled={isDousingPending}
                >
                  <Droplets className="h-3 w-3" />
                  {isDousingPending ? "Dousing…" : `Douse (${heroSlot.flames} droplet${heroSlot.flames === 1 ? "" : "s"})`}
                </button>
              ) : heroSlot.flames > 0 && !heroSlot.burnt ? (
                <p className="mt-1 text-[10px] text-sand/75">No droplets to douse with.</p>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* Past weeks — most recent next to the hero, oldest trailing off right. */}
        {reversedPast.map((slot, reversedIndex) => (
          <PastStamp key={`past-${slot.index}`} slot={slot} reversedIndex={reversedIndex} />
        ))}
      </div>

      {/* Micro-legend — just the timeliness glyphs (self-explanatory, higher
          contrast). Fires and ash are rarer and sound alarming, so their
          meaning lives in Help and in each flaming/burnt week's own tap
          popover, instead of always sitting in the legend. */}
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-sand/80">
        <span className="flex items-center gap-1.5">
          <ReceiptStateIcon state="green" className="h-3.5 w-3.5" />
          on time
        </span>
        <span className="flex items-center gap-1.5">
          <ReceiptStateIcon state="yellow" className="h-3.5 w-3.5" />
          a bit late
        </span>
        <span className="flex items-center gap-1.5">
          <ReceiptStateIcon state="brown" className="h-3.5 w-3.5" />
          very late
        </span>
        <span className="text-sand/60">· tap a week for details</span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main header component
// ─────────────────────────────────────────────────────────────────────────────

function ReceiptListHeader({
  dashboardData, highlightedCount, totalCount,
  celebratingStreak, userName,
  isDousingPending, onDouse,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  dashboardData: any;
  highlightedCount: number;
  totalCount: number;
  celebratingStreak: boolean;
  userName: string;
  isDousingPending: boolean;
  onDouse: (slot: GameWeeklySlot) => void;
}) {
  const isEmpty = totalCount === 0;
  // Two-pass render for the speech bubble: the line is randomized (greetings/
  // quotes use Math.random + the local clock), which would make the server-
  // rendered HTML differ from the client's first paint → hydration error.
  // Until mounted we pin random/clock to fixed values so SSR and the first
  // client render agree; after mount we re-pick freely. The memo also stops
  // the bubble flickering on every 7s poll.
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  const streak = dashboardData?.momentum?.current_streak ?? 0;
  const waterUnitsVal: number = dashboardData?.correctness?.water_units ?? 0;
  const totalActiveFlames: number = dashboardData?.correctness?.total_active_flames ?? 0;
  const tokenBalance: number = dashboardData?.momentum?.token_balance ?? 0;
  const weekSlots: GameWeeklySlot[] = dashboardData?.forest?.weekly_slots ?? [];

  const derived = useMemo(
    () =>
      deriveSnappyPose({
        needsReviewCount: highlightedCount,
        totalCount: isEmpty ? 0 : 1,
        userName,
        activeFlames: mounted ? totalActiveFlames : 0,
        ...(mounted ? {} : { random: () => 0.5, now: new Date(2026, 0, 1, 12, 0, 0) }),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mounted, highlightedCount, isEmpty, userName, totalActiveFlames],
  );
  const pose = celebratingStreak ? "celebrating" : derived.pose;

  // Which stat chip currently has its popover open (null = none)
  const [openTile, setOpenTile] = useState<StatTileId | null>(null);

  // Help dialog (app + game)
  const [helpOpen, setHelpOpen] = useState(false);

  // Mobile collapse state — persisted in localStorage.
  // null = SSR/unhydrated (always show full content so no layout shift on desktop).
  // After first client render we read the stored preference.
  const [expanded, setExpanded] = useState<boolean | null>(null);
  useEffect(() => {
    try {
      setExpanded(localStorage.getItem("snappy_header_expanded") !== "false");
    } catch {
      setExpanded(true);
    }
  }, []);
  const isExpanded = expanded !== false; // treat null (SSR) as expanded
  const toggleExpanded = () => {
    setExpanded((prev) => {
      const next = prev === false; // toggle: false → true, anything else → false
      try { localStorage.setItem("snappy_header_expanded", String(next)); } catch { /* ignore */ }
      return next;
    });
  };

  // Close open popover on Escape key
  useEffect(() => {
    if (!openTile) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenTile(null);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [openTile]);

  // Close on outside click
  const headerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!openTile) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (headerRef.current && !headerRef.current.contains(e.target as Node)) {
        setOpenTile(null);
      }
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [openTile]);

  const toggleTile = (id: StatTileId) => setOpenTile((prev) => (prev === id ? null : id));

  // Conditional indicators:
  // Droplets pill: visible iff water_units>0 OR total_active_flames>0
  const showDroplets = waterUnitsVal > 0 || totalActiveFlames > 0;
  // Shred token chip: visible iff token_balance >= 1
  const showShred = tokenBalance > 0;

  return (
    <>
      {/* visually-hidden page h1 for a11y; visible eyebrow below */}
      <h1 className="sr-only">Snappy — Receipt to YNAB</h1>

      <Card
        className="animate-reveal overflow-hidden rounded-3xl p-0 text-sand"
        style={{ background: "linear-gradient(135deg, #172026 0%, #0e2a2f 60%, #0d2535 100%)" }}
      >
        {/* ── Top section: always visible ────────────────────────────────── */}
        <div className="flex items-center gap-3 px-4 pt-3 pb-2.5">
          {/* Snappy hero */}
          <div className="shrink-0">
            <Snappy pose={pose} size="h-20 w-20 sm:h-24 sm:w-24" />
          </div>

          {/* Speech bubble + eyebrow */}
          <div className="min-w-0 flex-1">
            {/* Eyebrow label */}
            <p className="truncate whitespace-nowrap text-[10px] font-bold uppercase tracking-[0.18em] text-mint/70" aria-hidden="true">
              SNAPPY<span className="hidden sm:inline"> · RECEIPT &rarr; YNAB</span>
            </p>

            {/* Speech bubble */}
            <div className="relative mt-1.5">
              {/* Tail pointing left toward Snappy */}
              <div
                className="absolute -left-2 top-1/2 -translate-y-1/2 h-0 w-0"
                style={{
                  borderTop: "6px solid transparent",
                  borderBottom: "6px solid transparent",
                  borderRight: "8px solid rgba(255,248,237,0.12)",
                }}
                aria-hidden="true"
              />
              <div
                className="w-fit max-w-full rounded-2xl bg-white/10 px-3 py-2"
              >
                <p className="text-sm font-semibold leading-snug text-sand">
                  {derived.attribution ? <>&ldquo;{derived.line}&rdquo;</> : derived.line}
                </p>
                {derived.attribution ? (
                  <p className="mt-1 text-[11px] text-sand/70" title={derived.attributionSource}>
                    — {derived.attribution}
                  </p>
                ) : null}
              </div>
            </div>
          </div>

          {/* Mobile chevron */}
          <button
            type="button"
            onClick={toggleExpanded}
            aria-expanded={isExpanded}
            aria-label={isExpanded ? "Collapse header" : "Expand header"}
            className="ml-auto inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-sand/75 transition hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-mint/70 sm:hidden"
          >
            {isExpanded ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
          </button>
        </div>

        {/* ── Expandable section: chips + trail ──────────────────────────── */}
        <div className={cn("px-4 pb-3.5 space-y-3", isExpanded ? "block" : "hidden sm:block")}>
          {/* Stat chips row — every chip shares one structure (icon + number +
              label) so they stay the same height however many are shown. */}
          <div ref={headerRef} className="flex flex-wrap gap-2">

            {/* Streak — the one permanent chip; Zap icon (not Flame). */}
            <StatChip
              testid="stat-tile-streak"
              icon={<Zap className="h-5 w-5 text-white/90" />}
              value={streak}
              label="Streak"
              gradient="linear-gradient(135deg, #f59e0b 0%, #f97316 100%)"
              popoverId="tile-popover-streak"
              popoverText="Green, flame-free weeks in a row. Weeks with no receipts don't count — or break — it."
              ariaLabel={`Streak — ${streak} green weeks in a row`}
              open={openTile === "streak"}
              onToggle={() => toggleTile("streak")}
            />

            {/* Droplets — conditional: only when water_units>0 OR any active flames. */}
            {showDroplets ? (
              <StatChip
                testid="droplets-pill"
                icon={<Droplets className="h-5 w-5 text-white/90" />}
                value={waterUnitsVal}
                label="Droplets"
                gradient="linear-gradient(135deg, #0ea5e9 0%, #14b8a6 100%)"
                popoverId="tile-popover-droplets"
                popoverText={`Droplets — earned by catching Snappy's mistakes during review. You can hold up to ${dashboardData?.rules?.water_capacity ?? 5}. Tap a flaming week on your trail to spend them.`}
                ariaLabel={`Droplets — ${waterUnitsVal} available. Earned by catching Snappy's mistakes during review`}
                open={openTile === "droplets"}
                onToggle={() => toggleTile("droplets")}
              />
            ) : null}

            {/* Shred token — conditional: only when token_balance >= 1. */}
            {showShred ? (
              <StatChip
                testid="shred-badge"
                icon={<Scissors className="h-5 w-5 text-white/90" />}
                value={tokenBalance}
                label="Shred"
                gradient="linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%)"
                popoverId="tile-popover-shred"
                popoverText="Shred token — shred a late receipt and it won't count against its week. Earned every 4 consecutive green streak weeks."
                ariaLabel={`Shred tokens — ${tokenBalance} available. Shred a late receipt so it won't count against its week`}
                open={openTile === "shred"}
                onToggle={() => toggleTile("shred")}
              />
            ) : null}
          </div>

          {/* Trail section */}
          <div>
            <div className="mb-2.5 flex items-center justify-between">
              <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-sand/80">
                <Receipt className="h-3.5 w-3.5 text-mint/80" aria-hidden="true" />
                Paper trail
              </p>
              <button
                type="button"
                onClick={() => setHelpOpen(true)}
                className="flex min-h-11 items-center gap-1 rounded-full bg-white/10 px-3 py-1 text-[11px] font-semibold text-sand/85 transition hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70"
                aria-label="Help — how the paper trail and the game work"
              >
                <HelpCircle className="h-4 w-4" aria-hidden="true" />
                Help
              </button>
            </div>

            <WeekTrail
              slots={weekSlots}
              waterUnits={waterUnitsVal}
              isDousingPending={isDousingPending}
              onDouse={onDouse}
              gameTimezone={dashboardData?.rules?.timezone ?? "UTC"}
              greenHours={dashboardData?.rules?.green_hours_threshold ?? 24}
              brownHours={dashboardData?.rules?.brown_hours_threshold ?? 72}
            />
          </div>
        </div>
      </Card>

      <HelpDialog
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        avgValidationAgeHours={dashboardData?.summary?.avg_validation_age_hours}
        rules={dashboardData?.rules}
      />
    </>
  );
}

/**
 * TabBar — replaces the old flat filter chip row.
 *
 * Three primary tabs (To Review / Processing / History) with live count badges.
 * Tabs are accessible: role="tablist" wrapper, role="tab" buttons, aria-selected
 * on the active tab, and focus-visible rings.
 */
function TabBar({
  activeTab,
  setActiveTab,
  reviewCount,
  doneCount,
  lookupSlot,
}: {
  activeTab: ReceiptBucket;
  setActiveTab: (tab: ReceiptBucket) => void;
  reviewCount: number;
  doneCount: number;
  /** Right-aligned slot for the receipt-lookup (search) affordance. */
  lookupSlot?: React.ReactNode;
}) {
  const counts: Record<ReceiptBucket, number> = {
    review: reviewCount,
    done:   doneCount,
  };

  return (
    <section className="animate-reveal relative z-30 flex items-center gap-2 rounded-3xl border border-ink/[0.06] bg-surface/85 p-2 shadow-soft backdrop-blur-[2px]" style={{ animationDelay: "90ms" }}>
      <div role="tablist" aria-label="Receipt queue tabs" className="flex flex-1 gap-2">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.bucket;
          const count = counts[tab.bucket];
          return (
            <button
              key={tab.bucket}
              type="button"
              role="tab"
              aria-selected={isActive}
              data-testid={tab.testid}
              onClick={() => setActiveTab(tab.bucket)}
              className={cn(
                // Active uses the on-brand mint rather than a heavy dark fill,
                // so it reads as selected without competing with the dark header.
                "rounded-full px-4 py-1.5 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2",
                isActive
                  ? "bg-mint text-ink shadow-sm"
                  : "bg-ink/10 text-ink hover:bg-ink/15",
              )}
            >
              {tab.label}
              {" "}
              <span
                className={cn(
                  "inline-flex min-w-[1.25rem] items-center justify-center rounded-full px-1 text-xs font-bold",
                  isActive ? "bg-ink/15 text-ink" : "bg-ink/10",
                )}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>
      {lookupSlot}
    </section>
  );
}

function ReceiptListItem({
  receipt, tile, currentWeekSlot, spendableNow, shredWindowWeeks, onShred, isShredPending, onQuickSync, isQuickSyncPending, onDelete, isDeletePending, index,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  receipt: any;
  tile: GameForestTile | undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  currentWeekSlot: any;
  spendableNow: boolean;
  shredWindowWeeks: number;
  onShred: (receiptId: string) => void;
  isShredPending: boolean;
  onQuickSync: (receiptId: string) => void;
  isQuickSyncPending: boolean;
  onDelete: (receiptId: string) => void;
  isDeletePending: boolean;
  index: number;
}) {
  const iconState = deriveIconState(tile);
  const isProcessing = isProcessingStatus(receipt.status);
  const correctionOpacity = receipt.correction_shade_opacity ?? 0;
  const correctionVisible = correctionOpacity > 0.01;
  const correctionColor = `rgba(15, 23, 42, ${Math.max(0.16, Math.min(0.2 + correctionOpacity * 0.75, 1))})`;

  const canShred =
    tile?.shredded_at == null &&
    (tile?.display_state === "yellow" || tile?.display_state === "brown") &&
    spendableNow &&
    Boolean(
      tile &&
        currentWeekSlot &&
        isWithinShredWindow(tile.validated_at, currentWeekSlot.start_at, currentWeekSlot.end_at, shredWindowWeeks),
    );

  // Timeliness: sprout animation on first mount for green tiles (fallback approach —
  // edge-detection is unreliable vs 10s polling, so we animate once on mount).
  // The animation class collapses to static under prefers-reduced-motion.
  const isGreen = tile?.display_state === "green";
  const sproutClass = isGreen ? "animate-snappy-sprout" : undefined;

  return (
    <div className="relative">
      <Card
        className={cn(
          "animate-reveal transition",
          receipt.status === "needs_review"
            ? "border-amber-300 bg-amber-50/70"
            : receipt.status === "duplicate_review"
              ? "border-orange-300 bg-orange-50/70"
              : undefined,
          isProcessing ? "opacity-80" : undefined,
        )}
        style={{ animationDelay: `${120 + index * 28}ms` }}
      >
        <Link
          href={`/receipts/${receipt.id}`}
          className="absolute inset-0 rounded-[inherit] z-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-sand"
          aria-label={`Open receipt: ${receipt.display_payee_name ?? receipt.original_filename}`}
        />
        <div className="flex items-start gap-3">
          {/* Leading state-icon column — fixed width so every card's title
              starts on the same vertical line, icon present or not. */}
          <div className="mt-0.5 flex shrink-0 items-center gap-1.5">
            {correctionVisible ? (
              <span title="YNAB correction tracked" aria-label="YNAB correction tracked">
                <Flame
                  className="h-4 w-4 animate-fire-fade"
                  style={{ color: correctionColor, opacity: Math.max(correctionOpacity, 0.12) }}
                  aria-hidden="true"
                />
              </span>
            ) : null}
            <div className="flex w-6 justify-center">
              {iconState ? <ReceiptStateIcon state={iconState} className={cn("h-5 w-5", sproutClass)} /> : null}
            </div>
          </div>

          <div className="min-w-0 flex-1">
            {/* Title row — payee on the left, the transaction amount pinned
                top-right (dark for an outflow/purchase, green for an inflow/
                refund) so the number you care about reads first. */}
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 flex-1 truncate text-sm font-semibold leading-snug">
                {receipt.display_payee_name ?? receipt.original_filename}
              </p>
              {(() => {
                const kind = receipt.transaction_kind ?? "purchase";
                const millis = receipt.display_total_milliunits;
                if (millis == null) {
                  return <span className="shrink-0 text-base font-bold tabular-nums text-ink/40">--</span>;
                }
                const formatted = formatSignedDollars(signedDollars(millis / 1000, kind));
                const isInflow = kind === "refund";
                return (
                  <span
                    className={cn(
                      "shrink-0 text-base font-bold tabular-nums leading-snug",
                      isInflow ? "text-emerald-600" : "text-ink",
                    )}
                  >
                    {formatted}
                  </span>
                );
              })()}
            </div>

            {/* Meta line — status badge plus a calm grey detail: the working
                note while processing, otherwise the receipt's date (and, on the
                Done tab, how long it took to reach YNAB). */}
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
              {/* The specific reason ("Needs an account", "Ready to sync", …)
                  when we have one; otherwise the raw pipeline status. */}
              {receipt.review_hint ? (
                <ReviewHintBadge hint={receipt.review_hint} />
              ) : (
                <StatusBadge status={receipt.status} />
              )}
              <span className="text-xs text-ink/55">
                {isProcessing
                  ? "Snappy is working on this one — no action needed"
                  : formatReceiptDate(receipt.display_receipt_date, receipt.ingested_at)}
                {!isProcessing && tile?.age_hours_at_validation != null
                  ? ` · ${formatWaitTime(tile.age_hours_at_validation)} to YNAB`
                  : ""}
              </span>
            </div>

            {receipt.correction_message ? (
              <p className="mt-1 text-[11px] font-semibold text-ink/70">{receipt.correction_message}</p>
            ) : null}

            {/* Footer — actions, right-aligned. The amount now lives in the
                title row, so this row carries the buttons alone. */}
            <div className="mt-3 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
              <div className="relative z-10 ml-auto flex flex-wrap items-center justify-end gap-1.5">
                {tile?.display_state === "shredded" ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-700">
                    <Sparkles className="h-3 w-3" />
                    Shredded
                  </span>
                ) : null}
                {canShred ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 gap-1 border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100"
                    onClick={() => onShred(receipt.id)}
                    disabled={isShredPending}
                  >
                    <Scissors className="h-3.5 w-3.5" />
                    {isShredPending ? "Shredding..." : "Shred"}
                  </Button>
                ) : null}
                {receipt.sync_ready ? (
                  <Button
                    data-testid="quick-sync-button"
                    size="sm"
                    variant="success"
                    className="h-8 gap-1.5 px-3"
                    title="Quick sync — available because Snappy is highly confident about this receipt"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onQuickSync(receipt.id);
                    }}
                    disabled={isQuickSyncPending}
                  >
                    <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                    {isQuickSyncPending ? "Syncing…" : "Quick sync"}
                  </Button>
                ) : !isProcessing && receipt.status !== "synced" ? (
                  // Every actionable card gets a visible primary action, so no
                  // card ever shows the trash icon as its only button. (The whole
                  // card is also a link; this is the explicit affordance.)
                  <Link
                    href={`/receipts/${receipt.id}`}
                    data-testid="review-receipt-button"
                    className="inline-flex h-8 items-center gap-1.5 rounded-xl2 border border-ink/15 bg-surface px-3 text-sm font-semibold text-ink shadow-soft transition hover:border-ink/30 hover:bg-cream/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-sand"
                  >
                    Review
                  </Link>
                ) : null}
                {receipt.status !== "synced" && receipt.status !== "syncing" ? (
                  <button
                    type="button"
                    data-testid="delete-receipt-button"
                    aria-label="Delete receipt"
                    title="Delete receipt (you can undo)"
                    className="group inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 disabled:opacity-40"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onDelete(receipt.id);
                    }}
                    disabled={isDeletePending}
                  >
                    {/* 44px hit area; the visible glyph stays small + quiet so
                        the (reversible, undo-toasted) delete isn't over-exposed. */}
                    <span className="flex h-8 w-8 items-center justify-center rounded-full text-ink/40 opacity-70 transition group-hover:bg-red-50 group-hover:text-red-600 group-hover:opacity-100 group-focus-visible:opacity-100">
                      <Trash2 className="h-3.5 w-3.5" />
                    </span>
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

/**
 * QuickSyncPreview — the confirm gate for "Looks right — Sync" on the list.
 * Fetches the receipt's full detail on demand and shows the SAME bank-register
 * preview the detail page uses (account, date, total, category splits, memo),
 * so the user always sees exactly what will land in YNAB before approving.
 * Cancel returns to the list; nothing is sent until Confirm.
 */
function QuickSyncPreview({
  receiptId, onClose, onConfirm, isSyncing,
}: {
  receiptId: string | null;
  onClose: () => void;
  onConfirm: (receiptId: string) => void;
  isSyncing: boolean;
}) {
  const open = receiptId !== null;

  const detailQuery = useQuery({
    queryKey: ["receipt", receiptId],
    queryFn: () => getReceiptDetail(receiptId as string),
    enabled: open,
  });
  const cacheQuery = useQuery({
    queryKey: ["ynab-cache"],
    queryFn: () => getYnabCache(),
    staleTime: 20_000,
    enabled: open,
  });
  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: () => getAppConfig(),
    staleTime: 60_000,
    enabled: open,
  });

  const accounts = useMemo(
    () =>
      (cacheQuery.data ?? [])
        .filter((item) => item.entity_type === "account")
        .map((item) => ({
          entity_id: String(item.entity_id ?? "").trim(),
          name: String(item.name ?? "").trim() || "Unknown account",
        }))
        .filter((item) => item.entity_id.length > 0),
    [cacheQuery.data],
  );
  const categories = useMemo(
    () =>
      (cacheQuery.data ?? [])
        .filter((item) => item.entity_type === "category")
        .map((item) => ({
          entity_id: String(item.entity_id ?? "").trim(),
          name: String(item.name ?? "").trim(),
          group_name: item.group_name == null ? null : String(item.group_name),
        }))
        .filter((item) => item.entity_id.length > 0 && item.name.length > 0),
    [cacheQuery.data],
  );

  if (!open) return null;

  const receipt = detailQuery.data;
  if (!receipt) {
    return (
      <Dialog open onClose={onClose} labelledById="quick-sync-loading-heading">
        <Card className="w-full max-w-sm border-0 p-4 shadow-none">
          <h2 id="quick-sync-loading-heading" className="text-sm font-semibold">Review transaction</h2>
          <p className="mt-2 text-sm text-ink/60">
            {detailQuery.isError ? "Couldn't load the receipt — try again from its page." : "Getting the details…"}
          </p>
        </Card>
      </Dialog>
    );
  }

  const payload = (receipt.latest_validation?.payload ?? {}) as Record<string, unknown>;
  const draft = toDraftFromPayload(payload, receipt.display_payee_name ?? "");
  const config = configQuery.data;

  return (
    <SyncPreviewDialog
      open
      onClose={onClose}
      draft={draft}
      accounts={accounts}
      categories={categories}
      hasSuccessfulSync={receipt.has_successful_sync}
      mode={{
        dryRun: config?.ynab_dry_run ?? true,
        syncEnabled: config?.ynab_sync_enabled ?? false,
        budgetName: config?.ynab_budget_name ?? null,
        budgetId: config?.ynab_budget_id ?? null,
        newFlagColor: config?.new_transaction_flag_color ?? "green",
        updatedFlagColor: config?.updated_transaction_flag_color ?? "blue",
      }}
      isConfirmDisabled={false}
      isSyncing={isSyncing}
      dateTimeConfirmed={receipt.latest_twin?.confirmed_sections.date_time ?? false}
      totalConfirmed={receipt.latest_twin?.confirmed_sections.total ?? false}
      onConfirm={() => onConfirm(receipt.id)}
      showSkipPreviewOption={false}
    />
  );
}

/**
 * DevtoolsToggleRow — a single toggle row inside DebugPanel that controls
 * whether the ReactQueryDevtools palm-tree button is shown.  Uses
 * useSyncExternalStore so the DevtoolsGate in providers.tsx updates live
 * (without a reload) when the user flips the switch.
 */
function DevtoolsToggleRow() {
  const enabled = useSyncExternalStore(subscribeDevtoolsPref, readDevtoolsPref, () => false);
  return (
    <div className="flex items-start gap-3 rounded-xl bg-ink/5 px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-ink">Show query devtools</p>
        <p className="mt-0.5 text-xs leading-relaxed text-ink/60">
          Shows the TanStack Query floating panel (palm-tree button). Off by default — it overlaps the sync bar on mobile.
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        onClick={() => setDevtoolsPref(!enabled)}
        className={cn(
          "relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2",
          enabled ? "bg-mint" : "bg-ink/20",
        )}
        aria-label="Toggle query devtools"
      >
        <span
          className={cn(
            "absolute top-0.5 block h-4 w-4 rounded-full bg-white shadow transition-transform",
            enabled ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}

/**
 * GameSettingsSection — admin-configurable game parameters (timeliness thresholds
 * + shred window). These are real persistent settings (not testing seed data), so
 * they live in their own self-contained section with their own save. Changing the
 * thresholds re-grades existing receipts server-side.
 */
function GameSettingsSection() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["game-settings"], queryFn: getGameSettings });

  const [form, setForm] = useState({ user_name: "", green_hours_threshold: 24, brown_hours_threshold: 72, shred_window_weeks: 2 });
  useEffect(() => {
    if (!settingsQuery.data) return;
    setForm({
      user_name: settingsQuery.data.user_name ?? "",
      green_hours_threshold: settingsQuery.data.green_hours_threshold ?? 24,
      brown_hours_threshold: settingsQuery.data.brown_hours_threshold ?? 72,
      shred_window_weeks: settingsQuery.data.shred_window_weeks ?? 2,
    });
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => updateGameSettings(form),
    onSuccess: () => {
      toast({ variant: "success", message: "Game settings saved" });
      queryClient.invalidateQueries({ queryKey: ["game-settings"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Failed to save game settings" });
    },
  });

  type NumKey = "green_hours_threshold" | "brown_hours_threshold" | "shred_window_weeks";
  const numField = (key: NumKey, label: string, min: number) => (
    <label className="text-xs font-semibold text-ink/70">
      {label}
      <Input
        type="number"
        value={form[key]}
        onChange={(event) => setForm({ ...form, [key]: Math.max(Number(event.target.value) || 0, min) })}
      />
    </label>
  );

  return (
    <section className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink/50">Game settings</p>
      <p className="text-xs text-ink/60">
        Persistent game configuration — saved on the server and respected on every load, on any device.
        Saving re-grades existing receipts.
      </p>
      {settingsQuery.isError ? (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">Debug tools are disabled or unavailable.</p>
      ) : null}
      <label className="block text-xs font-semibold text-ink/70">
        Player name
        <Input
          value={form.user_name}
          onChange={(event) => setForm({ ...form, user_name: event.target.value })}
          placeholder="Anna"
        />
      </label>
      <p className="text-[11px] text-ink/50">Snappy greets this name. Leave blank for a generic greeting.</p>
      <div className="grid grid-cols-2 gap-2">
        {numField("green_hours_threshold", "On-time within (hours)", 0)}
        {numField("brown_hours_threshold", "Very late after (hours)", 0)}
        {numField("shred_window_weeks", "Shred window (weeks)", 1)}
      </div>
      <p className="text-[11px] text-ink/50">
        ≤ on-time → green · ≤ very-late → yellow · beyond → brown. Very-late is clamped to at least on-time.
      </p>
      <div className="flex justify-end">
        <Button size="sm" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || settingsQuery.isLoading}>
          {saveMutation.isPending ? "Saving…" : "Save settings"}
        </Button>
      </div>
    </section>
  );
}

/**
 * DebugPanel — the developer's drawer. Every maintenance action the old
 * hamburger menu exposed lives here now, each with a plain-language
 * explanation of what it does and why you'd run it. Normal use of the app
 * never requires opening this panel (refresh happens automatically).
 */
function DebugPanel({
  open, onClose,
  onScan, isScanPending,
  onFetchUpdates, isFetchUpdatesPending,
  onRebuild, isRebuildPending,
  onRecompute, isRecomputePending,
  onOpenCardMappings,
  debugForm, setDebugForm, debugResetFloors, setDebugResetFloors, isSeedLoading, isSeedError, isSaving, onSave,
}: {
  open: boolean;
  onClose: () => void;
  onScan: () => void; isScanPending: boolean;
  onFetchUpdates: () => void; isFetchUpdatesPending: boolean;
  onRebuild: () => void; isRebuildPending: boolean;
  onRecompute: () => void; isRecomputePending: boolean;
  onOpenCardMappings: () => void;
  debugForm: DebugSeedForm;
  setDebugForm: (v: DebugSeedForm) => void;
  debugResetFloors: boolean;
  setDebugResetFloors: (v: boolean) => void;
  isSeedLoading: boolean;
  isSeedError: boolean;
  isSaving: boolean;
  onSave: () => void;
}) {
  const numField = (key: keyof DebugSeedForm, label: string, min = 0) => (
    <label className="text-xs font-semibold text-ink/70">
      {label}
      <Input
        type="number"
        value={debugForm[key] as number}
        onChange={(event) => setDebugForm({ ...debugForm, [key]: Math.max(Number(event.target.value) || 0, min) })}
      />
    </label>
  );

  const maintenanceRow = (
    label: string,
    description: string,
    onClick: () => void,
    pending: boolean,
    pendingLabel: string,
  ) => (
    <div className="flex items-start gap-3 rounded-xl bg-ink/5 px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-ink">{label}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-ink/60">{description}</p>
      </div>
      <Button variant="outline" size="sm" className="shrink-0" onClick={onClick} disabled={pending}>
        {pending ? pendingLabel : "Run"}
      </Button>
    </div>
  );

  return (
    <Dialog open={open} onClose={onClose} labelledById="debug-panel-heading" data-testid="debug-panel">
      <Card className="max-h-[85vh] w-full max-w-lg space-y-4 overflow-y-auto animate-incident-enter border-0 shadow-none">
        <div>
          <h2 id="debug-panel-heading" className="text-base font-semibold">Debug panel</h2>
          <p className="mt-0.5 text-xs text-ink/60">
            Developer tools. Everyday use never needs this — checking for new receipts and
            pulling YNAB updates happen automatically.
          </p>
        </div>

        {/* ── Game settings (admin config: name, timeliness, shred window) ── */}
        <GameSettingsSection />

        {/* ── Maintenance ─────────────────────────────────────────────── */}
        <section className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/50">Maintenance</p>
          {maintenanceRow(
            "Check for new receipts",
            "Scans the receipt inbox folder right now. Runs automatically when the app is opened or revisited.",
            onScan, isScanPending, "Checking…",
          )}
          {maintenanceRow(
            "Pull YNAB updates",
            "Refreshes categories, accounts, and recent transactions from YNAB. Also runs automatically.",
            onFetchUpdates, isFetchUpdatesPending, "Pulling…",
          )}
          {maintenanceRow(
            "Rebuild game board",
            "Recalculates streaks, water, fire, and the weekly board from scratch. Safe to run anytime; fixes a board that looks wrong.",
            onRebuild, isRebuildPending, "Rebuilding…",
          )}
          {maintenanceRow(
            "Re-check YNAB corrections",
            "Looks at synced receipts for category changes made in YNAB, then updates water and fire. Normally happens on its own.",
            onRecompute, isRecomputePending, "Checking…",
          )}
          <div className="flex items-start gap-3 rounded-xl bg-ink/5 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">Card mappings</p>
              <p className="mt-0.5 text-xs leading-relaxed text-ink/60">
                Which card (last 4 digits) belongs to which YNAB account. Learned automatically on sync; edit here.
              </p>
            </div>
            <Button variant="outline" size="sm" className="shrink-0" onClick={onOpenCardMappings}>
              Open
            </Button>
          </div>
        </section>

        {/* ── Developer tools ─────────────────────────────────────────── */}
        <DevtoolsToggleRow />

        {/* ── Game seed (advanced) ────────────────────────────────────── */}
        <details className="rounded-xl border border-ink/10 px-3 py-2">
          <summary className="cursor-pointer select-none text-sm font-semibold text-ink/80">
            Game seed (advanced)
          </summary>
          <div className="mt-3 space-y-3">
            <p className="text-xs text-ink/60">
              Overrides the game&apos;s internal counters for testing. Saving applies the values to the live board.
            </p>
            {isSeedLoading ? <p className="text-sm text-ink/70">Loading seed...</p> : null}
            {isSeedError ? (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">Debug tools are disabled or unavailable.</p>
            ) : null}

            <label className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={debugForm.enabled}
                onChange={(event) => setDebugForm({ ...debugForm, enabled: event.target.checked })}
              />
              Seed enabled
            </label>

            <div className="grid grid-cols-2 gap-2">
              {numField("water_units", "Droplets")}
              {numField("current_week_flames", "Current Week Flames")}
              {numField("token_balance", "Shred Token Balance")}
              {numField("token_earned_count", "Shred Tokens Earned")}
              {numField("token_spent_count", "Shred Tokens Spent")}
            </div>
            <p className="text-[11px] text-ink/50">current_week_flames: demo flames on the current week</p>

            <label className="inline-flex items-center gap-2 text-xs text-ink/70">
              <input
                type="checkbox"
                checked={debugResetFloors}
                onChange={(event) => setDebugResetFloors(event.target.checked)}
              />
              Reset replay floors to now on save
            </label>

            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setDebugForm({ enabled: false, water_units: 0, current_week_flames: 0, token_balance: 0, token_earned_count: 0, token_spent_count: 0 });
                  setDebugResetFloors(true);
                }}
                disabled={isSaving}
              >
                Zero Form
              </Button>
              <Button onClick={onSave} disabled={isSaving || isSeedError}>
                {isSaving ? "Saving..." : "Save Seed"}
              </Button>
            </div>
          </div>
        </details>

        <div className="flex justify-end">
          <Button variant="outline" onClick={onClose} disabled={isSaving}>Close</Button>
        </div>
      </Card>
    </Dialog>
  );
}

function GameIncidentModal({ incident, incidentWatersSpent, incidentBurnsTriggered, incidentWaterEarned, onAcknowledge, isAcknowledging }: {
  incident: GameIncident;
  incidentWatersSpent: number;
  incidentBurnsTriggered: number;
  incidentWaterEarned: number;
  onAcknowledge: (id: number) => void;
  isAcknowledging: boolean;
}) {
  // onClose is intentionally a no-op: Escape and backdrop clicks are disabled so
  // the incident MUST be acknowledged via the Acknowledge button. This is by design —
  // incidents require explicit user acknowledgement before the user can continue.
  return (
    <Dialog open onClose={() => {}} labelledById="game-incident-heading" describedById="game-incident-desc">
      <Card className={cn("relative w-full max-w-lg overflow-hidden border-2 animate-incident-enter", severityClass(incident))}>
        {incidentWatersSpent > 0 || incidentWaterEarned > 0 ? (
          <div className="pointer-events-none absolute inset-0">
            {Array.from({ length: Math.min(Math.max(incidentWatersSpent, incidentWaterEarned), 8) }).map((_, index) => (
              <span
                key={`water-burst-${index}`}
                className={cn(
                  "absolute block h-2.5 w-2.5 rounded-full animate-water-burst",
                  incidentWaterEarned > 0 ? "bg-cyan-400/75" : "bg-sky-400/70",
                )}
                style={{ left: `${12 + index * 10}%`, top: `${75 - (index % 2) * 20}%`, animationDelay: `${index * 60}ms` }}
              />
            ))}
          </div>
        ) : null}

        <div className="relative space-y-3">
          <div className="flex items-start gap-2">
            {incident.severity === "critical" ? (
              <AlertTriangle className="mt-0.5 h-5 w-5 text-red-700 animate-fire-fade" aria-hidden="true" />
            ) : (
              <Flame className="mt-0.5 h-5 w-5 text-amber-700 animate-fire-fade" aria-hidden="true" />
            )}
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Game Event</p>
              <h2 id="game-incident-heading" className="text-lg font-bold text-ink">{incident.title}</h2>
            </div>
          </div>

          <p id="game-incident-desc" className="text-sm text-ink/80">{incident.message}</p>

          {incidentBurnsTriggered > 0 ? (
            <p className="rounded-xl bg-red-100 px-3 py-2 text-xs font-semibold text-red-800">
              Board burn triggered. Acknowledge to continue.
            </p>
          ) : null}
          {incidentWaterEarned > 0 ? (
            <p className="rounded-xl bg-sky-100 px-3 py-2 text-xs font-semibold text-sky-800">
              Water earned! Keep correcting categories for more.
            </p>
          ) : null}

          <div className="flex items-center justify-between text-xs text-ink/60">
            <span>{formatDistanceToNow(parseApiDate(incident.created_at), { addSuffix: true })}</span>
            {incidentWaterEarned > 0 ? (
              <span className="inline-flex items-center gap-1 text-sky-700">
                <Droplets className="h-3.5 w-3.5" />
                Water earned: {incidentWaterEarned}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1">
                <Droplets className="h-3.5 w-3.5" />
                Waters spent: {incidentWatersSpent}
              </span>
            )}
          </div>

          <div className="flex justify-end">
            <Button
              className={cn(incident.severity === "critical" ? "bg-red-700 hover:bg-red-800" : undefined)}
              onClick={() => onAcknowledge(incident.id)}
              disabled={isAcknowledging}
            >
              {isAcknowledging ? "Acknowledging..." : "Acknowledge"}
            </Button>
          </div>
        </div>
      </Card>
    </Dialog>
  );
}

export function ReceiptList() {
  const router = useRouter();
  const { toast } = useToast();
  // Active queue tab — defaults to "review" so unreviewed receipts are shown first
  const [activeTab, setActiveTab] = useState<ReceiptBucket>("review");
  const [debugPanelOpen, setDebugPanelOpen] = useState(false);
  const [cardMappingPanelOpen, setCardMappingPanelOpen] = useState(false);
  const [debugResetFloors, setDebugResetFloors] = useState(true);
  // Streak milestone celebration (consistency incentive)
  const prevStreakRef = useRef<number | null>(null);
  const [celebratingStreak, setCelebratingStreak] = useState(false);
  const [debugForm, setDebugForm] = useState<DebugSeedForm>({
    enabled: false,
    water_units: 0,
    current_week_flames: 0,
    token_balance: 0,
    token_earned_count: 0,
    token_spent_count: 0,
  });
  const queryClient = useQueryClient();

  // Fetch ALL receipts — no status filter, no sort param.
  // Bucketing, sorting, and counting are done client-side via partitionReceipts.
  // The API returns all receipts with no pagination cap (see audit note: no cursor/offset).
  const receiptsQuery = useQuery({
    queryKey: ["receipts"],
    queryFn: () => listReceipts(undefined, "newest"),
    refetchInterval: 7000,
  });

  // NOTE: /stats/summary is no longer queried here — tab counts come from
  // the client-side partition of the single receipts fetch.
  // The scanMutation still invalidates ["stats"] for downstream consumers.

  const dashboardQuery = useQuery({
    queryKey: ["game-dashboard", "week", 400],
    queryFn: () => getGameDashboard("week", 400),
    refetchInterval: 10_000,
  });

  // Display name for Snappy's greetings — persistent server config (admin sets it
  // in Game settings), so it's respected on every load and on any device.
  const userName = dashboardQuery.data?.user_name ?? "";

  const incidentsQuery = useQuery({
    queryKey: ["game-incidents", "pending"],
    queryFn: () => listGameIncidents(true, 25),
    refetchInterval: 6000,
  });

  const debugToolsEnabled = dashboardQuery.data?.debug_tools_enabled ?? false;

  const debugSeedQuery = useQuery({
    queryKey: ["game-debug-seed"],
    queryFn: getGameDebugSeed,
    enabled: debugToolsEnabled && debugPanelOpen,
    staleTime: 0,
  });

  useEffect(() => {
    if (!debugToolsEnabled) {
      setDebugPanelOpen(false);
      setCardMappingPanelOpen(false);
    }
  }, [debugToolsEnabled]);

  useEffect(() => {
    if (!debugSeedQuery.data) return;
    setDebugForm({
      enabled: debugSeedQuery.data.enabled,
      water_units: debugSeedQuery.data.water_units,
      current_week_flames: debugSeedQuery.data.current_week_flames ?? 0,
      token_balance: debugSeedQuery.data.token_balance,
      token_earned_count: debugSeedQuery.data.token_earned_count,
      token_spent_count: debugSeedQuery.data.token_spent_count,
    });
  }, [debugSeedQuery.data]);

  // Scan + YNAB pull run in two modes: silent (automatic refresh — only speak
  // up when something new arrived) and manual (debug panel — always report).
  const scanMutation = useMutation({
    mutationFn: (_opts?: { silent?: boolean }) => triggerScan(),
    onSuccess: (data, opts) => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      if (data.ingested_count > 0) {
        const noun = data.ingested_count === 1 ? "receipt" : "receipts";
        toast({
          variant: "success",
          title: "New receipts!",
          message: `Found ${data.ingested_count} new ${noun} in the inbox.`,
        });
      } else if (!opts?.silent) {
        toast({
          variant: "success",
          title: "Checked the inbox",
          message: `Nothing new (${data.duplicate_count} duplicate, ${data.skipped_count} skipped).`,
        });
      }
    },
    onError: (e, opts) => {
      if (opts?.silent) return; // don't nag on automatic background refresh
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Couldn't check for new receipts" });
    },
  });

  const fetchUpdatesMutation = useMutation({
    mutationFn: (_opts?: { silent?: boolean }) => fetchYnabUpdates(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ynab-cache"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
    },
    onError: (e, opts) => {
      if (opts?.silent) return;
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Failed to fetch YNAB updates" });
    },
  });

  const rebuildMutation = useMutation({
    mutationFn: rebuildGameState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Rebuild failed" });
    },
  });

  const recomputeMutation = useMutation({
    mutationFn: recomputeCorrectnessState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Recompute failed" });
    },
  });

  // ── Automatic refresh ──────────────────────────────────────────────────
  // When the user OPENS the page (first load) or COMES BACK to a tab that's
  // been idle for a while, check the inbox + pull YNAB updates automatically.
  // A page just sitting there does nothing extra (the regular query polling
  // already keeps the list fresh); the localStorage timestamp is the
  // staleness gate, shared across tabs.
  const scanMutationRef = useRef(scanMutation);
  scanMutationRef.current = scanMutation;
  const fetchUpdatesMutationRef = useRef(fetchUpdatesMutation);
  fetchUpdatesMutationRef.current = fetchUpdatesMutation;

  useEffect(() => {
    const REFRESH_STALE_MS = 5 * 60_000;

    const maybeRefresh = () => {
      if (document.visibilityState !== "visible") return;
      let last = 0;
      try { last = Number(localStorage.getItem("snappy_last_refresh") ?? 0); } catch { /* ignore */ }
      if (Date.now() - last < REFRESH_STALE_MS) return;
      try { localStorage.setItem("snappy_last_refresh", String(Date.now())); } catch { /* ignore */ }
      if (!scanMutationRef.current.isPending) scanMutationRef.current.mutate({ silent: true });
      if (!fetchUpdatesMutationRef.current.isPending) fetchUpdatesMutationRef.current.mutate({ silent: true });
    };

    maybeRefresh(); // page open / reload
    document.addEventListener("visibilitychange", maybeRefresh);
    window.addEventListener("focus", maybeRefresh);
    return () => {
      document.removeEventListener("visibilitychange", maybeRefresh);
      window.removeEventListener("focus", maybeRefresh);
    };
  }, []);

  const shredMutation = useMutation({
    mutationFn: (receiptId: string) => shredGameReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Shred failed" });
    },
  });

  const [quickSyncingId, setQuickSyncingId] = useState<string | null>(null);
  // Receipt whose quick-sync preview dialog is open (null = closed).
  // "Looks right — Sync" NEVER fires the sync directly: it opens this preview
  // (account, date, total, category splits) and only Confirm enqueues.
  const [quickSyncPreviewId, setQuickSyncPreviewId] = useState<string | null>(null);

  // Friendly label for toasts — never show raw IDs to the user.
  const receiptLabel = (receiptId: string): string => {
    const match = (receiptsQuery.data ?? []).find((r) => r.id === receiptId);
    return match?.display_payee_name ?? match?.original_filename ?? "Receipt";
  };

  const quickSyncMutation = useMutation({
    mutationFn: (receiptId: string) => enqueueSync(receiptId),
    onSuccess: (_data, receiptId) => {
      setQuickSyncingId(null);
      setQuickSyncPreviewId(null);
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      toast({ variant: "success", title: "Sent to YNAB ✓", message: `${receiptLabel(receiptId)} is on its way.` });
    },
    onError: (e, receiptId) => {
      setQuickSyncingId(null);
      const message = e instanceof Error && e.message ? e.message : `Sync failed for ${receiptLabel(receiptId)}`;
      toast({ variant: "error", message });
    },
  });

  const restoreMutation = useMutation({
    mutationFn: (receiptId: string) => restoreReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Couldn’t restore the receipt" });
    },
  });

  const [deletingId, setDeletingId] = useState<string | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (receiptId: string) => deleteReceipt(receiptId),
    onMutate: (receiptId) => setDeletingId(receiptId),
    onSuccess: (_data, receiptId) => {
      setDeletingId(null);
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      toast({
        variant: "success",
        message: "Receipt deleted.",
        durationMs: 6000,
        action: { label: "Undo", onClick: () => restoreMutation.mutate(receiptId) },
      });
    },
    onError: (e, receiptId) => {
      setDeletingId(null);
      const message =
        e instanceof Error && e.message ? e.message : `Couldn’t delete ${receiptLabel(receiptId)}`;
      toast({ variant: "error", message });
    },
  });

  // Per-slot douse mutation: POST /api/game/water/spend { units, week_start_at }
  const douseMutation = useMutation({
    mutationFn: ({ units, week_start_at }: { units: number; week_start_at: string }) =>
      spendGameWater(units, week_start_at),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Failed to douse flames" });
    },
  });

  const acknowledgeIncidentMutation = useMutation({
    mutationFn: (incidentId: number) => acknowledgeGameIncident(incidentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Failed to acknowledge incident" });
    },
  });

  const saveDebugSeedMutation = useMutation({
    mutationFn: () =>
      updateGameDebugSeed({
        enabled: debugForm.enabled,
        water_units: debugForm.water_units,
        current_week_flames: debugForm.current_week_flames,
        token_balance: debugForm.token_balance,
        token_earned_count: debugForm.token_earned_count,
        token_spent_count: debugForm.token_spent_count,
        reset_floors_to_now: debugResetFloors,
        apply_to_live_state: true,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-debug-seed"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
      setDebugPanelOpen(false);
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Failed to save debug seed" });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      setDebugPanelOpen(false);
    },
  });

  // Client-side partition: bucket + sort in one pass.
  // Counts come from the partition (not from /stats/summary), so they stay
  // in sync with the displayed list without a second fetch.
  const { review: reviewReceipts, done: doneReceipts } = useMemo(
    () => partitionReceipts(receiptsQuery.data ?? []),
    [receiptsQuery.data],
  );

  // highlightedCount drives Snappy's pose (needs_review + duplicate_review)
  const highlightedCount = useMemo(
    () => reviewReceipts.filter(
      (r) => r.status === "needs_review" || r.status === "duplicate_review",
    ).length,
    [reviewReceipts],
  );

  const totalCount = receiptsQuery.data?.length ?? 0;

  // Streak milestone: fire once when streak crosses a milestone threshold (every 4 green weeks)
  const STREAK_MILESTONE_THRESHOLD = dashboardQuery.data?.rules?.pass_every_green_weeks ?? 4;
  useEffect(() => {
    const currentStreak = dashboardQuery.data?.momentum?.current_streak ?? 0;
    const prevStreak = prevStreakRef.current;
    if (prevStreak !== null && prevStreak !== currentStreak) {
      if (isStreakMilestone(currentStreak, STREAK_MILESTONE_THRESHOLD)) {
        setCelebratingStreak(true);
        toast({
          variant: "success",
          title: "Streak milestone!",
          message: `${currentStreak} in a row — shred token earned!`,
        });
        setTimeout(() => setCelebratingStreak(false), 1600);
      }
    }
    prevStreakRef.current = currentStreak;
  }, [dashboardQuery.data?.momentum?.current_streak, STREAK_MILESTONE_THRESHOLD, toast]);

  const tileByReceiptId = useMemo(() => {
    const map = new Map<string, GameForestTile>();
    for (const tile of dashboardQuery.data?.forest?.receipts ?? []) {
      map.set(tile.receipt_id, tile);
    }
    return map;
  }, [dashboardQuery.data?.forest?.receipts]);

  const currentWeekSlot = dashboardQuery.data?.forest?.weekly_slots?.at(-1);
  const activeIncident = incidentsQuery.data?.[0] ?? null;
  const incidentDetails = (activeIncident?.details_json ?? null) as Record<string, unknown> | null;
  const incidentWatersSpent = toInt(incidentDetails?.waters_spent);
  const incidentBurnsTriggered = toInt(incidentDetails?.burns_triggered);
  const incidentWaterEarned = activeIncident?.incident_type === "water_earned" ? toInt(incidentDetails?.units) : 0;

  return (
    <main className="relative mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 pb-24 pt-6 sm:pt-8">
      <ReceiptListHeader
        dashboardData={dashboardQuery.data}
        highlightedCount={highlightedCount}
        totalCount={totalCount}
        celebratingStreak={celebratingStreak}
        userName={userName}
        isDousingPending={douseMutation.isPending}
        onDouse={(slot) => douseMutation.mutate({ units: slot.flames, week_start_at: slot.start_at })}
      />

      <TabBar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        reviewCount={reviewReceipts.length}
        doneCount={doneReceipts.length}
        lookupSlot={
          <div className="flex items-center gap-1">
            <ReceiptLookup onNavigate={(path) => router.push(path)} />
            {debugToolsEnabled ? (
              <button
                type="button"
                data-testid="open-debug-panel"
                className="rounded-full p-2 text-ink/60 transition hover:bg-ink/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70"
                onClick={() => setDebugPanelOpen(true)}
                aria-label="Open debug panel"
                title="Debug panel"
              >
                <Wrench className="h-5 w-5" />
              </button>
            ) : null}
          </div>
        }
      />

      <section className="space-y-3" role="tabpanel" aria-label={TABS.find((t) => t.bucket === activeTab)?.label}>
        {receiptsQuery.isLoading ? <p className="text-sm text-ink/70">Loading transactions...</p> : null}

        {/* Per-tab empty states */}
        {!receiptsQuery.isLoading && activeTab === "review" && reviewReceipts.length === 0 ? (
          <Card className="flex flex-col items-center gap-3 py-8 text-center">
            <Snappy pose="asleep" size="h-20 w-20" />
            <p className="text-base font-semibold text-ink/80">All caught up!</p>
            <p className="text-sm text-ink/60">No receipts need review right now.</p>
          </Card>
        ) : null}
        {!receiptsQuery.isLoading && activeTab === "done" && doneReceipts.length === 0 ? (
          <p className="py-4 text-center text-sm text-ink/60">No synced receipts yet.</p>
        ) : null}

        {/* Active tab receipts */}
        {(activeTab === "review" ? reviewReceipts : doneReceipts).map(
          (receipt, index) => (
            <ReceiptListItem
              key={receipt.id}
              receipt={receipt}
              tile={tileByReceiptId.get(receipt.id)}
              currentWeekSlot={currentWeekSlot}
              spendableNow={Boolean(dashboardQuery.data?.momentum?.spendable_now)}
              shredWindowWeeks={dashboardQuery.data?.momentum?.shred_window_weeks ?? 1}
              onShred={(receiptId) => shredMutation.mutate(receiptId)}
              isShredPending={shredMutation.isPending}
              onQuickSync={(receiptId) => setQuickSyncPreviewId(receiptId)}
              isQuickSyncPending={quickSyncingId === receipt.id}
              onDelete={(receiptId) => deleteMutation.mutate(receiptId)}
              isDeletePending={deletingId === receipt.id}
              index={index}
            />
          ),
        )}
      </section>

      {/* Quick-sync preview — always shown before a sync fires from the list */}
      <QuickSyncPreview
        receiptId={quickSyncPreviewId}
        onClose={() => setQuickSyncPreviewId(null)}
        onConfirm={(receiptId) => {
          setQuickSyncingId(receiptId);
          quickSyncMutation.mutate(receiptId);
        }}
        isSyncing={quickSyncMutation.isPending}
      />

      {/* DebugPanel — rendered unconditionally; Dialog handles mount + restore-focus */}
      <DebugPanel
        open={debugToolsEnabled && debugPanelOpen}
        onClose={() => setDebugPanelOpen(false)}
        onScan={() => scanMutation.mutate({ silent: false })} isScanPending={scanMutation.isPending}
        onFetchUpdates={() => fetchUpdatesMutation.mutate({ silent: false })} isFetchUpdatesPending={fetchUpdatesMutation.isPending}
        onRebuild={() => rebuildMutation.mutate()} isRebuildPending={rebuildMutation.isPending}
        onRecompute={() => recomputeMutation.mutate()} isRecomputePending={recomputeMutation.isPending}
        onOpenCardMappings={() => { setDebugPanelOpen(false); setCardMappingPanelOpen(true); }}
        debugForm={debugForm}
        setDebugForm={setDebugForm}
        debugResetFloors={debugResetFloors}
        setDebugResetFloors={setDebugResetFloors}
        isSeedLoading={debugSeedQuery.isLoading}
        isSeedError={debugSeedQuery.isError}
        isSaving={saveDebugSeedMutation.isPending}
        onSave={() => saveDebugSeedMutation.mutate()}
      />

      {/* CardMappingPanel — rendered unconditionally; Dialog handles mount + restore-focus */}
      <CardMappingPanel
        open={debugToolsEnabled && cardMappingPanelOpen}
        onClose={() => setCardMappingPanelOpen(false)}
        debugToolsEnabled={debugToolsEnabled}
      />

      {/* GameIncidentModal — conditionally mount; blocking: onClose is a no-op */}
      {activeIncident ? (
        <GameIncidentModal
          incident={activeIncident}
          incidentWatersSpent={incidentWatersSpent}
          incidentBurnsTriggered={incidentBurnsTriggered}
          incidentWaterEarned={incidentWaterEarned}
          onAcknowledge={(id) => acknowledgeIncidentMutation.mutate(id)}
          isAcknowledging={acknowledgeIncidentMutation.isPending}
        />
      ) : null}
    </main>
  );
}
