"use client";

import { useMemo, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Flame, RotateCcw, Scissors, Sparkles, Target } from "lucide-react";

import { getGameDashboard, rebuildGameState, shredGameReceipt } from "@/lib/api";
import { GameDisplayState, GameForestTile, GameWindow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const TILE_STYLE: Record<GameDisplayState, string> = {
  green: "border-emerald-400/70 bg-emerald-100 text-emerald-900 shadow-[inset_0_-2px_0_rgba(6,95,70,0.12)]",
  yellow: "border-amber-300/70 bg-amber-100 text-amber-900 rotate-[1.4deg] saturate-75",
  brown: "border-amber-900/50 bg-stone-300 text-stone-700 rotate-[-2.1deg] opacity-90",
  shredded: "border-slate-300 bg-slate-100 text-slate-500",
};

function isShredEligible(tile: GameForestTile): boolean {
  return tile.display_state === "yellow" || tile.display_state === "brown";
}

function formatAgeHours(value: number): string {
  if (value < 1) return `${Math.max(Math.round(value * 60), 1)}m`;
  if (value < 24) return `${Math.round(value)}h`;
  return `${(value / 24).toFixed(1)}d`;
}

function helperText(nextTokenIn: number, threshold: number, currentStreak: number): string {
  if (currentStreak > 0 && currentStreak % threshold === 0) {
    return `Token earned. ${threshold} greens for the next one.`;
  }
  return `${nextTokenIn} more green${nextTokenIn === 1 ? "" : "s"} to earn a shred token`;
}

export function GamificationDashboard() {
  const queryClient = useQueryClient();
  const [window, setWindow] = useState<GameWindow>("week");
  const [shredMode, setShredMode] = useState(false);
  const [selectedReceiptId, setSelectedReceiptId] = useState<string | null>(null);

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

  const summary = dashboardQuery.data?.summary;
  const momentum = dashboardQuery.data?.momentum;

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

  if (dashboardQuery.isLoading || !dashboardQuery.data || !summary || !momentum) {
    return (
      <Card>
        <p className="text-sm text-ink/70">Loading momentum graph...</p>
      </Card>
    );
  }

  return (
    <section className="space-y-4">
      <Card className="animate-reveal overflow-hidden bg-ink text-sand">
        <div className="grid gap-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.2em] text-mint">Current Momentum</p>
              <h2 className="mt-1 flex items-center gap-2 font-[var(--font-heading)] text-2xl font-bold">
                <Flame className="h-6 w-6 text-ember" /> {momentum.current_streak} Green Streak
              </h2>
              <p className="mt-1 text-sm text-sand/75">
                {helperText(momentum.next_token_in, momentum.token_threshold, momentum.current_streak)}
              </p>
            </div>
            <div className="rounded-xl bg-white/10 px-3 py-2 text-right">
              <p className="text-xs text-sand/65">Max</p>
              <p className="text-lg font-semibold">{momentum.max_streak}</p>
              <Button
                size="sm"
                variant="outline"
                className="mt-2 h-8 border-white/30 bg-transparent px-2 text-[11px] text-sand hover:bg-white/10"
                onClick={() => rebuildMutation.mutate()}
                disabled={rebuildMutation.isPending}
                title="Replay history for deterministic state rebuild"
              >
                <RotateCcw className="mr-1 h-3.5 w-3.5" />
                {rebuildMutation.isPending ? "Rebuilding" : "Rebuild"}
              </Button>
            </div>
          </div>

          <div className="rounded-2xl bg-white/10 p-3">
            <div className="flex items-center justify-between text-xs">
              <p className="inline-flex items-center gap-1 text-sand/75">
                <Scissors className="h-3.5 w-3.5" /> Shred Tokens
              </p>
              <p className="font-semibold text-sand">
                {momentum.token_progress_current}/{momentum.token_threshold}
              </p>
            </div>
            <div className="mt-2 h-2 rounded-full bg-sand/20">
              <div
                className="h-full rounded-full bg-mint transition-all duration-300"
                style={{ width: `${Math.max((momentum.token_progress_current / momentum.token_threshold) * 100, 8)}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between">
              <p className="text-xs text-sand/75">Balance: {momentum.token_balance}</p>
              <p className="text-xs text-sand/75">
                Earned {momentum.token_earned_count}, Spent {momentum.token_spent_count}
              </p>
            </div>
          </div>
        </div>
      </Card>

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "80ms" }}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">Forest Grid</h3>
            <p className="text-xs text-ink/65">Latest receipts at the top. Green means under 24h validation.</p>
          </div>
          <Button
            size="sm"
            variant={shredMode ? "solid" : "outline"}
            className="gap-2"
            onClick={() => {
              setShredMode((current) => !current);
              setSelectedReceiptId(null);
            }}
            disabled={!momentum.spendable_now || shredCandidates.length === 0}
          >
            <Scissors className="h-4 w-4" />
            {shredMode ? "Cancel" : "Shred Mode"}
          </Button>
        </div>

        {shredMode ? (
          <div className="rounded-xl bg-amber-50 p-3 text-xs text-amber-800">
            <p>Select one yellow/brown receipt, then confirm.</p>
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                className="gap-2"
                onClick={() => selectedReceiptId && shredMutation.mutate(selectedReceiptId)}
                disabled={!selectedReceiptId || shredMutation.isPending}
              >
                <Sparkles className="h-4 w-4" />
                {shredMutation.isPending ? "Shredding..." : "Confirm Shred"}
              </Button>
              <p className="self-center text-xs text-amber-700">
                {shredCandidates.length} eligible, {momentum.token_balance} token{momentum.token_balance === 1 ? "" : "s"} available
              </p>
            </div>
          </div>
        ) : null}

        <div className="grid grid-cols-5 gap-2 sm:grid-cols-6 md:grid-cols-7">
          {dashboardQuery.data.forest.receipts.map((tile, index) => {
            const selectable = shredMode && isShredEligible(tile) && tile.shredded_at == null;
            const selected = selectedReceiptId === tile.receipt_id;

            return (
              <button
                key={tile.receipt_id}
                type="button"
                className={cn(
                  "group relative rounded-xl border px-2 py-2 text-left transition",
                  TILE_STYLE[tile.display_state],
                  tile.is_latest ? "ring-2 ring-ink/40" : "ring-0",
                  selectable ? "cursor-pointer hover:-translate-y-0.5 hover:shadow-md" : "cursor-default",
                  selected ? "ring-2 ring-ink" : "",
                )}
                style={{ animationDelay: `${100 + index * 10}ms` }}
                onClick={() => {
                  if (!selectable) return;
                  setSelectedReceiptId((current) => (current === tile.receipt_id ? null : tile.receipt_id));
                }}
                disabled={!selectable}
              >
                <p className="text-[10px] font-semibold uppercase tracking-wide">{tile.display_state}</p>
                <p className="mt-1 text-[11px] opacity-80">{formatAgeHours(tile.age_hours_at_validation)}</p>
                <p className="mt-1 text-[10px] opacity-60">
                  {formatDistanceToNow(new Date(tile.validated_at), { addSuffix: true })}
                </p>
                {tile.display_state === "shredded" ? <p className="mt-1 text-[10px]">shredded</p> : null}
              </button>
            );
          })}
        </div>
        {dashboardQuery.data.forest.receipts.length === 0 ? (
          <p className="rounded-xl bg-slate-100 px-3 py-2 text-xs text-slate-700">
            No validated receipts yet. Validate a receipt to seed the forest.
          </p>
        ) : null}

        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <p className="rounded-lg bg-emerald-50 px-2 py-1 text-emerald-700">Green {dashboardQuery.data.forest.counts.green}</p>
          <p className="rounded-lg bg-amber-50 px-2 py-1 text-amber-700">Yellow {dashboardQuery.data.forest.counts.yellow}</p>
          <p className="rounded-lg bg-stone-200 px-2 py-1 text-stone-700">Brown {dashboardQuery.data.forest.counts.brown}</p>
          <p className="rounded-lg bg-slate-100 px-2 py-1 text-slate-700">Shredded {dashboardQuery.data.forest.counts.shredded}</p>
        </div>
      </Card>

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "140ms" }}>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">Summary + Challenges</h3>
          <div className="inline-flex rounded-full border border-ink/15 bg-white p-1">
            <button
              type="button"
              className={cn(
                "rounded-full px-3 py-1 text-xs font-semibold",
                window === "week" ? "bg-ink text-white" : "text-ink/70",
              )}
              onClick={() => setWindow("week")}
            >
              Week
            </button>
            <button
              type="button"
              className={cn(
                "rounded-full px-3 py-1 text-xs font-semibold",
                window === "month" ? "bg-ink text-white" : "text-ink/70",
              )}
              onClick={() => setWindow("month")}
            >
              Month
            </button>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="rounded-xl bg-emerald-50 p-2 text-emerald-800">
            <p className="opacity-80">Green %</p>
            <p className="mt-1 text-base font-semibold">{summary.green_percent.toFixed(1)}%</p>
          </div>
          <div className="rounded-xl bg-slate-100 p-2 text-slate-800">
            <p className="opacity-80">Avg validation age</p>
            <p className="mt-1 text-base font-semibold">
              {summary.avg_validation_age_hours == null ? "--" : `${summary.avg_validation_age_hours.toFixed(1)}h`}
            </p>
          </div>
          <div className="rounded-xl bg-sand p-2 text-ink">
            <p className="opacity-80">Total validated</p>
            <p className="mt-1 text-base font-semibold">{summary.total_validated}</p>
          </div>
        </div>

        <div className="space-y-2">
          {dashboardQuery.data.challenges.map((challenge) => (
            <div key={challenge.key} className="rounded-2xl border border-ink/10 bg-white/80 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold">{challenge.title}</p>
                  <p className="text-xs text-ink/65">{challenge.description}</p>
                </div>
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide",
                    challenge.status === "completed" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700",
                  )}
                >
                  <Target className="h-3 w-3" />
                  {challenge.status === "completed" ? "Done" : "In progress"}
                </span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-ink/10">
                <div className="h-full rounded-full bg-ink transition-all" style={{ width: `${Math.min(challenge.progress * 100, 100)}%` }} />
              </div>
              <p className="mt-1 text-xs text-ink/70">
                {challenge.current.toFixed(1)} / {challenge.target.toFixed(1)} {challenge.unit}
              </p>
            </div>
          ))}
        </div>
      </Card>
    </section>
  );
}
