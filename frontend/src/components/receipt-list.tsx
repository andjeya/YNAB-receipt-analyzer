"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { format, formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Droplets,
  Flame,
  Menu,
  RefreshCcw,
  ScanSearch,
  Scissors,
  Sparkles,
  Waves,
} from "lucide-react";

import {
  acknowledgeGameIncident,
  fetchYnabUpdates,
  getGameDebugSeed,
  getGameDashboard,
  getStatsSummary,
  listGameIncidents,
  listReceipts,
  rebuildGameState,
  recomputeCorrectnessState,
  shredGameReceipt,
  spendGameWater,
  triggerScan,
  updateGameDebugSeed,
} from "@/lib/api";
import { GameDisplayState, GameForestTile, GameIncident, ReceiptStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { extractReceiptIdFromText } from "@/lib/receipt-id";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ReceiptStateIcon } from "@/components/receipt-state-icon";

const FILTERS: Array<{ label: string; value: "" | ReceiptStatus }> = [
  { label: "All", value: "" },
  { label: "Needs review", value: "needs_review" },
  { label: "Extracting", value: "extracting" },
  { label: "Syncing", value: "syncing" },
  { label: "Errors", value: "error_extract" },
  { label: "Synced", value: "synced" },
];

function formatAmount(milliunits: number | null): string {
  if (milliunits == null) return "--";
  return `$${Math.abs(milliunits / 1000).toFixed(2)}`;
}

function formatWaitTime(value: number | null | undefined): string {
  if (value == null) return "Not scored";
  if (value < 1) return `${Math.max(Math.round(value * 60), 1)}m`;
  if (value < 24) return `${Math.round(value)}h`;
  return `${(value / 24).toFixed(1)}d`;
}

function tokenHint(nextTokenIn: number): string {
  return `${nextTokenIn} more green receipt${nextTokenIn === 1 ? "" : "s"} for next shred token`;
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

function ActionMenu({
  menuRef, menuOpen, setMenuOpen,
  onScan, isScanPending,
  onFetchUpdates, isFetchUpdatesPending,
  onRebuild, isRebuildPending,
  onRecompute, isRecomputePending,
  debugToolsEnabled, onOpenDebugPanel,
  onNavigate,
}: {
  menuRef: { current: HTMLDivElement | null };
  menuOpen: boolean;
  setMenuOpen: (v: boolean | ((prev: boolean) => boolean)) => void;
  onScan: () => void; isScanPending: boolean;
  onFetchUpdates: () => void; isFetchUpdatesPending: boolean;
  onRebuild: () => void; isRebuildPending: boolean;
  onRecompute: () => void; isRecomputePending: boolean;
  debugToolsEnabled: boolean;
  onOpenDebugPanel: () => void;
  onNavigate: (path: string) => void;
}) {
  const [receiptLookupInput, setReceiptLookupInput] = useState("");
  const [receiptLookupError, setReceiptLookupError] = useState<string | null>(null);

  const openReceiptById = () => {
    const parsedId = extractReceiptIdFromText(receiptLookupInput.trim());
    if (!parsedId) {
      setReceiptLookupError("Enter a valid receipt ID (UUID) or memo token containing one.");
      return;
    }
    setReceiptLookupError(null);
    setMenuOpen(false);
    onNavigate(`/receipts/${parsedId}`);
  };

  return (
    <div ref={menuRef} className="absolute right-4 top-3 z-30">
      <button
        type="button"
        className="rounded-full border border-ink/20 bg-white/90 p-2 text-ink shadow-float transition hover:bg-white"
        onClick={() => setMenuOpen((current) => !current)}
        aria-label="Open actions menu"
      >
        <Menu className="h-5 w-5" />
      </button>
      {menuOpen ? (
        <Card className="absolute right-0 mt-2 w-[22rem] rounded-2xl p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Actions</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <Button variant="outline" size="sm" className="justify-start gap-1" onClick={onScan} disabled={isScanPending}>
              <ScanSearch className="h-3.5 w-3.5" />
              {isScanPending ? "Checking" : "Check ingestion queue"}
            </Button>
            <Button variant="outline" size="sm" className="justify-start gap-1" onClick={onFetchUpdates} disabled={isFetchUpdatesPending}>
              <RefreshCcw className="h-3.5 w-3.5" />
              {isFetchUpdatesPending ? "Fetching" : "Fetch YNAB updates"}
            </Button>
            <Button variant="outline" size="sm" className="justify-start gap-1" onClick={onRebuild} disabled={isRebuildPending}>
              <RefreshCcw className="h-3.5 w-3.5" />
              {isRebuildPending ? "Rebuilding" : "Rebuild game"}
            </Button>
            <Button variant="outline" size="sm" className="justify-start gap-1" onClick={onRecompute} disabled={isRecomputePending}>
              {isRecomputePending ? "Recomputing" : "Recompute correctness"}
            </Button>
            {debugToolsEnabled ? (
              <Button
                variant="outline"
                size="sm"
                className="justify-start gap-1 sm:col-span-2"
                onClick={() => { setMenuOpen(false); onOpenDebugPanel(); }}
              >
                Debug panel
              </Button>
            ) : null}
          </div>
          <div className="mt-3 border-t border-ink/10 pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Open by ID</p>
            <div className="mt-2 flex gap-2">
              <Input
                value={receiptLookupInput}
                onChange={(event) => setReceiptLookupInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") { event.preventDefault(); openReceiptById(); }
                }}
                placeholder="Receipt UUID or memo token"
                className="h-10"
              />
              <Button size="sm" onClick={openReceiptById}>Open</Button>
            </div>
            {receiptLookupError ? <p className="mt-1 text-xs text-red-700">{receiptLookupError}</p> : null}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function ReceiptListHeader({
  dashboardData, highlightedCount, maxWaterSpend, fireUnits, fireToBurn, isSpendWaterPending, onOpenWaterSpend,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  dashboardData: any;
  highlightedCount: number;
  maxWaterSpend: number;
  fireUnits: number;
  fireToBurn: number;
  isSpendWaterPending: boolean;
  onOpenWaterSpend: () => void;
}) {
  return (
    <Card className="animate-reveal space-y-3 bg-ink p-4 text-sand">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-mint">Receipt -&gt; YNAB</p>
          <h1 className="mt-1 font-[var(--font-heading)] text-3xl font-bold">Snappy</h1>
          <p className="mt-1 text-sm text-sand/80">
            {dashboardData
              ? tokenHint(dashboardData.momentum.next_token_in)
              : `${highlightedCount} receipt${highlightedCount === 1 ? "" : "s"} waiting for review`}
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-2xl bg-white/10 px-3 py-2 text-xs">
          <Scissors className="h-3.5 w-3.5 text-amber-300" />
          <span className="font-semibold">Shred tokens:</span>
          <span>{dashboardData?.momentum.token_balance ?? 0}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div className="rounded-xl bg-white/10 px-3 py-2">
          <p className="text-sand/70">Streak</p>
          <p className="mt-1 text-base font-semibold">{dashboardData?.momentum.current_streak ?? 0}</p>
        </div>
        <div className="rounded-xl bg-white/10 px-3 py-2">
          <p className="text-sand/70">Validation wait</p>
          <p className="mt-1 text-base font-semibold">{formatWaitTime(dashboardData?.summary.avg_validation_age_hours)}</p>
        </div>
        <button
          type="button"
          className={cn(
            "rounded-xl bg-white/10 px-3 py-2 text-left transition",
            maxWaterSpend > 0 ? "hover:bg-white/20" : "cursor-not-allowed opacity-80",
            isSpendWaterPending ? "animate-water-pulse" : undefined,
          )}
          onClick={onOpenWaterSpend}
          disabled={maxWaterSpend <= 0 || isSpendWaterPending}
          title={maxWaterSpend > 0 ? "Click to spend water and extinguish fire" : "No fire to extinguish"}
        >
          <p className="text-sand/70">Water</p>
          <p className="mt-1 inline-flex items-center gap-1 text-base font-semibold">
            <Waves className="h-3.5 w-3.5 text-sky-300" />
            {dashboardData ? `${dashboardData.correctness.water_units}/${dashboardData.correctness.water_capacity}` : "0/0"}
          </p>
        </button>
        <div className="rounded-xl bg-white/10 px-3 py-2">
          <p className="text-sand/70">Fire</p>
          <p className="mt-1 inline-flex items-center gap-1 text-base font-semibold">
            <Flame className="h-3.5 w-3.5 text-rose-300" />
            {fireUnits}
          </p>
          <p className="mt-1 text-[11px] text-sand/70">
            {fireToBurn > 0 ? `${fireToBurn} to burn` : "Burn threshold reached"}
          </p>
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-wide text-sand/70">
          <p>Past 9 weeks</p>
          <p>Weekly score = lowest non-shredded receipt</p>
        </div>
        <div className="grid grid-cols-9 gap-1.5 rounded-2xl bg-black/20 p-2">
          {(dashboardData?.forest.weekly_slots ?? []).map((slot: { index: number; start_at: string; end_at: string; receipt_count: number; display_state: GameDisplayState | null }) => (
            <div
              key={`week-slot-${slot.index}`}
              className="flex h-11 flex-col items-center justify-center rounded-lg bg-white/5"
              title={`${format(new Date(slot.start_at), "MMM d")} - ${format(new Date(slot.end_at), "MMM d")} | scored receipts: ${slot.receipt_count}`}
            >
              {slot.display_state ? (
                <ReceiptStateIcon tone={slot.display_state} shredded={false} className="h-[18px] w-[18px]" />
              ) : (
                <span className="h-[18px] w-[18px] rounded-full border border-sand/25" />
              )}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function FilterBar({ statusFilter, setStatusFilter, statusCounts, sortOrder, setSortOrder }: {
  statusFilter: "" | ReceiptStatus;
  setStatusFilter: (v: "" | ReceiptStatus) => void;
  statusCounts: Record<string, number>;
  sortOrder: "newest" | "oldest";
  setSortOrder: (v: "newest" | "oldest") => void;
}) {
  return (
    <section className="animate-reveal rounded-3xl bg-white/85 p-3 shadow-float" style={{ animationDelay: "90ms" }}>
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((filter) => (
          <button
            key={filter.label}
            type="button"
            onClick={() => setStatusFilter(filter.value)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-semibold transition",
              statusFilter === filter.value ? "bg-ink text-white" : "bg-ink/10 text-ink",
            )}
          >
            {filter.label}
            {filter.value ? ` (${statusCounts[filter.value] ?? 0})` : ""}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setSortOrder(sortOrder === "newest" ? "oldest" : "newest")}
          className="ml-auto rounded-full bg-ink/10 px-3 py-1 text-xs font-semibold text-ink transition hover:bg-ink/15"
        >
          Sort: {sortOrder}
        </button>
      </div>
    </section>
  );
}

function ReceiptListItem({
  receipt, tile, currentWeekSlot, spendableNow, onShred, isShredPending, index,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  receipt: any;
  tile: GameForestTile | undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  currentWeekSlot: any;
  spendableNow: boolean;
  onShred: (receiptId: string) => void;
  isShredPending: boolean;
  index: number;
}) {
  const { tone, shredded } = deriveIconState(tile);
  const correctionOpacity = receipt.correction_shade_opacity ?? 0;
  const correctionVisible = correctionOpacity > 0.01;
  const correctionColor = `rgba(15, 23, 42, ${Math.max(0.16, Math.min(0.2 + correctionOpacity * 0.75, 1))})`;

  const canShred =
    tile?.shredded_at == null &&
    (tile?.display_state === "yellow" || tile?.display_state === "brown") &&
    spendableNow &&
    Boolean(tile && currentWeekSlot && isWithinSlot(tile.validated_at, currentWeekSlot.start_at, currentWeekSlot.end_at));

  return (
    <Card
      className={cn("animate-reveal transition", receipt.status === "needs_review" ? "border-amber-300 bg-amber-50/70" : undefined)}
      style={{ animationDelay: `${120 + index * 28}ms` }}
    >
      <div className="flex items-start gap-3">
        <div className="mt-1 flex shrink-0 items-center gap-1.5">
          {correctionVisible ? (
            <span title="YNAB correction tracked">
              <Flame
                className="h-4 w-4 animate-fire-fade"
                style={{ color: correctionColor, opacity: Math.max(correctionOpacity, 0.12) }}
              />
            </span>
          ) : null}
          <div className="flex w-7 justify-center">
            {tone ? <ReceiptStateIcon tone={tone} shredded={shredded} className="h-5 w-5" /> : null}
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <Link href={`/receipts/${receipt.id}`} className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">
                {receipt.display_payee_name ?? receipt.original_filename}
              </p>
              <p className="mt-1 text-xs text-ink/65">
                {formatDistanceToNow(new Date(receipt.ingested_at), { addSuffix: true })}
              </p>
            </Link>
            <div className="text-right text-xs">
              <p className="uppercase tracking-wide text-ink/55">Validation wait</p>
              <p className="mt-1 font-semibold">{formatWaitTime(tile?.age_hours_at_validation)}</p>
            </div>
          </div>

          {receipt.correction_message ? (
            <p className="mt-1 text-[11px] font-semibold text-ink/70">{receipt.correction_message}</p>
          ) : null}

          <div className="mt-3 flex items-center justify-between gap-2">
            <p className="text-sm font-semibold">{formatAmount(receipt.display_total_milliunits)}</p>
            <div className="flex items-center gap-2">
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
              <StatusBadge status={receipt.status} />
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

function WaterSpendModal({ waterSpendAmount, setWaterSpendAmount, maxWaterSpend, onSpend, isSpendPending, onClose }: {
  waterSpendAmount: number;
  setWaterSpendAmount: (v: number) => void;
  maxWaterSpend: number;
  onSpend: (units: number) => void;
  isSpendPending: boolean;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[65] flex items-center justify-center bg-black/45 px-4">
      <Card className="w-full max-w-sm space-y-3 animate-incident-enter">
        <h2 className="text-base font-semibold">Spend Water</h2>
        <p className="text-sm text-ink/70">Choose how much water to spend to extinguish fire.</p>
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/60">Amount</label>
          <Input
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
    </div>
  );
}

function DebugPanel({ debugForm, setDebugForm, debugResetFloors, setDebugResetFloors, isSeedLoading, isSeedError, isSaving, onSave, onClose }: {
  debugForm: DebugSeedForm;
  setDebugForm: (v: DebugSeedForm) => void;
  debugResetFloors: boolean;
  setDebugResetFloors: (v: boolean) => void;
  isSeedLoading: boolean;
  isSeedError: boolean;
  isSaving: boolean;
  onSave: () => void;
  onClose: () => void;
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

  return (
    <div className="fixed inset-0 z-[66] flex items-center justify-center bg-black/55 px-4">
      <Card className="w-full max-w-lg space-y-3 animate-incident-enter">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Debug Seed Panel</h2>
          <span className="text-xs text-ink/60">Enabled via terminal toggle</span>
        </div>
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
          <Button variant="outline" onClick={onClose} disabled={isSaving}>Close</Button>
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
      </Card>
    </div>
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
  return (
    <div
      className={cn(
        "fixed inset-0 z-[70] flex items-center justify-center px-4",
        incident.severity === "critical" ? "bg-red-950/70 animate-burn-flash" : "bg-black/45",
      )}
    >
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
              <AlertTriangle className="mt-0.5 h-5 w-5 text-red-700 animate-fire-fade" />
            ) : (
              <Flame className="mt-0.5 h-5 w-5 text-amber-700 animate-fire-fade" />
            )}
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Game Event</p>
              <h2 className="text-lg font-bold text-ink">{incident.title}</h2>
            </div>
          </div>

          <p className="text-sm text-ink/80">{incident.message}</p>

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
    </div>
  );
}

export function ReceiptList() {
  const router = useRouter();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"" | ReceiptStatus>("");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const [receiptLookupInput, setReceiptLookupInput] = useState("");
  const [receiptLookupError, setReceiptLookupError] = useState<string | null>(null);
  const [waterSpendOpen, setWaterSpendOpen] = useState(false);
  const [waterSpendAmount, setWaterSpendAmount] = useState(1);
  const [debugPanelOpen, setDebugPanelOpen] = useState(false);
  const [debugResetFloors, setDebugResetFloors] = useState(true);
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

  const receiptsQuery = useQuery({
    queryKey: ["receipts", statusFilter, sortOrder],
    queryFn: () => listReceipts(statusFilter || undefined, sortOrder),
    refetchInterval: 7000,
  });

  const statsQuery = useQuery({
    queryKey: ["stats"],
    queryFn: getStatsSummary,
    refetchInterval: 12000,
  });

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
    if (!menuOpen) return;

    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!debugToolsEnabled) {
      setDebugPanelOpen(false);
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

  const scanMutation = useMutation({
    mutationFn: triggerScan,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      setMenuOpen(false);
    },
  });

  const fetchUpdatesMutation = useMutation({
    mutationFn: fetchYnabUpdates,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ynab-cache"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
      setMenuOpen(false);
    },
  });

  const rebuildMutation = useMutation({
    mutationFn: rebuildGameState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      setMenuOpen(false);
    },
  });

  const recomputeMutation = useMutation({
    mutationFn: recomputeCorrectnessState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
      setMenuOpen(false);
    },
  });

  const shredMutation = useMutation({
    mutationFn: (receiptId: string) => shredGameReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const spendWaterMutation = useMutation({
    mutationFn: (units: number) => spendGameWater(units),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["game-incidents"] });
      setWaterSpendOpen(false);
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
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      setDebugPanelOpen(false);
    },
  });

  const statusCounts = statsQuery.data?.status_counts ?? {};

  const highlightedCount = useMemo(() => {
    return receiptsQuery.data?.filter((receipt) => receipt.status === "needs_review").length ?? 0;
  }, [receiptsQuery.data]);

  const tileByReceiptId = useMemo(() => {
    const map = new Map<string, GameForestTile>();
    for (const tile of dashboardQuery.data?.forest.receipts ?? []) {
      map.set(tile.receipt_id, tile);
    }
    return map;
  }, [dashboardQuery.data?.forest.receipts]);

  const currentWeekSlot = dashboardQuery.data?.forest.weekly_slots[dashboardQuery.data.forest.weekly_slots.length - 1];
  const activeIncident = incidentsQuery.data?.[0] ?? null;
  const incidentDetails = (activeIncident?.details_json ?? null) as Record<string, unknown> | null;
  const incidentWatersSpent = toInt(incidentDetails?.waters_spent);
  const incidentBurnsTriggered = toInt(incidentDetails?.burns_triggered);
  const incidentWaterEarned = activeIncident?.incident_type === "water_earned" ? toInt(incidentDetails?.units) : 0;

  const waterUnits = dashboardQuery.data?.correctness.water_units ?? 0;
  const fireUnits = dashboardQuery.data?.correctness.fire_units ?? 0;
  const fireToBurn = dashboardQuery.data?.correctness.fires_to_burn ?? Math.max((dashboardQuery.data?.rules.fire_burn_threshold ?? 0) - fireUnits, 0);
  const maxWaterSpend = Math.min(waterUnits, fireUnits);

  const openReceiptById = () => {
    const parsedId = extractReceiptIdFromText(receiptLookupInput.trim());
    if (!parsedId) {
      setReceiptLookupError("Enter a valid receipt ID (UUID) or memo token containing one.");
      return;
    }
    setReceiptLookupError(null);
    setMenuOpen(false);
    router.push(`/receipts/${parsedId}`);
  };

  return (
    <main className="relative mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 pb-24 pt-14">
      <ActionMenu
        menuRef={menuRef}
        menuOpen={menuOpen}
        setMenuOpen={setMenuOpen}
        onScan={() => scanMutation.mutate()} isScanPending={scanMutation.isPending}
        onFetchUpdates={() => fetchUpdatesMutation.mutate()} isFetchUpdatesPending={fetchUpdatesMutation.isPending}
        onRebuild={() => rebuildMutation.mutate()} isRebuildPending={rebuildMutation.isPending}
        onRecompute={() => recomputeMutation.mutate()} isRecomputePending={recomputeMutation.isPending}
        debugToolsEnabled={debugToolsEnabled}
        onOpenDebugPanel={() => setDebugPanelOpen(true)}
        onNavigate={(path) => router.push(path)}
      />

      <ReceiptListHeader
        dashboardData={dashboardQuery.data}
        highlightedCount={highlightedCount}
        maxWaterSpend={maxWaterSpend}
        fireUnits={fireUnits}
        fireToBurn={fireToBurn}
        isSpendWaterPending={spendWaterMutation.isPending}
        onOpenWaterSpend={() => {
          if (maxWaterSpend <= 0) return;
          setWaterSpendAmount(Math.min(1, maxWaterSpend) || 1);
          setWaterSpendOpen(true);
        }}
      />

      <FilterBar
        statusFilter={statusFilter}
        setStatusFilter={setStatusFilter}
        statusCounts={statusCounts}
        sortOrder={sortOrder}
        setSortOrder={setSortOrder}
      />

      <section className="space-y-3">
        {receiptsQuery.isLoading ? <p className="text-sm text-ink/70">Loading transactions...</p> : null}
        {receiptsQuery.data?.length === 0 ? (
          <Card>
            <p className="text-sm text-ink/70">No receipts found yet. Drop files into your ingest folder.</p>
          </Card>
        ) : null}
        {receiptsQuery.data?.map((receipt, index) => (
          <ReceiptListItem
            key={receipt.id}
            receipt={receipt}
            tile={tileByReceiptId.get(receipt.id)}
            currentWeekSlot={currentWeekSlot}
            spendableNow={Boolean(dashboardQuery.data?.momentum.spendable_now)}
            onShred={(receiptId) => shredMutation.mutate(receiptId)}
            isShredPending={shredMutation.isPending}
            index={index}
          />
        ))}
      </section>

      {waterSpendOpen ? (
        <WaterSpendModal
          waterSpendAmount={waterSpendAmount}
          setWaterSpendAmount={setWaterSpendAmount}
          maxWaterSpend={maxWaterSpend}
          onSpend={(units) => spendWaterMutation.mutate(units)}
          isSpendPending={spendWaterMutation.isPending}
          onClose={() => setWaterSpendOpen(false)}
        />
      ) : null}

      {debugToolsEnabled && debugPanelOpen ? (
        <DebugPanel
          debugForm={debugForm}
          setDebugForm={setDebugForm}
          debugResetFloors={debugResetFloors}
          setDebugResetFloors={setDebugResetFloors}
          isSeedLoading={debugSeedQuery.isLoading}
          isSeedError={debugSeedQuery.isError}
          isSaving={saveDebugSeedMutation.isPending}
          onSave={() => saveDebugSeedMutation.mutate()}
          onClose={() => setDebugPanelOpen(false)}
        />
      ) : null}

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
