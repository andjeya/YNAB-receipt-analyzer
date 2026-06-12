import { cn } from "@/lib/utils";

const statusStyles: Record<string, string> = {
  ingested: "bg-slate-100 text-slate-700",
  extracting: "bg-blue-100 text-blue-700",
  needs_review: "bg-amber-100 text-amber-700",
  duplicate_review: "bg-orange-100 text-orange-700",
  syncing: "bg-teal-100 text-teal-700",
  synced: "bg-emerald-100 text-emerald-700",
  error_extract: "bg-red-100 text-red-700",
  error_sync: "bg-red-100 text-red-700",
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
        "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold",
        statusStyles[status] ?? "bg-slate-100 text-slate-700",
      )}
    >
      {statusLabels[status] ?? status.replace(/_/g, " ")}
    </span>
  );
}
