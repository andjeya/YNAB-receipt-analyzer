"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { format, formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Droplets,
  Flame,
  HelpCircle,
  Scissors,
  Search,
  Sparkles,
  Trash2,
  Waves,
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
} from "@/lib/api";
import { GameDisplayState, GameForestTile, GameIncident } from "@/lib/types";
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
import { ReceiptStateIcon } from "@/components/receipt-state-icon";
import { Snappy } from "@/components/snappy/snappy";
import { CardMappingPanel } from "@/components/card-mapping-panel";
import { SyncPreviewDialog } from "@/components/sync-preview-dialog";
import { toDraftFromPayload } from "@/lib/validation-draft";

// Tab definitions — each maps to a ReceiptBucket (see src/lib/receipt-buckets.ts).
// Processing receipts live at the bottom of To Review with a "working" label,
// so there are only two places to look: what needs you, and what's done.
const TABS: Array<{ label: string; bucket: ReceiptBucket; testid: string }> = [
  { label: "To Review", bucket: "review", testid: "tab-review" },
  { label: "Done",      bucket: "done",   testid: "tab-done"   },
];

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
 * Formats the wall-clock time since a receipt was ingested as a short
 * "waiting" string (e.g. "3d", "5h", "12m") for the To Review tab.
 */
function formatWallWait(ingestedAt: string): string {
  const elapsedMs = Date.now() - new Date(ingestedAt).getTime();
  const totalMinutes = Math.max(Math.round(elapsedMs / 60_000), 1);
  const totalHours = elapsedMs / (1000 * 60 * 60);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  if (totalHours < 24) return `${Math.round(totalHours)}h`;
  return `${Math.max(Math.round(totalHours / 24), 1)}d`;
}

function deriveIconState(
  tile: GameForestTile | undefined,
): { tone: Exclude<GameDisplayState, "shredded"> | null; shredded: boolean } {
  if (!tile) return { tone: null, shredded: false };
  if (tile.display_state === "shredded") {
    return { tone: tile.state, shredded: true };
  }
  if (tile.display_state === "green" || tile.display_state === "yellow" || tile.display_state === "brown") {
    return { tone: tile.display_state, shredded: false };
  }
  return { tone: null, shredded: false };
}

function isWithinSlot(isoTimestamp: string, slotStart: string, slotEnd: string): boolean {
  const ts = new Date(isoTimestamp).getTime();
  return ts >= new Date(slotStart).getTime() && ts < new Date(slotEnd).getTime();
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
  fire_units: number;
  burn_count: number;
  token_balance: number;
  token_earned_count: number;
  token_spent_count: number;
  current_streak: number;
  max_streak: number;
  active_streak_group_id: number;
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
        className="rounded-full p-2 text-ink/60 transition hover:bg-ink/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label="Find a receipt"
        title="Find a receipt"
      >
        <Search className="h-5 w-5" />
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
type StatTileId = "streak" | "wait" | "water" | "fire";

const STAT_TILE_TOOLTIPS: Record<Exclude<StatTileId, "water">, string> = {
  streak: "Streak: consecutive weeks with at least one synced receipt. Milestones every 5 earn a shred token.",
  wait: "Average hours between a receipt landing in the inbox and you reviewing it. Lower is better — affects your weekly score.",
  fire: "Fire: earned when you wait too long to review receipts. Too much fire burns your weekly score.",
};

function StatTilePopover({ text, id }: { text: string; id: string }) {
  return (
    <div
      role="status"
      id={id}
      className="absolute left-0 top-full z-20 mt-1 w-max max-w-[16rem] rounded-xl bg-ink/95 px-3 py-2 text-[11px] leading-relaxed text-sand shadow-float"
    >
      {text}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// "How scoring works" dialog content
// ─────────────────────────────────────────────────────────────────────────────

function HowScoringWorksDialog({
  open,
  onClose,
  avgValidationAgeHours,
}: {
  open: boolean;
  onClose: () => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  avgValidationAgeHours: any;
}) {
  return (
    <Dialog open={open} onClose={onClose} labelledById="how-scoring-heading">
      <Card className="w-full max-w-sm space-y-4 border-0 shadow-none">
        <h2 id="how-scoring-heading" className="text-base font-semibold">How it works</h2>

        <section className="space-y-2 text-sm text-ink/80">
          <p className="font-semibold text-ink">Weekly score</p>
          <p>
            Each week gets a colour based on your <em>slowest</em> receipt that wasn&apos;t shredded.
            Review within roughly 24 hours and the week goes <span className="font-semibold text-emerald-600">green</span>.
            A little later and it&apos;s <span className="font-semibold text-yellow-600">yellow</span>.
            Leave it too long and it turns <span className="font-semibold text-amber-800">brown</span>.
          </p>
        </section>

        <section className="space-y-2 text-sm text-ink/80">
          <p className="font-semibold text-ink">Water drops</p>
          <p>
            Every time you fix a category in YNAB after syncing, you earn water drops.
            Spend them to put out fires before they burn your score.
          </p>
        </section>

        <section className="space-y-2 text-sm text-ink/80">
          <p className="font-semibold text-ink">Fire &amp; burn</p>
          <p>
            Fire builds up when receipts sit unreviewed. Once fire crosses the threshold,
            it burns — turning a past week brown. Use water early to prevent this.
          </p>
        </section>

        <section className="space-y-2 text-sm text-ink/80">
          <p className="font-semibold text-ink">Shred tokens</p>
          <p>
            Hit a streak milestone (every 5 weeks in a row) and you earn a shred token.
            Use it to shred a late or very-late receipt — it won&apos;t count against your weekly score.
          </p>
        </section>

        <section className="space-y-2 text-sm text-ink/80">
          <p className="font-semibold text-ink">Streak</p>
          <p>
            A streak counts consecutive weeks where you reviewed at least one receipt.
            Keep it going to reach milestones and earn shred tokens.
          </p>
        </section>

        <section className="rounded-xl bg-ink/5 px-3 py-2 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/50">Your average review time</p>
          <p className="mt-1 text-lg font-bold text-ink">
            {avgValidationAgeHours == null ? "No reviews yet" : formatWaitTime(avgValidationAgeHours)}
          </p>
          <p className="text-xs text-ink/60">Average time between a receipt arriving and you reviewing it.</p>
        </section>

        <div className="flex justify-end">
          <Button variant="outline" onClick={onClose}>Got it</Button>
        </div>
      </Card>
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Journey-path week board
// ─────────────────────────────────────────────────────────────────────────────

type WeekSlot = {
  index: number;
  start_at: string;
  end_at: string;
  receipt_count: number;
  display_state: GameDisplayState | null;
};

// State-colour map for the path nodes (matches TONE_STYLES in receipt-state-icon.tsx)
const NODE_FILL: Record<Exclude<GameDisplayState, null>, string> = {
  green:    "#34d399",
  yellow:   "#facc15",
  brown:    "#a16207",
  shredded: "#a16207",
};

function JourneyPathBoard({ slots }: { slots: WeekSlot[] }) {
  const [openNode, setOpenNode] = useState<number | null>(null);

  // Close on Escape
  useEffect(() => {
    if (openNode === null) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") setOpenNode(null); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [openNode]);

  // Close on outside click
  const boardRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (openNode === null) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (boardRef.current && !boardRef.current.contains(e.target as Node)) setOpenNode(null);
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [openNode]);

  return (
    <div ref={boardRef} className="relative">
      {/* Connecting track line */}
      <div className="absolute left-[calc(100%/18)] right-[calc(100%/18)] top-1/2 -translate-y-1/2 h-0.5 bg-white/20 rounded-full" aria-hidden="true" />

      <div className="relative flex items-center justify-between gap-0">
        {slots.map((slot, arrayIndex) => {
          const isCurrentWeek = arrayIndex === slots.length - 1;
          const receiptsPart = slot.receipt_count === 0 ? "no receipts yet" : `${slot.receipt_count} receipt${slot.receipt_count === 1 ? "" : "s"} scored`;
          const slotLabel = `${isCurrentWeek ? "Current week — " : ""}${format(new Date(slot.start_at), "MMM d")} - ${format(new Date(slot.end_at), "MMM d")} · ${receiptsPart}`;
          const hasState = slot.display_state !== null;
          const fill = hasState ? NODE_FILL[slot.display_state!] : undefined;
          const isOpen = openNode === arrayIndex;

          return (
            <div key={`week-slot-${slot.index}`} className="relative flex flex-col items-center">
              <button
                type="button"
                onClick={() => setOpenNode((prev) => (prev === arrayIndex ? null : arrayIndex))}
                aria-describedby={isOpen ? `week-node-popover-${arrayIndex}` : undefined}
                className={cn(
                  "relative flex items-center justify-center rounded-full border-2 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink",
                  isCurrentWeek
                    ? "h-10 w-10 border-mint/70 bg-ink animate-current-week-pulse"
                    : "h-7 w-7 border-sand/20",
                  hasState ? "border-transparent" : "border-dashed border-sand/30 bg-white/5",
                )}
                style={hasState ? { backgroundColor: fill, borderColor: fill } : undefined}
                role="img"
                aria-label={slotLabel}
                title={slotLabel}
              >
                {slot.display_state === "shredded" ? (
                  <ReceiptStateIcon tone="brown" shredded className={isCurrentWeek ? "h-5 w-5" : "h-3.5 w-3.5"} />
                ) : hasState ? (
                  <span
                    className={cn("rounded-full bg-white/30", isCurrentWeek ? "h-3 w-3" : "h-2 w-2")}
                    aria-hidden="true"
                  />
                ) : null}
              </button>

              {/* Node popover */}
              {isOpen ? (
                <div
                  role="status"
                  id={`week-node-popover-${arrayIndex}`}
                  className="absolute bottom-full left-1/2 z-20 mb-2 w-max max-w-[13rem] -translate-x-1/2 rounded-xl bg-ink/95 px-3 py-2 text-[11px] leading-relaxed text-sand shadow-float"
                >
                  {slotLabel}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-[10px] text-sand/60">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: "#34d399" }} />
          On time
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: "#facc15" }} />
          Late
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: "#a16207" }} />
          Very late
        </span>
        <span className="flex items-center gap-1">
          <ReceiptStateIcon tone="brown" shredded className="h-2.5 w-2.5" />
          Shredded
        </span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main header component
// ─────────────────────────────────────────────────────────────────────────────

function ReceiptListHeader({
  dashboardData, highlightedCount, totalCount, maxWaterSpend, fireUnits, fireToBurn, isSpendWaterPending, onOpenWaterSpend,
  celebratingStreak, userName,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  dashboardData: any;
  highlightedCount: number;
  totalCount: number;
  maxWaterSpend: number;
  fireUnits: number;
  fireToBurn: number;
  isSpendWaterPending: boolean;
  onOpenWaterSpend: () => void;
  celebratingStreak: boolean;
  userName: string;
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
  const derived = useMemo(
    () =>
      deriveSnappyPose({
        needsReviewCount: highlightedCount,
        totalCount: isEmpty ? 0 : 1,
        userName,
        ...(mounted ? {} : { random: () => 0.5, now: new Date(2026, 0, 1, 12, 0, 0) }),
      }),
    [mounted, highlightedCount, isEmpty, userName],
  );
  const pose = celebratingStreak ? "celebrating" : derived.pose;

  // Which stat chip currently has its popover open (null = none)
  const [openTile, setOpenTile] = useState<StatTileId | null>(null);

  // "How scoring works" dialog
  const [scoringOpen, setScoringOpen] = useState(false);

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

  const streak = dashboardData?.momentum?.current_streak ?? 0;
  const waterUnitsVal = dashboardData?.correctness?.water_units ?? 0;
  const waterCapacity = dashboardData?.correctness?.water_capacity ?? 0;

  const weekSlots: WeekSlot[] = dashboardData?.forest?.weekly_slots ?? [];

  // ── Chip render helpers ────────────────────────────────────────────────────

  const streakLabel = streak === 0 ? "start a streak" : "week streak";
  const fireLabel = fireToBurn === 0 ? "nothing to burn \u{1F389}" : `${fireToBurn} to burn`;

  return (
    <>
      {/* visually-hidden page h1 for a11y; visible eyebrow below */}
      <h1 className="sr-only">Snappy — Receipt to YNAB</h1>

      <Card
        className="animate-reveal overflow-hidden rounded-3xl p-0 text-sand"
        style={{ background: "linear-gradient(135deg, #172026 0%, #0e2a2f 60%, #0d2535 100%)" }}
      >
        {/* ── Top section: always visible ────────────────────────────────── */}
        <div className="flex items-center gap-3 px-4 pt-4 pb-3">
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
                  <p className="mt-1 text-[11px] text-sand/60" title={derived.attributionSource}>
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
            className="ml-auto shrink-0 rounded-full p-1.5 text-sand/60 transition hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-mint/70 sm:hidden"
          >
            {isExpanded ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
          </button>
        </div>

        {/* ── Expandable section: chips + journey path ───────────────────── */}
        <div className={cn("px-4 pb-4 space-y-4", isExpanded ? "block" : "hidden sm:block")}>
          {/* Stat chips row */}
          <div ref={headerRef} className="flex flex-wrap gap-2">

            {/* Streak chip */}
            <div className="relative">
              <button
                type="button"
                onClick={() => toggleTile("streak")}
                aria-describedby={openTile === "streak" ? "tile-popover-streak" : undefined}
                data-testid="stat-tile-streak"
                className="flex items-center gap-2 rounded-2xl px-3 py-2 transition hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
                style={{ background: "linear-gradient(135deg, #f59e0b 0%, #f97316 100%)" }}
              >
                <Flame className="h-5 w-5 text-white/90" aria-hidden="true" />
                <div className="text-left">
                  <p className="text-xl font-bold leading-none text-white">{streak}</p>
                  <p className="mt-0.5 text-[11px] font-medium text-white/80">{streakLabel}</p>
                </div>
              </button>
              {openTile === "streak" ? (
                <StatTilePopover id="tile-popover-streak" text={STAT_TILE_TOOLTIPS.streak} />
              ) : null}
            </div>

            {/* Water chip */}
            <div className="relative">
              <button
                type="button"
                onClick={onOpenWaterSpend}
                disabled={maxWaterSpend <= 0 || isSpendWaterPending}
                aria-label={maxWaterSpend > 0 ? "Click to spend water and extinguish fire" : "No fire to extinguish"}
                data-testid="stat-tile-water"
                className={cn(
                  "flex items-center gap-2 rounded-2xl px-3 py-2 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink",
                  maxWaterSpend > 0 ? "hover:brightness-110" : "cursor-not-allowed opacity-70",
                  isSpendWaterPending ? "animate-water-pulse" : undefined,
                )}
                style={{ background: "linear-gradient(135deg, #0ea5e9 0%, #14b8a6 100%)" }}
              >
                <Waves className="h-5 w-5 text-white/90" aria-hidden="true" />
                <div className="text-left">
                  <p className="text-xl font-bold leading-none text-white">
                    {waterUnitsVal}
                    <span className="text-sm font-normal text-white/70">/{waterCapacity}</span>
                  </p>
                  <p className="mt-0.5 text-[11px] font-medium text-white/80">water saved</p>
                </div>
              </button>
              {/* Info affordance */}
              <button
                type="button"
                className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-ink/60 text-[10px] font-bold text-sand/80 hover:bg-ink/80 focus-visible:ring-2 focus-visible:ring-mint/70"
                onClick={(e) => { e.stopPropagation(); toggleTile("water"); }}
                aria-label="Water tile info"
                aria-describedby={openTile === "water" ? "tile-popover-water" : undefined}
                data-testid="stat-tile-water-info"
              >
                ?
              </button>
              {openTile === "water" ? (
                <StatTilePopover
                  id="tile-popover-water"
                  text={maxWaterSpend > 0 ? "Tap the tile to spend water and extinguish fire. Water is earned by correcting categories in YNAB." : "Water: earned by correcting categories in YNAB. Spend water to extinguish fire."}
                />
              ) : null}
            </div>

            {/* Fire chip */}
            <div className="relative">
              <button
                type="button"
                onClick={() => toggleTile("fire")}
                aria-describedby={openTile === "fire" ? "tile-popover-fire" : undefined}
                data-testid="stat-tile-fire"
                className="flex items-center gap-2 rounded-2xl px-3 py-2 transition hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
                style={{ background: "linear-gradient(135deg, #e11d48 0%, #f43f5e 100%)" }}
              >
                <Flame className="h-5 w-5 text-white/90" aria-hidden="true" />
                <div className="text-left">
                  <p className="text-xl font-bold leading-none text-white">{fireUnits}</p>
                  <p className="mt-0.5 text-[11px] font-medium text-white/80">{fireLabel}</p>
                </div>
              </button>
              {openTile === "fire" ? (
                <StatTilePopover id="tile-popover-fire" text={STAT_TILE_TOOLTIPS.fire} />
              ) : null}
            </div>

            {/* Shred tokens pill */}
            <div className="flex items-center gap-1.5 rounded-2xl bg-white/10 px-3 py-2">
              <Scissors className="h-4 w-4 text-amber-300" aria-hidden="true" />
              <div className="text-left">
                <p className="text-xl font-bold leading-none text-white">{dashboardData?.momentum?.token_balance ?? 0}</p>
                <p className="mt-0.5 text-[11px] font-medium text-sand/70">shred tokens</p>
              </div>
            </div>
          </div>

          {/* Journey path section */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-sand/60">Past 9 weeks</p>
              <button
                type="button"
                onClick={() => setScoringOpen(true)}
                className="flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-semibold text-sand/70 transition hover:bg-white/20 focus-visible:ring-2 focus-visible:ring-mint/70"
                aria-label="How scoring works"
              >
                <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
                How it works
              </button>
            </div>

            <JourneyPathBoard slots={weekSlots} />
          </div>
        </div>
      </Card>

      <HowScoringWorksDialog
        open={scoringOpen}
        onClose={() => setScoringOpen(false)}
        avgValidationAgeHours={dashboardData?.summary?.avg_validation_age_hours}
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
    <section className="animate-reveal flex items-center gap-2 rounded-3xl bg-white/85 p-3 shadow-float" style={{ animationDelay: "90ms" }}>
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
                "rounded-full px-4 py-1.5 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2",
                isActive
                  ? "bg-ink text-white shadow-sm"
                  : "bg-ink/10 text-ink hover:bg-ink/15",
              )}
            >
              {tab.label}
              {" "}
              <span
                className={cn(
                  "inline-flex min-w-[1.25rem] items-center justify-center rounded-full px-1 text-xs font-bold",
                  isActive ? "bg-white/20" : "bg-ink/10",
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
  receipt, tile, currentWeekSlot, spendableNow, onShred, isShredPending, onQuickSync, isQuickSyncPending, onDelete, isDeletePending, index, showWaiting,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  receipt: any;
  tile: GameForestTile | undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  currentWeekSlot: any;
  spendableNow: boolean;
  onShred: (receiptId: string) => void;
  isShredPending: boolean;
  onQuickSync: (receiptId: string) => void;
  isQuickSyncPending: boolean;
  onDelete: (receiptId: string) => void;
  isDeletePending: boolean;
  index: number;
  /** When true (To Review tab), show a prominent "Xd waiting" label. */
  showWaiting?: boolean;
}) {
  const { tone, shredded } = deriveIconState(tile);
  const isProcessing = isProcessingStatus(receipt.status);
  const correctionOpacity = receipt.correction_shade_opacity ?? 0;
  const correctionVisible = correctionOpacity > 0.01;
  const correctionColor = `rgba(15, 23, 42, ${Math.max(0.16, Math.min(0.2 + correctionOpacity * 0.75, 1))})`;

  const canShred =
    tile?.shredded_at == null &&
    (tile?.display_state === "yellow" || tile?.display_state === "brown") &&
    spendableNow &&
    Boolean(tile && currentWeekSlot && isWithinSlot(tile.validated_at, currentWeekSlot.start_at, currentWeekSlot.end_at));

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
          <div className="mt-1 flex shrink-0 items-center gap-1.5">
            {correctionVisible ? (
              <span title="YNAB correction tracked" aria-label="YNAB correction tracked">
                <Flame
                  className="h-4 w-4 animate-fire-fade"
                  style={{ color: correctionColor, opacity: Math.max(correctionOpacity, 0.12) }}
                  aria-hidden="true"
                />
              </span>
            ) : null}
            <div className="flex w-7 justify-center">
              {tone ? <ReceiptStateIcon tone={tone} shredded={shredded} className={cn("h-5 w-5", sproutClass)} /> : null}
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">
                  {receipt.display_payee_name ?? receipt.original_filename}
                </p>
                {isProcessing ? (
                  <p className="mt-1 text-xs font-medium text-ink/55">
                    Snappy is working on this one — no action needed
                  </p>
                ) : showWaiting ? (
                  <p className="mt-1 text-xs font-semibold text-amber-700">
                    {formatWallWait(receipt.ingested_at)} waiting
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-ink/65">
                    {formatDistanceToNow(new Date(receipt.ingested_at), { addSuffix: true })}
                  </p>
                )}
              </div>
              {(tile?.age_hours_at_validation != null || !showWaiting) ? (
                <div className="text-right text-xs">
                  <p className="uppercase tracking-wide text-ink/70">Review time</p>
                  <p className="mt-1 font-semibold">{formatWaitTime(tile?.age_hours_at_validation)}</p>
                </div>
              ) : null}
            </div>

            {receipt.correction_message ? (
              <p className="mt-1 text-[11px] font-semibold text-ink/70">{receipt.correction_message}</p>
            ) : null}

            <div className="mt-3 flex items-center justify-between gap-2">
              {(() => {
                const kind = receipt.transaction_kind ?? "purchase";
                const millis = receipt.display_total_milliunits;
                if (millis == null) return <p className="text-sm font-semibold">--</p>;
                const dollars = signedDollars(millis / 1000, kind);
                const formatted = formatSignedDollars(dollars);
                const isRefund = kind === "refund";
                return (
                  <p className={cn("text-sm font-semibold", isRefund ? "text-emerald-700" : undefined)}>
                    {formatted}
                  </p>
                );
              })()}
              <div className="relative z-10 flex items-center gap-2">
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
                    className="h-8 gap-1 bg-mint text-white hover:bg-mint/90"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onQuickSync(receipt.id);
                    }}
                    disabled={isQuickSyncPending}
                  >
                    {isQuickSyncPending ? "Syncing…" : "Looks right — Sync"}
                  </Button>
                ) : null}
                {receipt.status !== "synced" && receipt.status !== "syncing" ? (
                  <button
                    type="button"
                    data-testid="delete-receipt-button"
                    aria-label="Delete receipt"
                    title="Delete receipt"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full text-ink/40 opacity-70 transition hover:bg-red-50 hover:text-red-600 hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 disabled:opacity-40"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onDelete(receipt.id);
                    }}
                    disabled={isDeletePending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                ) : null}
                <StatusBadge status={receipt.status} />
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
  const latestSync = receipt.latest_sync;
  const lastDryRunTransaction: Record<string, unknown> | null = (() => {
    if (!latestSync || latestSync.status !== "dry_run" || !latestSync.raw_request) return null;
    const txn = (latestSync.raw_request as Record<string, unknown>).transaction;
    return txn && typeof txn === "object" ? (txn as Record<string, unknown>) : null;
  })();
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
      lastDryRunTransaction={lastDryRunTransaction}
      isConfirmDisabled={false}
      isSyncing={isSyncing}
      dateTimeConfirmed={receipt.latest_twin?.confirmed_sections.date_time ?? false}
      totalConfirmed={receipt.latest_twin?.confirmed_sections.total ?? false}
      onConfirm={() => onConfirm(receipt.id)}
      showSkipPreviewOption={false}
    />
  );
}

function WaterSpendModal({ open, waterSpendAmount, setWaterSpendAmount, maxWaterSpend, onSpend, isSpendPending, onClose }: {
  open: boolean;
  waterSpendAmount: number;
  setWaterSpendAmount: (v: number) => void;
  maxWaterSpend: number;
  onSpend: (units: number) => void;
  isSpendPending: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} labelledById="water-spend-heading">
      <Card className="w-full max-w-sm space-y-3 animate-incident-enter border-0 shadow-none">
        <h2 id="water-spend-heading" className="text-base font-semibold">Spend Water</h2>
        <p className="text-sm text-ink/70">Choose how much water to spend to extinguish fire.</p>
        <div>
          <label htmlFor="water-spend-amount" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Amount</label>
          <Input
            id="water-spend-amount"
            type="number"
            min={1}
            max={Math.max(maxWaterSpend, 1)}
            value={waterSpendAmount}
            onChange={(event) => {
              const next = Number(event.target.value) || 1;
              setWaterSpendAmount(Math.max(1, Math.min(next, Math.max(maxWaterSpend, 1))));
            }}
          />
          <p className="mt-1 text-xs text-ink/60">Max now: {maxWaterSpend}</p>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={isSpendPending}>Cancel</Button>
          <Button onClick={() => onSpend(waterSpendAmount)} disabled={isSpendPending || maxWaterSpend <= 0}>
            {isSpendPending ? "Spending..." : "Extinguish"}
          </Button>
        </div>
      </Card>
    </Dialog>
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
  userName, onUserNameChange,
  onScan, isScanPending,
  onFetchUpdates, isFetchUpdatesPending,
  onRebuild, isRebuildPending,
  onRecompute, isRecomputePending,
  onOpenCardMappings,
  debugForm, setDebugForm, debugResetFloors, setDebugResetFloors, isSeedLoading, isSeedError, isSaving, onSave,
}: {
  open: boolean;
  onClose: () => void;
  userName: string;
  onUserNameChange: (name: string) => void;
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

        {/* ── Profile ─────────────────────────────────────────────────── */}
        <section className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/50">Profile</p>
          <label htmlFor="debug-user-name" className="block text-sm font-semibold text-ink">
            Your name
          </label>
          <Input
            id="debug-user-name"
            value={userName}
            onChange={(event) => onUserNameChange(event.target.value)}
            placeholder="Anna"
            className="h-10"
          />
          <p className="text-xs text-ink/60">Snappy uses this to say hi. (Stored on this device only.)</p>
        </section>

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
              {numField("water_units", "Water")}
              {numField("fire_units", "Fire")}
              {numField("burn_count", "Burn Count")}
              {numField("token_balance", "Token Balance")}
              {numField("token_earned_count", "Token Earned")}
              {numField("token_spent_count", "Token Spent")}
              {numField("current_streak", "Current Streak")}
              {numField("max_streak", "Max Streak")}
              {numField("active_streak_group_id", "Streak Group", 1)}
            </div>

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
                  setDebugForm({ enabled: false, water_units: 0, fire_units: 0, burn_count: 0, token_balance: 0, token_earned_count: 0, token_spent_count: 0, current_streak: 0, max_streak: 0, active_streak_group_id: 1 });
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
            <span>{formatDistanceToNow(new Date(incident.created_at), { addSuffix: true })}</span>
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
  // Display name for Snappy's greetings — single-user for now, set in the
  // debug panel and stored on-device. Defaults to "Anna".
  const [userName, setUserName] = useState("Anna");
  useEffect(() => {
    try {
      const stored = localStorage.getItem("snappy_user_name");
      if (stored) setUserName(stored);
    } catch { /* ignore */ }
  }, []);
  const handleUserNameChange = (name: string) => {
    setUserName(name);
    try { localStorage.setItem("snappy_user_name", name); } catch { /* ignore */ }
  };
  const [waterSpendOpen, setWaterSpendOpen] = useState(false);
  const [waterSpendAmount, setWaterSpendAmount] = useState(1);
  const [debugPanelOpen, setDebugPanelOpen] = useState(false);
  const [cardMappingPanelOpen, setCardMappingPanelOpen] = useState(false);
  const [debugResetFloors, setDebugResetFloors] = useState(true);
  // Streak milestone celebration (consistency incentive)
  const prevStreakRef = useRef<number | null>(null);
  const [celebratingStreak, setCelebratingStreak] = useState(false);
  const [debugForm, setDebugForm] = useState<DebugSeedForm>({
    enabled: false,
    water_units: 0,
    fire_units: 0,
    burn_count: 0,
    token_balance: 0,
    token_earned_count: 0,
    token_spent_count: 0,
    current_streak: 0,
    max_streak: 0,
    active_streak_group_id: 1,
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
      fire_units: debugSeedQuery.data.fire_units,
      burn_count: debugSeedQuery.data.burn_count,
      token_balance: debugSeedQuery.data.token_balance,
      token_earned_count: debugSeedQuery.data.token_earned_count,
      token_spent_count: debugSeedQuery.data.token_spent_count,
      current_streak: debugSeedQuery.data.current_streak,
      max_streak: debugSeedQuery.data.max_streak,
      active_streak_group_id: debugSeedQuery.data.active_streak_group_id,
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

  const spendWaterMutation = useMutation({
    mutationFn: (units: number) => spendGameWater(units),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
      setWaterSpendOpen(false);
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Failed to spend water" });
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
        fire_units: debugForm.fire_units,
        burn_count: debugForm.burn_count,
        token_balance: debugForm.token_balance,
        token_earned_count: debugForm.token_earned_count,
        token_spent_count: debugForm.token_spent_count,
        current_streak: debugForm.current_streak,
        max_streak: debugForm.max_streak,
        active_streak_group_id: debugForm.active_streak_group_id,
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

  // Streak milestone: fire once when streak crosses a milestone threshold (every 5)
  const STREAK_MILESTONE_THRESHOLD = 5;
  useEffect(() => {
    const currentStreak = dashboardQuery.data?.momentum?.current_streak ?? 0;
    const prevStreak = prevStreakRef.current;
    if (prevStreak !== null && prevStreak !== currentStreak) {
      if (isStreakMilestone(currentStreak, STREAK_MILESTONE_THRESHOLD)) {
        setCelebratingStreak(true);
        toast({
          variant: "success",
          title: "Streak milestone!",
          message: `${currentStreak} in a row — shred pass earned.`,
        });
        setTimeout(() => setCelebratingStreak(false), 1600);
      }
    }
    prevStreakRef.current = currentStreak;
  }, [dashboardQuery.data?.momentum?.current_streak, toast]);

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

  const waterUnits = dashboardQuery.data?.correctness?.water_units ?? 0;
  const fireUnits = dashboardQuery.data?.correctness?.fire_units ?? 0;
  const fireToBurn = dashboardQuery.data?.correctness?.fires_to_burn ?? Math.max((dashboardQuery.data?.rules?.fire_burn_threshold ?? 0) - fireUnits, 0);
  const maxWaterSpend = Math.min(waterUnits, fireUnits);

  return (
    <main className="relative mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 pb-24 pt-6">
      <ReceiptListHeader
        dashboardData={dashboardQuery.data}
        highlightedCount={highlightedCount}
        totalCount={totalCount}
        maxWaterSpend={maxWaterSpend}
        fireUnits={fireUnits}
        fireToBurn={fireToBurn}
        isSpendWaterPending={spendWaterMutation.isPending}
        onOpenWaterSpend={() => {
          if (maxWaterSpend <= 0) return;
          setWaterSpendAmount(Math.min(1, maxWaterSpend) || 1);
          setWaterSpendOpen(true);
        }}
        celebratingStreak={celebratingStreak}
        userName={userName}
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
              onShred={(receiptId) => shredMutation.mutate(receiptId)}
              isShredPending={shredMutation.isPending}
              onQuickSync={(receiptId) => setQuickSyncPreviewId(receiptId)}
              isQuickSyncPending={quickSyncingId === receipt.id}
              onDelete={(receiptId) => deleteMutation.mutate(receiptId)}
              isDeletePending={deletingId === receipt.id}
              index={index}
              showWaiting={activeTab === "review"}
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

      {/* WaterSpendModal — rendered unconditionally; Dialog handles mount + restore-focus */}
      <WaterSpendModal
        open={waterSpendOpen}
        waterSpendAmount={waterSpendAmount}
        setWaterSpendAmount={setWaterSpendAmount}
        maxWaterSpend={maxWaterSpend}
        onSpend={(units) => spendWaterMutation.mutate(units)}
        isSpendPending={spendWaterMutation.isPending}
        onClose={() => setWaterSpendOpen(false)}
      />

      {/* DebugPanel — rendered unconditionally; Dialog handles mount + restore-focus */}
      <DebugPanel
        open={debugToolsEnabled && debugPanelOpen}
        onClose={() => setDebugPanelOpen(false)}
        userName={userName}
        onUserNameChange={handleUserNameChange}
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
