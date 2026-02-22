"use client";

import { useMemo, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Circle,
  Droplets,
  Flame,
  Menu,
  RefreshCcw,
  ScanSearch,
  Scissors,
  Sparkles,
  Trophy,
  Waves,
} from "lucide-react";

import {
  fetchYnabUpdates,
  getGameDashboard,
  rebuildGameState,
  recomputeCorrectnessState,
  shredGameReceipt,
  triggerScan,
} from "@/lib/api";
import { GameDisplayState, GameForestTile, GameWindow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const SLOT_STYLE: Record<Exclude<GameDisplayState, "shredded">, string> = {
  green: "border-emerald-500/50 bg-emerald-500/20",
  yellow: "border-amber-400/60 bg-amber-400/18",
  brown: "border-stone-500/65 bg-stone-500/18",
};

function isShredEligible(tile: GameForestTile): boolean {
  return tile.display_state === "yellow" || tile.display_state === "brown";
}

function stateIcon(state: GameDisplayState) {
  if (state === "green") return <Circle className="h-3.5 w-3.5 fill-emerald-400 text-emerald-400" />;
  if (state === "yellow") return <Circle className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />;
  if (state === "brown") return <Circle className="h-3.5 w-3.5 fill-stone-500 text-stone-500" />;
  return <Circle className="h-3.5 w-3.5 fill-slate-400 text-slate-400" />;
}

function formatAgeHours(value: number): string {
  if (value < 1) return `${Math.max(Math.round(value * 60), 1)}m`;
  if (value < 24) return `${Math.round(value)}h`;
  return `${(value / 24).toFixed(1)}d`;
}

function helperText(nextTokenIn: number, threshold: number, currentStreak: number): string {
  if (currentStreak > 0 && currentStreak % threshold === 0) {
    return `Token earned. ${threshold} perfect receipts for the next token.`;
  }
  return `${nextTokenIn} more perfect receipt${nextTokenIn === 1 ? "" : "s"} to earn a shred token`;
}

function FireRow({ small, medium, large }: { small: number; medium: number; large: number }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
      <Flame className="h-3.5 w-3.5 text-red-300" />
      <span className="font-semibold">Fire queue</span>
      <div className="ml-auto flex items-center gap-2">
        {Array.from({ length: small }).map((_, i) => (
          <Flame key={`s-${i}`} className="h-3.5 w-3.5 text-red-400" />
        ))}
        {Array.from({ length: medium }).map((_, i) => (
          <Flame key={`m-${i}`} className="h-4 w-4 text-orange-400" />
        ))}
        {Array.from({ length: large }).map((_, i) => (
          <Flame key={`l-${i}`} className="h-5 w-5 text-stone-200" />
        ))}
      </div>
    </div>
  );
}

function WaterRow({ waterUnits, capacity }: { waterUnits: number; capacity: number }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-sky-400/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-100">
      <Droplets className="h-3.5 w-3.5 text-sky-300" />
      <span className="font-semibold">Water</span>
      <span className="text-sky-200">{waterUnits}/{capacity}</span>
      <div className="ml-auto flex max-w-[45%] flex-wrap items-center justify-end gap-1">
        {Array.from({ length: capacity }).map((_, index) => (
          <Waves
            key={`w-${index}`}
            className={cn(
              "h-3 w-3",
              index < waterUnits ? "text-sky-300" : "text-slate-500/50",
            )}
          />
        ))}
      </div>
    </div>
  );
}

export function GamificationDashboard() {
  const queryClient = useQueryClient();
  const [window, setWindow] = useState<GameWindow>("week");
  const [shredMode, setShredMode] = useState(false);
  const [selectedReceiptId, setSelectedReceiptId] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const dashboardQuery = useQuery({
    queryKey: ["game-dashboard", window],
    queryFn: () => getGameDashboard(window),
    refetchInterval: 10_000,
  });

  const shredMutation = useMutation({
    mutationFn: (receiptId: string) => shredGameReceipt(receiptId),
    onSuccess: () => {
      setSelectedReceiptId(null);
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
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
    mutationFn: fetchYnabUpdates,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ynab-cache"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const scanMutation = useMutation({
    mutationFn: triggerScan,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
    },
  });

  const recomputeMutation = useMutation({
    mutationFn: recomputeCorrectnessState,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
    },
  });

  const summary = dashboardQuery.data?.summary;
  const momentum = dashboardQuery.data?.momentum;
  const correctness = dashboardQuery.data?.correctness;

  const shredCandidates = useMemo(() => {
    return dashboardQuery.data?.forest.receipts.filter((tile) => isShredEligible(tile) && tile.shredded_at == null) ?? [];
  }, [dashboardQuery.data]);

  if (dashboardQuery.isError) {
    return (
      <Card>
        <p className="text-sm text-red-700">Failed to load gamification dashboard.</p>
      </Card>
    );
  }

  if (dashboardQuery.isLoading || !dashboardQuery.data || !summary || !momentum || !correctness) {
    return (
      <Card>
        <p className="text-sm text-ink/70">Loading receipt genie...</p>
      </Card>
    );
  }

  const celebrationReady = summary.total_validated > 0 && summary.total_validated % 30 === 0;

  return (
    <section className="space-y-4">
      <Card className="animate-reveal overflow-hidden bg-ink text-sand">
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.2em] text-mint">Receipt Genie</p>
              <h2 className="mt-1 text-2xl font-bold">streak: {momentum.current_streak} receipts</h2>
              <p className="mt-1 text-sm text-sand/75">{helperText(momentum.next_token_in, momentum.token_threshold, momentum.current_streak)}</p>
            </div>
            <button
              type="button"
              className="rounded-full border border-white/20 bg-white/10 p-2 text-sand transition hover:bg-white/20"
              onClick={() => setMenuOpen((current) => !current)}
              aria-label="Open game menu"
            >
              <Menu className="h-4 w-4" />
            </button>
          </div>

          <div className="flex items-center justify-between gap-2 rounded-2xl bg-white/10 px-3 py-2">
            <div>
              <p className="text-xs text-sand/70">Shred tokens</p>
              <p className="text-sm font-semibold">Balance {momentum.token_balance}</p>
            </div>
            <div className="flex items-center gap-1">
              {Array.from({ length: Math.max(momentum.token_balance, 1) }).slice(0, 8).map((_, index) => (
                <Scissors
                  key={`token-${index}`}
                  className={cn("h-4 w-4", index < momentum.token_balance ? "text-amber-300" : "text-slate-600")}
                />
              ))}
            </div>
          </div>

          {celebrationReady ? (
            <div className="rounded-xl border border-emerald-300/30 bg-emerald-500/15 px-3 py-2 text-xs text-emerald-100">
              <p className="inline-flex items-center gap-1 font-semibold">
                <Trophy className="h-3.5 w-3.5" />
                Celebration: {summary.total_validated} validations reached
              </p>
            </div>
          ) : null}

          {menuOpen ? (
            <div className="grid gap-2 rounded-2xl border border-white/15 bg-black/25 p-2 sm:grid-cols-2">
              <Button
                size="sm"
                variant="outline"
                className="h-8 border-white/30 bg-transparent text-sand hover:bg-white/10"
                onClick={() => scanMutation.mutate()}
                disabled={scanMutation.isPending}
              >
                <ScanSearch className="mr-1 h-3.5 w-3.5" />
                {scanMutation.isPending ? "Checking" : "Check ingestion queue"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 border-white/30 bg-transparent text-sand hover:bg-white/10"
                onClick={() => rebuildMutation.mutate()}
                disabled={rebuildMutation.isPending}
              >
                <RefreshCcw className="mr-1 h-3.5 w-3.5" />
                {rebuildMutation.isPending ? "Rebuilding" : "Rebuild"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 border-white/30 bg-transparent text-sand hover:bg-white/10"
                onClick={() => reconcileMutation.mutate()}
                disabled={reconcileMutation.isPending}
              >
                {reconcileMutation.isPending ? "Fetching" : "Fetch YNAB updates"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 border-white/30 bg-transparent text-sand hover:bg-white/10"
                onClick={() => recomputeMutation.mutate()}
                disabled={recomputeMutation.isPending}
              >
                {recomputeMutation.isPending ? "Recomputing" : "Recompute"}
              </Button>
            </div>
          ) : null}
        </div>
      </Card>

      <Card className="animate-reveal space-y-3 bg-slate-950 text-slate-100" style={{ animationDelay: "70ms" }}>
        <div className="flex items-center justify-between text-xs uppercase tracking-wide text-slate-300">
          <p>Oldest</p>
          <p>Week board (9 slots)</p>
          <p>Newest</p>
        </div>
        <div className="grid grid-cols-9 gap-1">
          {dashboardQuery.data.forest.weekly_slots.map((slot) => (
            <div
              key={`slot-${slot.index}`}
              className={cn(
                "h-9 rounded border",
                slot.is_empty
                  ? "border-slate-700/70 bg-transparent"
                  : slot.display_state
                    ? SLOT_STYLE[slot.display_state]
                    : "border-slate-700/70 bg-transparent",
              )}
              title={`${slot.start_at} -> ${slot.end_at} | receipts=${slot.receipt_count}`}
            />
          ))}
        </div>

        <FireRow
          small={correctness.small_fires}
          medium={correctness.medium_fires}
          large={correctness.large_fires}
        />
        <WaterRow waterUnits={correctness.water_units} capacity={correctness.water_capacity} />
      </Card>

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "120ms" }}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">Transactions</h3>
            <p className="text-xs text-ink/65">Latest validations, with shred actions for yellow/brown receipts.</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={window === "week" ? "solid" : "outline"}
              onClick={() => setWindow("week")}
            >
              Week
            </Button>
            <Button
              size="sm"
              variant={window === "month" ? "solid" : "outline"}
              onClick={() => setWindow("month")}
            >
              Month
            </Button>
            <Button
              size="sm"
              variant={shredMode ? "solid" : "outline"}
              onClick={() => {
                setShredMode((current) => !current);
                setSelectedReceiptId(null);
              }}
              disabled={!momentum.spendable_now || shredCandidates.length === 0}
            >
              <Scissors className="mr-1 h-4 w-4" />
              {shredMode ? "Cancel" : "Shred"}
            </Button>
          </div>
        </div>

        {shredMode ? (
          <div className="rounded-xl bg-amber-50 p-3 text-xs text-amber-900">
            <p>Select one eligible transaction and confirm shred.</p>
            <Button
              size="sm"
              className="mt-2"
              onClick={() => selectedReceiptId && shredMutation.mutate(selectedReceiptId)}
              disabled={!selectedReceiptId || shredMutation.isPending}
            >
              <Sparkles className="mr-1 h-4 w-4" />
              {shredMutation.isPending ? "Shredding..." : "Confirm Shred"}
            </Button>
          </div>
        ) : null}

        <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
          {dashboardQuery.data.forest.receipts.map((tile) => {
            const selectable = shredMode && isShredEligible(tile) && tile.shredded_at == null;
            const selected = selectedReceiptId === tile.receipt_id;
            return (
              <button
                key={tile.receipt_id}
                type="button"
                onClick={() => {
                  if (!selectable) return;
                  setSelectedReceiptId((current) => (current === tile.receipt_id ? null : tile.receipt_id));
                }}
                className={cn(
                  "w-full rounded-xl border px-3 py-2 text-left",
                  selected ? "border-ink ring-2 ring-ink" : "border-ink/15",
                  selectable ? "hover:bg-sand/50" : "cursor-default",
                )}
                disabled={!selectable}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="inline-flex items-center gap-2 text-sm font-semibold">
                    {stateIcon(tile.display_state)}
                    {tile.display_state.toUpperCase()}
                  </p>
                  <p className="text-xs text-ink/60">{formatAgeHours(tile.age_hours_at_validation)}</p>
                </div>
                <p className="mt-1 text-xs text-ink/60">{formatDistanceToNow(new Date(tile.validated_at), { addSuffix: true })}</p>
              </button>
            );
          })}
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <p className="rounded-lg bg-emerald-50 px-2 py-1 text-emerald-700">Green {dashboardQuery.data.forest.counts.green}</p>
          <p className="rounded-lg bg-amber-50 px-2 py-1 text-amber-700">Yellow {dashboardQuery.data.forest.counts.yellow}</p>
          <p className="rounded-lg bg-stone-200 px-2 py-1 text-stone-700">Brown {dashboardQuery.data.forest.counts.brown}</p>
          <p className="rounded-lg bg-slate-100 px-2 py-1 text-slate-700">Shredded {dashboardQuery.data.forest.counts.shredded}</p>
        </div>
      </Card>
    </section>
  );
}
