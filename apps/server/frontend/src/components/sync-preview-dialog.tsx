"use client";

import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  formatSignedDollarsWithDirection,
  formatDollarsMagnitude,
  signedDollars,
} from "@/lib/money";
import type { ValidationPayloadInput } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const REFUND_MEMO_PREFIX = "Return: ";

/** Mirror of Python _ensure_refund_memo_prefix */
function ensureRefundMemoPrefix(memo: string): string {
  const text = memo.trim();
  const lower = text.toLowerCase();
  if (lower.startsWith("return:") || lower.startsWith("returning") || lower.startsWith("refund")) {
    return text;
  }
  return `${REFUND_MEMO_PREFIX}${text}`.trim();
}

const FLAG_COLOR_LABELS: Record<string, string> = {
  red: "Red",
  orange: "Orange",
  yellow: "Yellow",
  green: "Green",
  blue: "Blue",
  purple: "Purple",
};

const FLAG_COLOR_SWATCHES: Record<string, string> = {
  red: "bg-red-500",
  orange: "bg-orange-500",
  yellow: "bg-yellow-400",
  green: "bg-emerald-500",
  blue: "bg-blue-500",
  purple: "bg-purple-500",
};

function flagColorLabel(color: string): string {
  return FLAG_COLOR_LABELS[color.toLowerCase()] ?? color;
}

function flagColorSwatch(color: string): string {
  return FLAG_COLOR_SWATCHES[color.toLowerCase()] ?? "bg-ink/30";
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SyncPreviewMode {
  dryRun: boolean;
  syncEnabled: boolean;
  budgetName: string | null;
  budgetId: string | null;
  newFlagColor: string;
  updatedFlagColor: string;
}

export interface SyncPreviewDialogProps {
  open: boolean;
  onClose: () => void;
  draft: ValidationPayloadInput;
  accounts: { entity_id: string; name: string }[];
  categories: { entity_id: string; name: string; group_name: string | null }[];
  hasSuccessfulSync: boolean;
  mode: SyncPreviewMode;
  /** The transaction object from the most recent dry-run's raw_request */
  lastDryRunTransaction: Record<string, unknown> | null;
  isConfirmDisabled: boolean;
  confirmDisabledReason?: string;
  isSyncing: boolean;
  dateTimeConfirmed: boolean;
  totalConfirmed: boolean;
  onConfirm: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SyncPreviewDialog({
  open,
  onClose,
  draft,
  accounts,
  categories,
  hasSuccessfulSync,
  mode,
  lastDryRunTransaction,
  isConfirmDisabled,
  confirmDisabledReason,
  isSyncing,
  dateTimeConfirmed,
  totalConfirmed,
  onConfirm,
}: SyncPreviewDialogProps) {
  const kind = draft.transaction_kind;
  const signed = signedDollars(draft.total_amount, kind);

  // Resolve account name
  const accountName =
    accounts.find((a) => a.entity_id === draft.account_id)?.name ?? "Unknown account";

  // Memo with refund prefix applied when needed
  const displayMemo =
    kind === "refund" ? ensureRefundMemoPrefix(draft.memo) : draft.memo;

  // Category label
  function categoryLabel(categoryId: string): string {
    const cat = categories.find((c) => c.entity_id === categoryId);
    if (!cat) return categoryId || "—";
    return cat.group_name ? `${cat.group_name} / ${cat.name}` : cat.name;
  }

  // Confirm button label
  let confirmLabel: string;
  if (mode.dryRun) {
    confirmLabel = "Run dry-run";
  } else if (hasSuccessfulSync) {
    confirmLabel = "Update transaction in YNAB";
  } else {
    confirmLabel = "Create transaction in YNAB";
  }

  // Mode badge
  let modeBadge: React.ReactNode;
  if (!mode.syncEnabled) {
    modeBadge = (
      <span className="rounded border border-ink/20 bg-sand px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink/70">
        SYNC DISABLED
      </span>
    );
  } else if (mode.dryRun) {
    modeBadge = (
      <span className="rounded border border-ink/20 bg-sand px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink/70">
        DRY RUN
      </span>
    );
  } else {
    const budgetLabel = mode.budgetName ?? (mode.budgetId ? `budget ${mode.budgetId}` : "");
    modeBadge = (
      <span className="rounded border border-ember bg-ember/15 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ember">
        LIVE{budgetLabel ? ` → ${budgetLabel}` : ""}
      </span>
    );
  }

  // Flag color
  const activeFlag = hasSuccessfulSync ? mode.updatedFlagColor : mode.newFlagColor;

  // Intent caption
  const intentLabel = hasSuccessfulSync ? "Update intent" : "Create intent";
  const intentCaption =
    "Create-intent payload (the exact body is finalized server-side based on current validation)";

  // Confirm button is fully disabled
  const confirmFullyDisabled = isConfirmDisabled || isSyncing || !mode.syncEnabled;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      labelledById="sync-preview-heading"
      data-testid="sync-preview-dialog"
    >
      {/* Header */}
      <div className="border-b border-ink/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 id="sync-preview-heading" className="text-sm font-semibold text-ink">
            Review transaction
          </h2>
          {modeBadge}
        </div>
        {mode.dryRun && mode.syncEnabled && (
          <p className="mt-1 text-[11px] text-ink/60">
            Payload is built and saved but NOT sent to YNAB.
          </p>
        )}
      </div>

      {/* Body — dense bank-register table */}
      <div className="px-4 py-3 text-xs">
        {/* Intent label */}
        <p className="mb-2 font-semibold text-ink/70 uppercase tracking-wide text-[11px]">
          {intentLabel}
        </p>
        <p className="mb-3 text-[11px] text-ink/50">{intentCaption}</p>

        {/* Table */}
        <table className="w-full border-collapse text-xs">
          <tbody>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/60 w-28 align-top">Payee</td>
              <td className="py-1.5 text-ink">{draft.payee_name || "—"}</td>
            </tr>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Account</td>
              <td className="py-1.5 text-ink">{accountName}</td>
            </tr>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Date</td>
              <td className="py-1.5 text-ink">{draft.transaction_date || "—"}</td>
            </tr>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Total</td>
              <td className="py-1.5 text-ink">
                {formatSignedDollarsWithDirection(signed, kind)}
              </td>
            </tr>

            {/* Category / splits */}
            {draft.splits.length > 0 ? (
              <tr className="border-b border-ink/8">
                <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Categories</td>
                <td className="py-1.5">
                  <table className="w-full">
                    <tbody>
                      {draft.splits.map((split, idx) => {
                        const splitSigned = signedDollars(split.amount, kind);
                        return (
                          <tr key={idx} className="border-b border-ink/5 last:border-0">
                            <td className="py-0.5 pr-2 text-ink/80">{categoryLabel(split.category_id)}</td>
                            <td className="py-0.5 text-right text-ink tabular-nums">
                              {formatDollarsMagnitude(Math.round(Math.abs(splitSigned) * 1000))}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-ink/15">
                        <td className="pt-1 font-semibold text-ink/70">Total</td>
                        <td className="pt-1 text-right font-semibold text-ink tabular-nums">
                          {formatSignedDollarsWithDirection(signed, kind)}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </td>
              </tr>
            ) : (
              <tr className="border-b border-ink/8">
                <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Category</td>
                <td className="py-1.5 text-ink">{categoryLabel(draft.category_id)}</td>
              </tr>
            )}

            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Memo</td>
              <td className="py-1.5 text-ink">{displayMemo || "—"}</td>
            </tr>

            {/* Twin confirmation */}
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Twin checks</td>
              <td className="py-1.5 space-y-0.5">
                <p className={dateTimeConfirmed ? "text-emerald-700" : "text-amber-700"}>
                  {dateTimeConfirmed ? "✓" : "✗"} Date + time
                </p>
                <p className={totalConfirmed ? "text-emerald-700" : "text-amber-700"}>
                  {totalConfirmed ? "✓" : "✗"} Total
                </p>
              </td>
            </tr>

            {/* Flag color */}
            <tr>
              <td className="py-1.5 pr-3 font-semibold text-ink/60 align-top">Flag color</td>
              <td className="py-1.5">
                <span className="inline-flex items-center gap-1.5">
                  <span
                    className={`inline-block h-3 w-3 rounded-sm ${flagColorSwatch(activeFlag)}`}
                    aria-label={`Flag color: ${flagColorLabel(activeFlag)}`}
                  />
                  <span className="text-ink">{flagColorLabel(activeFlag)}</span>
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        {/* Last dry-run payload reference */}
        {lastDryRunTransaction ? (
          <details className="mt-3">
            <summary className="cursor-pointer text-[11px] font-semibold text-ink/50 hover:text-ink/80 select-none">
              Server&apos;s last create-intent payload (reference)
            </summary>
            <pre className="mt-1 max-h-48 overflow-auto rounded-xl bg-ink/5 p-2 text-[10px] leading-relaxed text-ink/80">
              {JSON.stringify(lastDryRunTransaction, null, 2)}
            </pre>
          </details>
        ) : null}
      </div>

      {/* Footer */}
      <div className="border-t border-ink/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            data-testid="sync-preview-cancel"
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            variant="solid"
            size="sm"
            className="flex-1"
            data-testid="sync-preview-confirm"
            disabled={confirmFullyDisabled}
            onClick={onConfirm}
          >
            {isSyncing ? "Syncing…" : confirmLabel}
          </Button>
        </div>
        {confirmFullyDisabled && confirmDisabledReason ? (
          <p className="mt-2 text-[11px] text-amber-700">{confirmDisabledReason}</p>
        ) : null}
      </div>
    </Dialog>
  );
}
