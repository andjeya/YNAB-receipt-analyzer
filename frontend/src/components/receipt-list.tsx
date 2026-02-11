"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCcw, ScanSearch } from "lucide-react";

import { getStatsSummary, listReceipts, refreshYnabCache, triggerScan } from "@/lib/api";
import { ReceiptStatus } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/badge";

const FILTERS: Array<{ label: string; value: "" | ReceiptStatus }> = [
  { label: "All", value: "" },
  { label: "Needs review", value: "needs_review" },
  { label: "Extracting", value: "extracting" },
  { label: "Syncing", value: "syncing" },
  { label: "Errors", value: "error_extract" },
  { label: "Synced", value: "synced" },
];

function formatAmount(milliunits: number | null): string {
  if (milliunits == null) {
    return "--";
  }
  return `$${Math.abs(milliunits / 1000).toFixed(2)}`;
}

export function ReceiptList() {
  const [statusFilter, setStatusFilter] = useState<"" | ReceiptStatus>("");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
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

  const scanMutation = useMutation({
    mutationFn: triggerScan,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const cacheMutation = useMutation({
    mutationFn: refreshYnabCache,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ynab-cache"] });
    },
  });

  const statusCounts = statsQuery.data?.status_counts ?? {};

  const highlightedCount = useMemo(() => {
    return receiptsQuery.data?.filter((receipt) => receipt.status === "needs_review").length ?? 0;
  }, [receiptsQuery.data]);

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 pb-24 pt-5">
      <header className="animate-reveal rounded-3xl bg-ink p-4 text-sand shadow-float">
        <p className="text-xs uppercase tracking-[0.2em] text-mint">Receipt -> YNAB</p>
        <h1 className="mt-1 font-[var(--font-heading)] text-2xl font-bold">Review Queue</h1>
        <p className="mt-1 text-sm text-sand/80">
          {highlightedCount} receipt{highlightedCount === 1 ? "" : "s"} waiting for review
        </p>
        <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
          <div className="rounded-xl bg-sand/10 p-2">
            <p className="text-sand/70">Extract</p>
            <p className="mt-1 text-base font-semibold">{Math.round(statsQuery.data?.avg_extraction_duration_ms ?? 0)} ms</p>
          </div>
          <div className="rounded-xl bg-sand/10 p-2">
            <p className="text-sand/70">Validate</p>
            <p className="mt-1 text-base font-semibold">{Math.round(statsQuery.data?.avg_validation_duration_ms ?? 0)} ms</p>
          </div>
          <div className="rounded-xl bg-sand/10 p-2">
            <p className="text-sand/70">Age</p>
            <p className="mt-1 text-base font-semibold">{Math.round((statsQuery.data?.avg_receipt_age_at_validation_ms ?? 0) / 1000)} s</p>
          </div>
        </div>
      </header>

      <section className="animate-reveal rounded-3xl bg-white/85 p-3 shadow-float" style={{ animationDelay: "100ms" }}>
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((filter) => (
            <button
              key={filter.label}
              type="button"
              onClick={() => setStatusFilter(filter.value)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                statusFilter === filter.value ? "bg-ink text-white" : "bg-ink/10 text-ink"
              }`}
            >
              {filter.label}
              {filter.value ? ` (${statusCounts[filter.value] ?? 0})` : ""}
            </button>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => scanMutation.mutate()}
            disabled={scanMutation.isPending}
            className="gap-2"
          >
            <ScanSearch className="h-4 w-4" />
            Scan Folder
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => cacheMutation.mutate()}
            disabled={cacheMutation.isPending}
            className="gap-2"
          >
            <RefreshCcw className="h-4 w-4" />
            Refresh YNAB Cache
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSortOrder((current) => (current === "newest" ? "oldest" : "newest"))}
          >
            Sort: {sortOrder}
          </Button>
        </div>
      </section>

      <section className="space-y-3">
        {receiptsQuery.isLoading ? <p className="text-sm">Loading receipts...</p> : null}
        {receiptsQuery.data?.length === 0 ? (
          <Card>
            <p className="text-sm text-ink/70">No receipts found yet. Drop files into your ingest folder.</p>
          </Card>
        ) : null}

        {receiptsQuery.data?.map((receipt, index) => (
          <Link key={receipt.id} href={`/receipts/${receipt.id}`}>
            <Card
              className={`animate-reveal transition hover:-translate-y-0.5 ${
                receipt.status === "needs_review" ? "border-amber-400 bg-amber-50" : ""
              }`}
              style={{ animationDelay: `${120 + index * 35}ms` }}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">{receipt.display_payee_name ?? receipt.original_filename}</p>
                  <p className="mt-1 text-xs text-ink/70">
                    {formatDistanceToNow(new Date(receipt.ingested_at), { addSuffix: true })}
                  </p>
                </div>
                <StatusBadge status={receipt.status} />
              </div>
              <div className="mt-3 flex items-center justify-between text-sm">
                <p className="font-semibold">{formatAmount(receipt.display_total_milliunits)}</p>
                <p className="text-xs uppercase tracking-wide text-ink/60">
                  {receipt.status === "needs_review" ? "Needs review" : "Open"}
                </p>
              </div>
            </Card>
          </Link>
        ))}
      </section>
    </main>
  );
}
