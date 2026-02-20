"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { format, formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Flame,
  Menu,
  RefreshCcw,
  ScanSearch,
  Scissors,
  Sparkles,
  Waves,
} from "lucide-react";

import {
  getGameDashboard,
  getStatsSummary,
  listReceipts,
  rebuildGameState,
  reconcileGameState,
  recomputeCorrectnessState,
  refreshYnabCache,
  shredGameReceipt,
  triggerScan,
} from "@/lib/api";
import { GameDisplayState, GameForestTile, ReceiptStatus } from "@/lib/types";
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

export function ReceiptList() {
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"" | ReceiptStatus>("");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const [receiptLookupInput, setReceiptLookupInput] = useState("");
  const [receiptLookupError, setReceiptLookupError] = useState<string | null>(null);
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

  const scanMutation = useMutation({
    mutationFn: triggerScan,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
    },
  });

  const cacheMutation = useMutation({
    mutationFn: refreshYnabCache,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ynab-cache"] });
    },
  });

  const rebuildMutation = useMutation({
    mutationFn: rebuildGameState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
    },
  });

  const reconcileMutation = useMutation({
    mutationFn: reconcileGameState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
    },
  });

  const recomputeMutation = useMutation({
    mutationFn: recomputeCorrectnessState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
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
  }, [dashboardQuery.data]);

  const currentWeekSlot = dashboardQuery.data?.forest.weekly_slots[dashboardQuery.data.forest.weekly_slots.length - 1];

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
      <div className="absolute right-4 top-3 z-30">
        <button
          type="button"
          className="rounded-full border border-ink/20 bg-white/90 p-2 text-ink shadow-float transition hover:bg-white"
          onClick={() => setMenuOpen((current) => !current)}
          aria-label="Open actions menu"
        >
          <Menu className="h-5 w-5" />
        </button>
        {menuOpen ? (
          <Card className="absolute right-0 mt-2 w-[20rem] rounded-2xl p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Actions</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <Button
                variant="outline"
                size="sm"
                className="justify-start gap-1"
                onClick={() => scanMutation.mutate()}
                disabled={scanMutation.isPending}
              >
                <ScanSearch className="h-3.5 w-3.5" />
                {scanMutation.isPending ? "Scanning" : "Scan folder"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start gap-1"
                onClick={() => cacheMutation.mutate()}
                disabled={cacheMutation.isPending}
              >
                <RefreshCcw className="h-3.5 w-3.5" />
                {cacheMutation.isPending ? "Refreshing" : "Refresh cache"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start gap-1"
                onClick={() => rebuildMutation.mutate()}
                disabled={rebuildMutation.isPending}
              >
                <RefreshCcw className="h-3.5 w-3.5" />
                {rebuildMutation.isPending ? "Rebuilding" : "Rebuild game"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start gap-1"
                onClick={() => reconcileMutation.mutate()}
                disabled={reconcileMutation.isPending}
              >
                {reconcileMutation.isPending ? "Reconciling" : "Reconcile"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start gap-1 sm:col-span-2"
                onClick={() => recomputeMutation.mutate()}
                disabled={recomputeMutation.isPending}
              >
                {recomputeMutation.isPending ? "Recomputing" : "Recompute correctness"}
              </Button>
            </div>
            <div className="mt-3 border-t border-ink/10 pt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Open by ID</p>
              <div className="mt-2 flex gap-2">
                <Input
                  value={receiptLookupInput}
                  onChange={(event) => setReceiptLookupInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      openReceiptById();
                    }
                  }}
                  placeholder="Receipt UUID or memo token"
                  className="h-10"
                />
                <Button size="sm" onClick={openReceiptById}>
                  Open
                </Button>
              </div>
              {receiptLookupError ? <p className="mt-1 text-xs text-red-700">{receiptLookupError}</p> : null}
            </div>
          </Card>
        ) : null}
      </div>

      <Card className="animate-reveal space-y-3 bg-ink p-4 text-sand">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-mint">Receipt -&gt; YNAB</p>
            <h1 className="mt-1 font-[var(--font-heading)] text-3xl font-bold">Snappy</h1>
            <p className="mt-1 text-sm text-sand/80">
              {dashboardQuery.data
                ? tokenHint(dashboardQuery.data.momentum.next_token_in)
                : `${highlightedCount} receipt${highlightedCount === 1 ? "" : "s"} waiting for review`}
            </p>
          </div>
          <div className="flex items-center gap-1 rounded-2xl bg-white/10 px-3 py-2 text-xs">
            <Scissors className="h-3.5 w-3.5 text-amber-300" />
            <span className="font-semibold">Shred tokens:</span>
            <span>{dashboardQuery.data?.momentum.token_balance ?? 0}</span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div className="rounded-xl bg-white/10 px-3 py-2">
            <p className="text-sand/70">Streak</p>
            <p className="mt-1 text-base font-semibold">{dashboardQuery.data?.momentum.current_streak ?? 0}</p>
          </div>
          <div className="rounded-xl bg-white/10 px-3 py-2">
            <p className="text-sand/70">Validation wait</p>
            <p className="mt-1 text-base font-semibold">{formatWaitTime(dashboardQuery.data?.summary.avg_validation_age_hours)}</p>
          </div>
          <div className="rounded-xl bg-white/10 px-3 py-2">
            <p className="text-sand/70">Water</p>
            <p className="mt-1 inline-flex items-center gap-1 text-base font-semibold">
              <Waves className="h-3.5 w-3.5 text-sky-300" />
              {dashboardQuery.data ? `${dashboardQuery.data.correctness.water_units}/${dashboardQuery.data.correctness.water_capacity}` : "0/0"}
            </p>
          </div>
          <div className="rounded-xl bg-white/10 px-3 py-2">
            <p className="text-sand/70">Fire</p>
            <p className="mt-1 inline-flex items-center gap-1 text-base font-semibold">
              <Flame className="h-3.5 w-3.5 text-rose-300" />
              {dashboardQuery.data?.correctness.fire_units ?? 0}
            </p>
          </div>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-wide text-sand/70">
            <p>Past 9 weeks</p>
            <p>Weekly score = lowest non-shredded receipt</p>
          </div>
          <div className="grid grid-cols-9 gap-1.5 rounded-2xl bg-black/20 p-2">
            {(dashboardQuery.data?.forest.weekly_slots ?? []).map((slot) => (
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
            onClick={() => setSortOrder((current) => (current === "newest" ? "oldest" : "newest"))}
            className="ml-auto rounded-full bg-ink/10 px-3 py-1 text-xs font-semibold text-ink transition hover:bg-ink/15"
          >
            Sort: {sortOrder}
          </button>
        </div>
      </section>

      <section className="space-y-3">
        {receiptsQuery.isLoading ? <p className="text-sm text-ink/70">Loading transactions...</p> : null}
        {receiptsQuery.data?.length === 0 ? (
          <Card>
            <p className="text-sm text-ink/70">No receipts found yet. Drop files into your ingest folder.</p>
          </Card>
        ) : null}

        {receiptsQuery.data?.map((receipt, index) => {
          const tile = tileByReceiptId.get(receipt.id);
          const { tone, shredded } = deriveIconState(tile);

          const canShred =
            tile?.shredded_at == null &&
            (tile?.display_state === "yellow" || tile?.display_state === "brown") &&
            Boolean(dashboardQuery.data?.momentum.spendable_now) &&
            Boolean(
              tile &&
                currentWeekSlot &&
                isWithinSlot(tile.validated_at, currentWeekSlot.start_at, currentWeekSlot.end_at),
            );

          return (
            <Card
              key={receipt.id}
              className={cn(
                "animate-reveal transition",
                receipt.status === "needs_review" ? "border-amber-300 bg-amber-50/70" : undefined,
              )}
              style={{
                animationDelay: `${120 + index * 28}ms`,
                backgroundColor:
                  receipt.correction_shade_opacity && receipt.correction_shade_opacity > 0
                    ? `rgba(15, 23, 42, ${Math.min(0.12 + receipt.correction_shade_opacity * 0.45, 0.58)})`
                    : undefined,
                color: receipt.correction_shade_opacity && receipt.correction_shade_opacity > 0.45 ? "#f8fafc" : undefined,
              }}
            >
              <div className="flex items-start gap-3">
                <div className="mt-1 flex w-7 shrink-0 justify-center">
                  {tone ? <ReceiptStateIcon tone={tone} shredded={shredded} className="h-5 w-5" /> : null}
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
                    <p className="mt-1 text-[11px] font-semibold text-black/80">{receipt.correction_message}</p>
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
                          onClick={() => shredMutation.mutate(receipt.id)}
                          disabled={shredMutation.isPending}
                        >
                          <Scissors className="h-3.5 w-3.5" />
                          {shredMutation.isPending ? "Shredding..." : "Shred"}
                        </Button>
                      ) : null}
                      <StatusBadge status={receipt.status} />
                    </div>
                  </div>
                </div>
              </div>
            </Card>
          );
        })}
      </section>
    </main>
  );
}
