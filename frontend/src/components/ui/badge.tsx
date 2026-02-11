import { cn } from "@/lib/utils";

const statusStyles: Record<string, string> = {
  ingested: "bg-slate-100 text-slate-700",
  extracting: "bg-blue-100 text-blue-700",
  needs_review: "bg-amber-100 text-amber-700",
  syncing: "bg-teal-100 text-teal-700",
  synced: "bg-emerald-100 text-emerald-700",
  error_extract: "bg-red-100 text-red-700",
  error_sync: "bg-red-100 text-red-700",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold uppercase tracking-wide",
        statusStyles[status] ?? "bg-slate-100 text-slate-700",
      )}
    >
      {status.replace("_", " ")}
    </span>
  );
}
