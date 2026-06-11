"use client";

// ---------------------------------------------------------------------------
// SyncStatusStrip — shows blocking reasons before the ActionButtonBar
// ---------------------------------------------------------------------------

interface SyncStatusStripProps {
  reasons: string[];
}

/**
 * Renders an amber strip listing reasons that block syncing.
 * Returns null when the reasons array is empty (ready to sync).
 */
export function SyncStatusStrip({ reasons }: SyncStatusStripProps) {
  if (reasons.length === 0) return null;

  return (
    <div
      data-testid="sync-status-strip"
      className="animate-reveal rounded-2xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
    >
      <p className="mb-1 font-semibold">Resolve before syncing</p>
      <ul className="space-y-0.5">
        {reasons.map((reason) => (
          <li key={reason}>&bull; {reason}</li>
        ))}
      </ul>
    </div>
  );
}
