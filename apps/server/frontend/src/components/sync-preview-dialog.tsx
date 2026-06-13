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
  isConfirmDisabled: boolean;
  confirmDisabledReason?: string;
  isSyncing: boolean;
  dateTimeConfirmed: boolean;
  totalConfirmed: boolean;
  onConfirm: () => void;
  /**
   * Whether to offer the "skip this preview" checkbox. The quick-sync flow on
   * the receipt list always previews (that's its whole point), so it hides
   * the option to avoid implying this dialog can be skipped.
   */
  showSkipPreviewOption?: boolean;
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
  isConfirmDisabled,
  confirmDisabledReason,
  isSyncing,
  dateTimeConfirmed,
  totalConfirmed,
  onConfirm,
  showSkipPreviewOption = true,
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
    confirmLabel = "Update in YNAB";
  } else {
    confirmLabel = "Add to YNAB";
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
      <span className="max-w-full break-all rounded border border-ember bg-ember/15 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ember">
        LIVE{budgetLabel ? ` → ${budgetLabel}` : ""}
      </span>
    );
  }

  // Flag color
  const activeFlag = hasSuccessfulSync ? mode.updatedFlagColor : mode.newFlagColor;

  // One plain-English line explaining what confirming will do
  const intentSummary = hasSuccessfulSync
    ? "This receipt is already in YNAB — here's what the updated transaction will look like."
    : "Here's the transaction that will be added to YNAB.";

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
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
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
        <p className="mb-3 text-xs text-ink/70">{intentSummary}</p>

        {/* Table */}
        <table className="w-full table-fixed border-collapse text-xs">
          <colgroup>
            <col className="w-20 sm:w-28" />
            <col />
          </colgroup>
          <tbody>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Payee</td>
              <td className="py-1.5 text-ink break-words">{draft.payee_name || "—"}</td>
            </tr>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Account</td>
              <td className="py-1.5 text-ink break-words">{accountName}</td>
            </tr>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Date</td>
              <td className="py-1.5 text-ink break-words">{draft.transaction_date || "—"}</td>
            </tr>
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Total</td>
              <td className="py-1.5 text-ink break-words">
                {formatSignedDollarsWithDirection(signed, kind)}
              </td>
            </tr>

            {/* Category / splits */}
            {draft.splits.length > 0 ? (
              <tr className="border-b border-ink/8">
                <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Categories</td>
                <td className="py-1.5 min-w-0">
                  <table className="w-full table-fixed">
                    <colgroup>
                      <col />
                      <col className="w-16" />
                    </colgroup>
                    <tbody>
                      {draft.splits.map((split, idx) => {
                        const splitSigned = signedDollars(split.amount, kind);
                        return (
                          <tr key={idx} className="border-b border-ink/5 last:border-0">
                            <td className="py-0.5 pr-2 text-ink/80 break-words">{categoryLabel(split.category_id)}</td>
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
                <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Category</td>
                <td className="py-1.5 text-ink break-words">{categoryLabel(draft.category_id)}</td>
              </tr>
            )}

            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Memo</td>
              <td className="py-1.5 text-ink break-words">{displayMemo || "—"}</td>
            </tr>

            {/* Receipt confirmation status */}
            <tr className="border-b border-ink/8">
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Checked</td>
              <td className="py-1.5 space-y-0.5">
                <p className={dateTimeConfirmed ? "text-emerald-700" : "text-amber-700"}>
                  {dateTimeConfirmed ? "✓ Date + time match the receipt" : "✗ Date + time not confirmed yet"}
                </p>
                <p className={totalConfirmed ? "text-emerald-700" : "text-amber-700"}>
                  {totalConfirmed ? "✓ Total matches the receipt" : "✗ Total not confirmed yet"}
                </p>
              </td>
            </tr>

            {/* Flag color */}
            <tr>
              <td className="py-1.5 pr-3 font-semibold text-ink/70 align-top whitespace-nowrap">Flag color</td>
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

      </div>

      {/* Footer */}
      <div className="border-t border-ink/10 px-4 py-3">
        {showSkipPreviewOption ? (
        <div className="flex items-center gap-2 border-b border-ink/10 pb-3 mb-3">
          <input
            type="checkbox"
            id="skip-preview-checkbox"
            className="h-4 w-4 rounded"
            defaultChecked={typeof window !== "undefined" && window.localStorage.getItem("snappy_skip_preview") === "true"}
            onChange={(e) => {
              if (typeof window !== "undefined") {
                if (e.target.checked) {
                  window.localStorage.setItem("snappy_skip_preview", "true");
                } else {
                  window.localStorage.removeItem("snappy_skip_preview");
                }
              }
            }}
          />
          <label htmlFor="skip-preview-checkbox" className="text-xs text-ink/60">
            Skip this preview for future clean syncs
          </label>
        </div>
        ) : null}
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
            variant="success"
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
