import { cn } from "@/lib/utils";

// Each status carries a chip style (tinted fill + inset ring for a crisp edge)
// and a matching dot colour, so state is legible at a glance even before the
// label is read.
const statusStyles: Record<string, string> = {
  ingested: "bg-slate-100 text-slate-700 ring-slate-600/15",
  extracting: "bg-blue-100 text-blue-700 ring-blue-600/15",
  needs_review: "bg-amber-100 text-amber-800 ring-amber-600/20",
  duplicate_review: "bg-orange-100 text-orange-800 ring-orange-600/20",
  syncing: "bg-teal-100 text-teal-700 ring-teal-600/15",
  synced: "bg-emerald-100 text-emerald-700 ring-emerald-600/15",
  error_extract: "bg-red-100 text-red-700 ring-red-600/20",
  error_sync: "bg-red-100 text-red-700 ring-red-600/20",
};

const statusDot: Record<string, string> = {
  ingested: "bg-slate-400",
  extracting: "bg-blue-500 animate-pulse",
  needs_review: "bg-amber-500",
  duplicate_review: "bg-orange-500",
  syncing: "bg-teal-500 animate-pulse",
  synced: "bg-emerald-500",
  error_extract: "bg-red-500",
  error_sync: "bg-red-500",
};

// Plain-language labels — the raw status enums (e.g. "error_extract") are
// developer vocabulary and never shown to the user.
const statusLabels: Record<string, string> = {
  ingested: "In line",
  extracting: "Reading…",
  needs_review: "Needs review",
  duplicate_review: "Possible duplicate",
  syncing: "Sending…",
  synced: "Synced",
  error_extract: "Couldn't read it",
  error_sync: "Sync hiccup",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset",
        statusStyles[status] ?? "bg-slate-100 text-slate-700 ring-slate-600/15",
      )}
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", statusDot[status] ?? "bg-slate-400")} aria-hidden="true" />
      {statusLabels[status] ?? status.replace(/_/g, " ")}
    </span>
  );
}
