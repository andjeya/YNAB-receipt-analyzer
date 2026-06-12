"use client";

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Droplets, Flame, Plus, Trash2 } from "lucide-react";

import {
  confirmDuplicateReceipt,
  confirmTwinSection,
  enqueueSync,
  deleteReceipt,
  getAppConfig,
  getReceiptDetail,
  getYnabCache,
  overrideDuplicateReceipt,
  recomputeAllocationWorkspace,
  receiptFileUrl,
  restoreReceipt,
  saveDraft,
} from "@/lib/api";
import { AllocationWorkspace, ReceiptDetail, ValidationPayloadInput } from "@/lib/types";
import { toDraftFromPayload } from "@/lib/validation-draft";
import { shouldSkipPreview } from "@/lib/sync-skip";
import { formatSignedDollarsWithDirection, formatDollarsMagnitude, signedDollars } from "@/lib/money";
import { useToast } from "@/components/ui/toast";
import { SyncPreviewDialog } from "@/components/sync-preview-dialog";
import { SyncStatusStrip } from "@/components/sync-status-strip";
import {
  buildFallbackWorkspace,
  clearWorkspacePins,
  moveWorkspaceItems,
  reconcileWorkspaceToDraft,
  setWorkspaceLanePinnedAmount,
  workspaceFromApi,
} from "@/lib/allocation-workspace";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { ReceiptTwinViewer } from "@/components/receipt-twin-viewer";
import { AllocationBoard } from "@/components/allocation-board";
import { SnappyCelebration } from "@/components/snappy/celebration";

const UNKNOWN_ACCOUNT_ID = "__unknown__";
const PAYEE_SUGGESTION_LIMIT = 12;
const AMBIGUITY_MIN_CONFIDENCE = 0.7;


type CategoryAmbiguityFlag = {
  line_item: string;
  candidate_category_ids: string[];
  confidence: number;
  note: string;
};

type CategoryOption = {
  entity_id: string;
  name: string;
  group_name: string | null;
};

function formatCategoryLabel(category: CategoryOption): string {
  return `${category.group_name ? `${category.group_name} / ` : ""}${category.name}`;
}

function CategorySearchSelect({
  value,
  categories,
  placeholder,
  onChange,
}: {
  value: string;
  categories: CategoryOption[];
  placeholder: string;
  onChange: (nextCategoryId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");

  const selectedCategory = useMemo(
    () => categories.find((category) => category.entity_id === value) ?? null,
    [categories, value],
  );
  const filteredCategories = useMemo(() => {
    const normalizedQuery = searchTerm.trim().toLowerCase();
    if (!normalizedQuery) {
      return categories;
    }
    return categories.filter((category) => formatCategoryLabel(category).toLowerCase().includes(normalizedQuery));
  }, [categories, searchTerm]);

  const inputValue = open ? searchTerm : selectedCategory ? formatCategoryLabel(selectedCategory) : "";

  // useId guarantees a unique listbox id per instance (split rows reuse the same placeholder).
  const listboxId = `category-listbox-${useId()}`;

  return (
    <div className="relative">
      <Input
        value={inputValue}
        placeholder={placeholder}
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-autocomplete="list"
        onFocus={() => {
          setOpen(true);
          setSearchTerm("");
        }}
        onBlur={() => {
          setTimeout(() => {
            setOpen(false);
            setSearchTerm("");
          }, 120);
        }}
        onChange={(event) => {
          const nextSearch = event.target.value;
          setSearchTerm(nextSearch);
          if (value) {
            onChange("");
          }
        }}
      />
      {open ? (
        <div id={listboxId} role="listbox" className="absolute z-20 mt-1 max-h-60 w-full overflow-y-auto rounded-xl border border-ink/15 bg-white shadow-float">
          {filteredCategories.length ? (
            filteredCategories.map((category) => (
              <button
                key={category.entity_id}
                type="button"
                role="option"
                aria-selected={category.entity_id === value}
                className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-sand/70 focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  onChange(category.entity_id);
                  setOpen(false);
                  setSearchTerm("");
                }}
              >
                {formatCategoryLabel(category)}
              </button>
            ))
          ) : (
            <p className="px-3 py-2 text-sm text-ink/60">No matching categories</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function parseCategoryAmbiguityFlags(payload: Record<string, unknown> | null | undefined): CategoryAmbiguityFlag[] {
  if (!payload) {
    return [];
  }

  const rawFlags = payload.category_ambiguity_flags;
  if (!Array.isArray(rawFlags)) {
    return [];
  }

  const parsedFlags: CategoryAmbiguityFlag[] = [];
  for (const candidate of rawFlags) {
    if (!candidate || typeof candidate !== "object") {
      continue;
    }
    const row = candidate as Record<string, unknown>;
    const confidence = Number(row.confidence);
    if (!Number.isFinite(confidence) || confidence < AMBIGUITY_MIN_CONFIDENCE) {
      continue;
    }
    const lineItem = String(row.line_item ?? "").trim();
    const note = String(row.note ?? "").trim();
    const candidateCategoryIds = Array.isArray(row.candidate_category_ids)
      ? row.candidate_category_ids
          .map((item) => String(item ?? "").trim())
          .filter((item) => item.length > 0)
      : [];
    parsedFlags.push({
      line_item: lineItem,
      candidate_category_ids: candidateCategoryIds,
      confidence,
      note,
    });
  }
  return parsedFlags;
}

function toDraft(receipt: ReceiptDetail): ValidationPayloadInput {
  const payload = (receipt.latest_validation?.payload ?? receipt.latest_extraction?.parsed_json ?? {}) as Record<string, unknown>;
  return toDraftFromPayload(payload, receipt.display_payee_name ?? "");
}

function toBlankDraft(): ValidationPayloadInput {
  return {
    payee_name: "",
    account_id: "",
    transaction_date: "",
    transaction_time: "",
    memo: "",
    total_amount: 0,
    transaction_kind: "purchase",
    category_id: "",
    splits: [],
  };
}

function toModelBaselineDraft(receipt: ReceiptDetail): ValidationPayloadInput {
  const payload = receipt.model_validation?.payload;
  if (!payload || typeof payload !== "object") {
    return toBlankDraft();
  }
  return toDraftFromPayload(payload as Record<string, unknown>, "");
}

function validateDraft(
  draft: ValidationPayloadInput,
  options: { categoryIds: Set<string>; accountIds: Set<string> },
): string[] {
  const { categoryIds, accountIds } = options;
  const errors: string[] = [];
  const usesSplits = draft.splits.length > 0;

  if (!draft.payee_name.trim()) errors.push("Payee is required");
  if (!draft.account_id.trim()) {
    errors.push("Account is required");
  } else if (draft.account_id === UNKNOWN_ACCOUNT_ID) {
    errors.push("Account is unknown. Select a valid YNAB account before syncing");
  } else if (!accountIds.size) {
    errors.push("YNAB accounts are not loaded yet");
  } else if (!accountIds.has(draft.account_id)) {
    errors.push("Selected account is not a valid YNAB account");
  }
  if (!categoryIds.size) {
    errors.push("YNAB categories are not loaded yet");
  } else if (usesSplits) {
    const splitTotal = draft.splits.reduce((sum, split) => sum + Number(split.amount || 0), 0);
    if (Math.abs(splitTotal - draft.total_amount) > 0.01) {
      errors.push("Split amounts must equal total amount");
    }

    draft.splits.forEach((split, index) => {
      if (!split.category_id.trim()) {
        errors.push(`Split ${index + 1}: category is required`);
      } else if (!categoryIds.has(split.category_id)) {
        errors.push(`Split ${index + 1}: category must be an existing YNAB category`);
      }
    });
  } else if (!draft.category_id.trim()) {
    errors.push("Category is required");
  } else if (!categoryIds.has(draft.category_id)) {
    errors.push("Selected category must be an existing YNAB category");
  }

  return errors;
}

const INLINE_IMAGE_MIME_TYPES = new Set([
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/webp",
  "image/gif",
]);

function isInlineImageMimeType(mimeType: string | null | undefined): boolean {
  const normalized = String(mimeType ?? "").trim().toLowerCase();
  if (!normalized) return false;
  return INLINE_IMAGE_MIME_TYPES.has(normalized);
}

function isPdfMimeType(mimeType: string | null | undefined): boolean {
  const normalized = String(mimeType ?? "").trim().toLowerCase();
  return normalized === "application/pdf" || normalized.endsWith("+pdf");
}

function ScanPanel({ receiptId, mimeType, originalFilename }: {
  receiptId: string;
  mimeType: string;
  originalFilename: string;
}) {
  const previewUrl = receiptFileUrl(receiptId);
  const downloadUrl = receiptFileUrl(receiptId, false);
  const [imageError, setImageError] = useState(false);
  const [pdfLoading, setPdfLoading] = useState(true);
  // Headless/plugin-less browsers never fire onLoad for <object> PDFs, which
  // left "Loading PDF…" stacked on top of the fallback message. Time the veil
  // out so only one state shows.
  useEffect(() => {
    if (!pdfLoading) return;
    const timer = setTimeout(() => setPdfLoading(false), 4000);
    return () => clearTimeout(timer);
  }, [pdfLoading]);

  const isImage = isInlineImageMimeType(mimeType);
  const isPdf = isPdfMimeType(mimeType);

  const fallbackLinks = (
    <div className="flex flex-wrap items-center justify-center gap-2">
      <a
        href={previewUrl}
        target="_blank"
        rel="noreferrer"
        className="rounded-md border border-ink/20 bg-white px-3 py-1.5 text-xs font-semibold text-ink hover:bg-sand/70"
      >
        Open in new tab
      </a>
      <a
        href={downloadUrl}
        className="rounded-md bg-ink px-3 py-1.5 text-xs font-semibold text-white hover:bg-ink/90"
        download
      >
        Download
      </a>
    </div>
  );

  return (
    <Card className="h-full overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-ink/10 px-3 py-2">
        <h2 className="text-sm font-semibold">Original Scan</h2>
        <div className="flex gap-2">
          <a href={previewUrl} target="_blank" rel="noreferrer"
             className="rounded-md border border-ink/20 bg-white/80 px-2 py-1 text-xs font-medium text-ink/80 hover:bg-white transition">
            Open ↗
          </a>
          <a href={downloadUrl} download
             className="rounded-md border border-ink/20 bg-white/80 px-2 py-1 text-xs font-medium text-ink/80 hover:bg-white transition">
            Download ↓
          </a>
        </div>
      </div>
      <div className="h-[28rem] overflow-auto bg-black/5 p-2">
        {isImage && !imageError ? (
          <div className="relative h-full min-h-[22rem] w-full">
            <Image
              src={previewUrl}
              alt={originalFilename}
              fill
              unoptimized
              className="object-contain"
              onError={() => setImageError(true)}
            />
          </div>
        ) : isImage && imageError ? (
          <div className="flex h-full min-h-[22rem] flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-ink/20 bg-white/70 px-4 text-center">
            <p className="text-sm text-ink/75">Image preview unavailable.</p>
            {fallbackLinks}
          </div>
        ) : isPdf ? (
          <div className="relative h-full min-h-[22rem] w-full">
            {pdfLoading ? (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/5 text-xs text-ink/50">
                Loading PDF…
              </div>
            ) : null}
            {/* object element provides a native fallback paragraph when the browser PDF plugin is absent */}
            <object
              data={`${previewUrl}#toolbar=1&view=FitH`}
              type="application/pdf"
              className="h-full min-h-[22rem] w-full border-0"
              onLoad={() => setPdfLoading(false)}
              aria-label="Receipt scan PDF"
            >
              {/* Rendered when browser cannot display the PDF inline (headless, plugin disabled, etc.) */}
              <div className="flex h-full min-h-[22rem] flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-ink/20 bg-white/70 px-4 text-center">
                <p className="text-sm text-ink/75">
                  PDF inline preview is not available in this browser. Use the links above to view the receipt.
                </p>
                {fallbackLinks}
              </div>
            </object>
          </div>
        ) : (
          <div className="flex h-full min-h-[22rem] flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-ink/20 bg-white/70 px-4 text-center">
            <p className="text-sm text-ink/75">
              Preview not available for this file type ({mimeType || "unknown"}).
            </p>
            {fallbackLinks}
          </div>
        )}
      </div>
    </Card>
  );
}

function TwinAndScanSection({ receiptId, receipt, mobileView, setMobileView, mobilePanelRef, onTwinUpdated, autoConfirmed }: {
  receiptId: string;
  receipt: ReceiptDetail;
  mobileView: "twin" | "scan";
  setMobileView: (v: "twin" | "scan") => void;
  mobilePanelRef: { current: HTMLDivElement | null };
  onTwinUpdated: () => void;
  autoConfirmed: boolean;
}) {
  const scanPanel = (
    <ScanPanel
      receiptId={receiptId}
      mimeType={String(receipt.mime_type ?? "")}
      originalFilename={String(receipt.original_filename ?? "")}
    />
  );
  return (
    <section className="animate-reveal space-y-3" style={{ animationDelay: "55ms" }}>
      <div className="hidden gap-3 md:grid md:grid-cols-2">
        <ReceiptTwinViewer receiptId={receiptId} twin={receipt.latest_twin} onUpdated={onTwinUpdated} autoConfirmed={autoConfirmed} />
        {scanPanel}
      </div>
      <div className="space-y-2 md:hidden">
        <div className="inline-flex rounded-xl border border-ink/15 bg-white p-1 text-xs">
          <button
            type="button"
            className={`rounded-lg px-3 py-1 focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 ${mobileView === "twin" ? "bg-ink text-white" : "text-ink/70"}`}
            onClick={() => setMobileView("twin")}
          >
            Receipt Details
          </button>
          <button
            type="button"
            className={`rounded-lg px-3 py-1 focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 ${mobileView === "scan" ? "bg-ink text-white" : "text-ink/70"}`}
            onClick={() => setMobileView("scan")}
          >
            Original Scan
          </button>
        </div>
        <div ref={mobilePanelRef} className="max-h-[32rem] overflow-auto">
          {mobileView === "twin" ? (
            <ReceiptTwinViewer receiptId={receiptId} twin={receipt.latest_twin} onUpdated={onTwinUpdated} autoConfirmed={autoConfirmed} />
          ) : (
            scanPanel
          )}
        </div>
      </div>
    </section>
  );
}

function PayeeAccountCard({ draft, setDraft, setDirty, accounts, payeeSuggestions, payeeMenuOpen, setPayeeMenuOpen, accountNeedsAttention, cardLastFour, latestValidationPayload }: {
  draft: ValidationPayloadInput;
  setDraft: (d: ValidationPayloadInput) => void;
  setDirty: (v: boolean) => void;
  accounts: { entity_id: string; name: string }[];
  payeeSuggestions: { entity_id: string; name: string }[];
  payeeMenuOpen: boolean;
  setPayeeMenuOpen: (v: boolean) => void;
  accountNeedsAttention: boolean;
  cardLastFour?: string | null;
  latestValidationPayload?: Record<string, unknown> | null;
}) {
  return (
    <Card className="animate-reveal space-y-3" style={{ animationDelay: "70ms" }}>
      <h2 className="font-semibold">Payee + Account</h2>
      <div className="grid gap-3">
        <div className="relative">
          <label htmlFor="payee-input" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Payee</label>
          <Input
            id="payee-input"
            value={draft.payee_name}
            role="combobox"
            aria-expanded={payeeMenuOpen && payeeSuggestions.length > 0}
            aria-controls={payeeMenuOpen && payeeSuggestions.length > 0 ? "payee-suggestions-listbox" : undefined}
            aria-autocomplete="list"
            onFocus={() => setPayeeMenuOpen(true)}
            onBlur={() => { setTimeout(() => setPayeeMenuOpen(false), 120); }}
            onChange={(event) => {
              setDraft({ ...draft, payee_name: event.target.value });
              setDirty(true);
              setPayeeMenuOpen(true);
            }}
          />
          {payeeMenuOpen && payeeSuggestions.length > 0 ? (
            <div id="payee-suggestions-listbox" role="listbox" className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-xl border border-ink/15 bg-white shadow-float">
              {payeeSuggestions.map((payee) => (
                <button
                  key={payee.entity_id}
                  type="button"
                  role="option"
                  aria-selected={draft.payee_name === payee.name}
                  className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-sand/70 focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    setDraft({ ...draft, payee_name: payee.name });
                    setDirty(true);
                    setPayeeMenuOpen(false);
                  }}
                >
                  {payee.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div>
          <label htmlFor="account-select-input" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Account</label>
          <Select
            id="account-select-input"
            value={draft.account_id}
            data-testid="account-select"
            className={accountNeedsAttention ? "border-amber-500 bg-amber-50 text-amber-900 focus:ring-amber-300" : undefined}
            onChange={(event) => {
              setDraft({ ...draft, account_id: event.target.value, account_source: undefined });
              setDirty(true);
            }}
          >
            <option value="">Select account</option>
            {accounts.map((account) => (
              <option key={account.entity_id} value={account.entity_id}>{account.name}</option>
            ))}
            <option value={UNKNOWN_ACCOUNT_ID}>Unknown (needs review)</option>
          </Select>
          {accountNeedsAttention ? (
            <p className="mt-1 text-xs font-semibold text-amber-700">Unknown account selected. Sync is disabled until this is fixed.</p>
          ) : null}
          {cardLastFour &&
           draft.account_id &&
           draft.account_id !== UNKNOWN_ACCOUNT_ID &&
           draft.account_id !== "" &&
           draft.account_source === "card_mapping" ? (
            <p className="mt-1 flex items-center gap-1 text-[11px] text-sky-700">
              <span>📌</span>
              <span>Account remembered from card ending {cardLastFour}</span>
            </p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function MemoCard({ draft, setDraft, setDirty }: {
  draft: ValidationPayloadInput;
  setDraft: (d: ValidationPayloadInput) => void;
  setDirty: (v: boolean) => void;
}) {
  const [showTxType, setShowTxType] = useState(draft.transaction_kind === "refund");

  return (
    <Card className="animate-reveal space-y-3" style={{ animationDelay: "120ms" }}>
      <h2 className="font-semibold">Memo</h2>
      <div>
        <label htmlFor="memo-input" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Transaction memo</label>
        <p className="mb-1 flex items-center gap-1 text-[11px] text-sky-600">
          <span>✨</span>
          <span>AI-generated — edit if needed</span>
        </p>
        <Textarea
          id="memo-input"
          rows={2}
          value={draft.memo}
          onChange={(event) => {
            setDraft({ ...draft, memo: event.target.value });
            setDirty(true);
          }}
        />
      </div>
      <div>
        {!showTxType ? (
          <button
            type="button"
            className="text-[11px] text-ink/50 hover:text-ink/70 underline"
            onClick={() => setShowTxType(true)}
          >
            {draft.transaction_kind === "refund" ? "Refund / return (inflow)" : "Purchase (outflow)"} — change transaction type?
          </button>
        ) : (
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Transaction type</label>
            <Select
              value={draft.transaction_kind}
              onChange={(event) => {
                setDraft({ ...draft, transaction_kind: event.target.value as "purchase" | "refund" });
                setDirty(true);
              }}
            >
              <option value="purchase">Purchase (outflow)</option>
              <option value="refund">Refund / return (inflow)</option>
            </Select>
          </div>
        )}
      </div>
    </Card>
  );
}

function CategorySplitCard({ draft, setDraft, setDirty, categories, isSplitMode, splitTotal, onSplitAmountEdited }: {
  draft: ValidationPayloadInput;
  setDraft: (d: ValidationPayloadInput) => void;
  setDirty: (v: boolean) => void;
  categories: CategoryOption[];
  isSplitMode: boolean;
  splitTotal: number;
  onSplitAmountEdited?: (index: number, amount: number) => void;
}) {
  const [confirmDeleteIndex, setConfirmDeleteIndex] = useState<number | null>(null);

  return (
    <Card className="animate-reveal space-y-3" style={{ animationDelay: "170ms" }}>
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">Categories</h2>
        <div className="inline-flex rounded-xl border border-ink/20 bg-ink/5 p-0.5 text-xs">
          <button
            type="button"
            className={`rounded-lg px-3 py-1 font-semibold transition focus-visible:ring-2 focus-visible:ring-mint/70 ${!isSplitMode ? "bg-ink text-white shadow-sm" : "text-ink/60 hover:text-ink/80"}`}
            onClick={() => {
              if (isSplitMode) {
                const fallbackCategory = draft.splits.find((s) => s.category_id)?.category_id ?? draft.category_id;
                setDraft({ ...draft, category_id: fallbackCategory, splits: [], category_source: undefined });
                setDirty(true);
              }
            }}
            disabled={!isSplitMode}
          >
            Single
          </button>
          <button
            type="button"
            className={`rounded-lg px-3 py-1 font-semibold transition focus-visible:ring-2 focus-visible:ring-mint/70 ${isSplitMode ? "bg-ink text-white shadow-sm" : "text-ink/60 hover:text-ink/80"}`}
            onClick={() => {
              if (!isSplitMode) {
                setDraft({ ...draft, category_id: "", splits: [{ category_id: draft.category_id, amount: draft.total_amount, memo: "" }], category_source: undefined });
                setDirty(true);
              }
            }}
            disabled={isSplitMode}
          >
            Split
          </button>
        </div>
      </div>
      {draft.category_source === "payee_memory" ? (
        <p className="flex items-center gap-1 text-[11px] text-sky-700">
          <span>📌</span>
          <span>Categories remembered from how you sorted this store last time</span>
        </p>
      ) : null}

      {!isSplitMode ? (
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Category</label>
          <CategorySearchSelect
            value={draft.category_id}
            categories={categories}
            placeholder="Select or search category"
            onChange={(nextCategoryId) => {
              setDraft({ ...draft, category_id: nextCategoryId, category_source: undefined });
              setDirty(true);
            }}
          />
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between rounded-xl bg-sand/50 px-3 py-2 text-xs">
            {(() => {
              const delta = Math.round((draft.total_amount - splitTotal) * 100) / 100;
              const hasDiscrepancy = Math.abs(delta) > 0.005;
              return (
                <span className={hasDiscrepancy ? "font-semibold text-red-700" : "text-ink/75"}>
                  Split total: ${splitTotal.toFixed(2)}
                  {hasDiscrepancy ? (
                    <span className="ml-2">
                      ({delta > 0 ? `+$${delta.toFixed(2)} unassigned` : `-$${Math.abs(delta).toFixed(2)} over total`})
                    </span>
                  ) : (
                    <span className="ml-1 text-emerald-700">✓</span>
                  )}
                </span>
              );
            })()}
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => {
                setDraft({ ...draft, splits: [...draft.splits, { category_id: "", amount: 0, memo: "" }], category_source: undefined });
                setDirty(true);
              }}
            >
              <Plus className="h-4 w-4" /> Add split
            </Button>
          </div>

          {draft.splits.map((split, index) => (
            <div key={`${index}-${split.category_id}`} className="rounded-2xl border border-ink/10 bg-sand/70 p-3">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Split {index + 1}</p>
                {confirmDeleteIndex === index ? (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      className="text-[10px] font-semibold text-red-700 hover:underline"
                      onClick={() => {
                        setConfirmDeleteIndex(null);
                        const nextSplits = draft.splits.filter((_, i) => i !== index);
                        if (nextSplits.length === 0) {
                          const fallbackCategory = split.category_id || draft.category_id;
                          setDraft({ ...draft, category_id: fallbackCategory, splits: [], category_source: undefined });
                        } else {
                          setDraft({ ...draft, category_id: "", splits: nextSplits, category_source: undefined });
                        }
                        setDirty(true);
                      }}
                    >
                      Remove
                    </button>
                    <button
                      type="button"
                      className="text-[10px] text-ink/50 hover:underline"
                      onClick={() => setConfirmDeleteIndex(null)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="inline-flex items-center text-red-600 focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2"
                    onClick={() => setConfirmDeleteIndex(index)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
              <div className="grid gap-2">
                <div className="relative">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-ink/60">$</span>
                  <Input
                    type="number"
                    step="0.01"
                    value={split.amount}
                    className="pl-6"
                    onChange={(event) => {
                      const nextSplits = [...draft.splits];
                      const nextAmount = Number(event.target.value) || 0;
                      nextSplits[index] = { ...split, amount: nextAmount };
                      setDraft({ ...draft, splits: nextSplits, category_source: undefined });
                      setDirty(true);
                      onSplitAmountEdited?.(index, nextAmount);
                    }}
                  />
                </div>
                <CategorySearchSelect
                  value={split.category_id}
                  categories={categories}
                  placeholder="Move item to category"
                  onChange={(nextCategoryId) => {
                    const nextSplits = [...draft.splits];
                    nextSplits[index] = { ...split, category_id: nextCategoryId };
                    setDraft({ ...draft, splits: nextSplits, category_source: undefined });
                    setDirty(true);
                  }}
                />
                <Input
                  placeholder="Split memo"
                  value={split.memo}
                  onChange={(event) => {
                    const nextSplits = [...draft.splits];
                    nextSplits[index] = { ...split, memo: event.target.value };
                    setDraft({ ...draft, splits: nextSplits });
                    setDirty(true);
                  }}
                />
              </div>
            </div>
          ))}
        </>
      )}
    </Card>
  );
}

function ValidationStatusSection({ isAutosaving, dirty, lockWarnings, correctionHistory }: {
  isAutosaving: boolean;
  dirty: boolean;
  lockWarnings: string[];
  correctionHistory: ReceiptDetail["correction_history"];
}) {
  return (
    <>
      <section className="animate-reveal rounded-2xl bg-white/80 p-3 text-xs text-ink/70" style={{ animationDelay: "210ms" }}>
        <p className="font-semibold text-ink/70">
          {isAutosaving ? "Autosaving..." : dirty ? "Changes pending autosave" : "Draft saved"}
        </p>
        {lockWarnings.length > 0 ? (
          <div className="mt-2 space-y-1 rounded-xl border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] text-amber-900">
            {lockWarnings.map((warning) => <p key={warning}>- {warning}</p>)}
          </div>
        ) : null}
      </section>
      {correctionHistory.length > 0 ? (
        <section className="animate-reveal rounded-2xl border border-black/20 bg-black/90 p-3 text-xs text-white" style={{ animationDelay: "225ms" }}>
          <p className="font-semibold">Correction history</p>
          {correctionHistory.slice(0, 3).map((item) => (
            <p key={item.id} className="mt-1 text-[11px] text-slate-200">
              {new Date(item.detected_at).toLocaleDateString()}: {item.note?.split("| sig=", 1)[0] ?? "Category corrected in YNAB"}
            </p>
          ))}
        </section>
      ) : null}
    </>
  );
}

function DuplicateReviewSection({
  receipt,
  matchedReceipt,
  isLoadingMatch,
  onConfirmDuplicate,
  onOverrideDuplicate,
  isConfirmingDuplicate,
  isOverridingDuplicate,
}: {
  receipt: ReceiptDetail;
  matchedReceipt: ReceiptDetail | null;
  isLoadingMatch: boolean;
  onConfirmDuplicate: () => void;
  onOverrideDuplicate: () => void;
  isConfirmingDuplicate: boolean;
  isOverridingDuplicate: boolean;
}) {
  return (
    <section className="animate-reveal space-y-4">
      <Card className="space-y-3 border-2 border-orange-300 bg-orange-50/60">
        <h2 className="text-lg font-semibold text-orange-900">Duplicate Detected</h2>
        <p className="text-sm text-orange-900/90">
          This receipt matches an existing transaction by payee, date, time, and total. Sync is blocked until resolved.
        </p>
        {receipt.status_reason ? <p className="text-xs text-orange-800">{receipt.status_reason}</p> : null}
      </Card>

      <div className="grid gap-3 md:grid-cols-2">
        <Card className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Incoming Receipt</p>
          <p className="text-sm font-semibold">{receipt.display_payee_name ?? receipt.original_filename}</p>
          <p className="text-xs text-ink/70">Date {receipt.display_receipt_date ?? "--"}</p>
          <p className="text-xs text-ink/70">Total {formatDollarsMagnitude(receipt.display_total_milliunits)}</p>
          <div className="h-80 overflow-hidden rounded-xl border border-ink/10 bg-black/5">
            <ScanPanel
              receiptId={receipt.id}
              mimeType={String(receipt.mime_type ?? "")}
              originalFilename={String(receipt.original_filename ?? "")}
            />
          </div>
        </Card>

        <Card className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Existing Receipt</p>
          {isLoadingMatch ? <p className="text-sm text-ink/70">Loading matched receipt...</p> : null}
          {!isLoadingMatch && matchedReceipt ? (
            <>
              <p className="text-sm font-semibold">{matchedReceipt.display_payee_name ?? matchedReceipt.original_filename}</p>
              <p className="text-xs text-ink/70">Date {matchedReceipt.display_receipt_date ?? "--"}</p>
              <p className="text-xs text-ink/70">Total {formatDollarsMagnitude(matchedReceipt.display_total_milliunits)}</p>
              <div className="h-80 overflow-hidden rounded-xl border border-ink/10 bg-black/5">
                <ScanPanel
                  receiptId={matchedReceipt.id}
                  mimeType={String(matchedReceipt.mime_type ?? "")}
                  originalFilename={String(matchedReceipt.original_filename ?? "")}
                />
              </div>
            </>
          ) : null}
          {!isLoadingMatch && !matchedReceipt ? <p className="text-sm text-red-700">Matched receipt could not be loaded.</p> : null}
        </Card>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink/15 bg-white/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-2">
          <Button
            className="flex-1 bg-red-700 hover:bg-red-800"
            onClick={onConfirmDuplicate}
            disabled={isConfirmingDuplicate || isOverridingDuplicate}
          >
            {isConfirmingDuplicate ? "Discarding..." : "Confirm Duplicate (Discard Incoming)"}
          </Button>
          <Button
            variant="outline"
            className="flex-1"
            onClick={onOverrideDuplicate}
            disabled={isConfirmingDuplicate || isOverridingDuplicate}
          >
            {isOverridingDuplicate ? "Unlocking..." : "Detection Inaccurate"}
          </Button>
        </div>
      </div>
    </section>
  );
}

function ActionButtonBar({ isSyncing, onReset, canReset, isAutosaving, onSync, canSync, syncButtonLabel, stripReasons }: {
  isSyncing: boolean;
  onReset: () => void;
  canReset: boolean;
  isAutosaving: boolean;
  onSync: () => void;
  canSync: boolean;
  syncButtonLabel: string;
  stripReasons: string[];
}) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink/15 bg-white/95 px-4 py-3 backdrop-blur">
      {stripReasons.length > 0 ? (
        <div className="mx-auto mb-2 max-w-6xl">
          <p className="text-xs font-medium text-amber-800">
            {stripReasons[0]}
            {stripReasons.length > 1 ? <span className="ml-1 text-ink/50">(+{stripReasons.length - 1} more)</span> : null}
          </p>
        </div>
      ) : null}
      <div className="mx-auto flex max-w-6xl items-center gap-2">
        <Button variant="outline" className="flex-1" onClick={onReset} disabled={!canReset || isAutosaving}>
          Reset
        </Button>
        <Button className="flex-1" variant={isSyncing ? "outline" : "solid"} onClick={onSync} disabled={!canSync || isSyncing} data-testid="sync-button">
          {syncButtonLabel}
        </Button>
      </div>
    </div>
  );
}

export function ReceiptDetailView({ receiptId }: { receiptId: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [draft, setDraft] = useState<ValidationPayloadInput | null>(null);
  const [allocationWorkspace, setAllocationWorkspace] = useState<AllocationWorkspace | null>(null);
  const [cancelBaseline, setCancelBaseline] = useState<ValidationPayloadInput | null>(null);
  const [cancelBaselineWorkspace, setCancelBaselineWorkspace] = useState<AllocationWorkspace | null>(null);
  const [baselineReceiptId, setBaselineReceiptId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selectedAllocationItemIds, setSelectedAllocationItemIds] = useState<Set<string>>(new Set());
  const [allocationWarnings, setAllocationWarnings] = useState<string[]>([]);
  const [payeeMenuOpen, setPayeeMenuOpen] = useState(false);
  const [lockWarnings, setLockWarnings] = useState<string[]>([]);
  const [mobileView, setMobileView] = useState<"twin" | "scan">("twin");
  const [previewOpen, setPreviewOpen] = useState(false);
  const mobilePanelRef = useRef<HTMLDivElement | null>(null);
  // Accuracy celebration: track previous status to fire only on edge synced transition
  const prevStatusRef = useRef<string | null>(null);
  const [showSyncCelebration, setShowSyncCelebration] = useState(false);
  const autoConfirmedRef = useRef<Set<string>>(new Set());

  const receiptQuery = useQuery({
    queryKey: ["receipt", receiptId],
    queryFn: () => getReceiptDetail(receiptId),
    refetchInterval: 6000,
  });

  const cacheQuery = useQuery({
    queryKey: ["ynab-cache"],
    queryFn: () => getYnabCache(),
    staleTime: 20_000,
  });

  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: () => getAppConfig(),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!receiptQuery.data) return;
    const nextDraft = toDraft(receiptQuery.data);
    const rawWorkspace = workspaceFromApi(
      receiptQuery.data.latest_validation?.allocation_workspace ?? null,
      nextDraft,
      receiptQuery.data.latest_twin,
    );
    const nextWorkspace = receiptQuery.data.latest_validation?.source === "model"
      ? clearWorkspacePins(rawWorkspace)
      : rawWorkspace;
    if (baselineReceiptId !== receiptQuery.data.id) {
      setBaselineReceiptId(receiptQuery.data.id);
      setCancelBaseline(toModelBaselineDraft(receiptQuery.data));
      setCancelBaselineWorkspace(nextWorkspace);
      setDraft(nextDraft);
      setAllocationWorkspace(nextWorkspace);
      setAllocationWarnings(nextWorkspace.warnings ?? []);
      setDirty(false);
      setLockWarnings([]);
      setSelectedAllocationItemIds(new Set());
      setMobileView("twin");
      return;
    }
    if (!dirty) {
      setDraft(nextDraft);
      setAllocationWorkspace(nextWorkspace);
      setAllocationWarnings(nextWorkspace.warnings ?? []);
      setSelectedAllocationItemIds(new Set());
    }
  }, [receiptQuery.data, baselineReceiptId, dirty]);

  const saveMutation = useMutation({
    mutationFn: ({ nextDraft, nextWorkspace }: { nextDraft: ValidationPayloadInput; nextWorkspace: AllocationWorkspace | null }) =>
      saveDraft(receiptId, {
        ...nextDraft,
        transaction_time: nextDraft.transaction_time?.trim() ? nextDraft.transaction_time : null,
      }, nextWorkspace),
    onSuccess: (result) => {
      setDirty(false);
      setLockWarnings(result.lock_warnings ?? []);
      if (draft && result.validation.allocation_workspace) {
        const refreshedWorkspace = workspaceFromApi(
          result.validation.allocation_workspace,
          draft,
          receiptQuery.data?.latest_twin ?? null,
        );
        setAllocationWorkspace(refreshedWorkspace);
        setAllocationWarnings(refreshedWorkspace.warnings ?? []);
      }
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Autosave failed — your changes may not be saved",
        title: "Autosave failed",
      });
    },
  });

  const recomputeMutation = useMutation({
    mutationFn: ({
      workspace,
      mode,
    }: {
      workspace: AllocationWorkspace;
      mode: "discard_manual_amounts" | "keep_manual_amounts";
    }) => recomputeAllocationWorkspace(receiptId, workspace, mode),
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to recompute allocations",
      });
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => enqueueSync(receiptId),
    onSuccess: () => {
      setPreviewOpen(false);
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      toast({ variant: "success", message: "Sync enqueued", title: "Sync enqueued" });
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to enqueue sync",
        title: "Sync failed",
      });
    },
  });

  const confirmDuplicateMutation = useMutation({
    mutationFn: () => confirmDuplicateReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to confirm duplicate",
      });
    },
  });

  const overrideDuplicateMutation = useMutation({
    mutationFn: () => overrideDuplicateReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to override duplicate detection",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
      toast({
        variant: "success",
        message: "Receipt deleted.",
        durationMs: 6000,
        action: {
          label: "Undo",
          onClick: () => {
            restoreReceipt(receiptId)
              .then(() => {
                queryClient.invalidateQueries({ queryKey: ["receipts"] });
                queryClient.invalidateQueries({ queryKey: ["stats"] });
              })
              .catch((e) =>
                toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Couldn’t restore the receipt" }),
              );
          },
        },
      });
      router.push("/");
    },
    onError: (e) => {
      toast({ variant: "error", message: e instanceof Error && e.message ? e.message : "Couldn’t delete this receipt" });
    },
  });

  useEffect(() => {
    if (!draft || !dirty) return;
    const timer = setTimeout(() => {
      saveMutation.mutate({ nextDraft: draft, nextWorkspace: allocationWorkspace });
    }, 900);
    return () => clearTimeout(timer);
  }, [draft, allocationWorkspace, dirty, saveMutation]);

  useEffect(() => {
    if (!mobilePanelRef.current) return;
    mobilePanelRef.current.scrollTo({ top: 0, behavior: "auto" });
  }, [mobileView, receiptId]);

  useEffect(() => {
    if (!draft) return;
    setAllocationWorkspace((previous) => reconcileWorkspaceToDraft(previous, draft, receiptQuery.data?.latest_twin ?? null));
  }, [draft, receiptQuery.data?.latest_twin]);

  useEffect(() => {
    if (!allocationWorkspace) return;
    setAllocationWarnings(allocationWorkspace.warnings ?? []);
  }, [allocationWorkspace]);

  // Accuracy celebration: detect status transition to "synced" (fires once per edge, never every poll)
  useEffect(() => {
    const nextStatus = receiptQuery.data?.status ?? null;
    const prevStatus = prevStatusRef.current;
    if (prevStatus !== null && prevStatus !== "synced" && nextStatus === "synced") {
      setShowSyncCelebration(true);
      toast({
        variant: "success",
        title: "Synced to YNAB",
        message: "Clean sync — no corrections needed.",
      });
      setTimeout(() => setShowSyncCelebration(false), 1700);
    }
    prevStatusRef.current = nextStatus;
  }, [receiptQuery.data?.status, toast]);

  // Auto-confirm twin sections when extraction is clean and both sections are unconfirmed.
  // Guards:
  //   - duplicate_review receipts: never auto-confirm (duplicate resolution flow owns the UI)
  //   - extraction has warnings (schema_errors) or failed schema validation: skip
  //   - latest validation payload has ambiguity flags: skip (user should review)
  //   - per-browser once-per-receipt: localStorage key snappy_auto_confirm_done:<id>
  //   - per-mount guard: autoConfirmedRef (prevents double-fire on fast re-renders)
  useEffect(() => {
    const receipt = receiptQuery.data;
    if (!receipt) return;

    // FIX 2a: duplicate_review receipts must never be auto-confirmed
    if (receipt.status === "duplicate_review") return;

    // Per-mount guard
    if (autoConfirmedRef.current.has(receipt.id)) return;

    // FIX 3: localStorage persistence — skip if already auto-confirmed this browser session
    const lsKey = `snappy_auto_confirm_done:${receipt.id}`;
    try {
      if (typeof window !== "undefined" && window.localStorage.getItem(lsKey) === "true") return;
    } catch {
      // SSR or privacy mode — fall through; per-mount ref still guards double-fire
    }

    const twin = receipt.latest_twin;
    if (!twin) return;

    const extraction = receipt.latest_extraction;
    // FIX 2b: skip if extraction failed schema validation or has errors
    if (!extraction?.schema_valid || (extraction.schema_errors?.length ?? 0) > 0) return;

    // FIX 2c: skip if the extraction payload carries ambiguity flags (uncertain categories)
    const rawFlags = (extraction.parsed_json as Record<string, unknown> | null)?.category_ambiguity_flags;
    if (Array.isArray(rawFlags) && rawFlags.length > 0) return;

    const dateConfirmed = twin.confirmed_sections?.date_time ?? false;
    const totalConfirmed = twin.confirmed_sections?.total ?? false;

    // Only auto-confirm if BOTH sections are unconfirmed (brand new receipt, not one the user rejected)
    if (dateConfirmed || totalConfirmed) return;

    // Mark as processed in per-mount ref so we don't re-run in this component lifetime
    autoConfirmedRef.current.add(receipt.id);
    // FIX 3: persist to localStorage so remount (back-navigate, page reload) doesn't re-fire
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(lsKey, "true");
      }
    } catch {
      // SSR or privacy mode — per-mount ref still prevents double-fire
    }

    const confirmSection = async (section: "date_time" | "total") => {
      try {
        await confirmTwinSection(receipt.id, { section, confirmed: true });
      } catch {
        // Silent fail — user can manually confirm
      }
    };

    void confirmSection("date_time").then(() => confirmSection("total")).then(() => {
      queryClient.invalidateQueries({ queryKey: ["receipt", receipt.id] });
    });
  }, [receiptQuery.data, queryClient]);

  const categories = useMemo(
    () =>
      (cacheQuery.data ?? [])
        .filter((item) => item.entity_type === "category")
        .map((item) => ({
          entity_id: String(item.entity_id ?? "").trim(),
          name: String(item.name ?? "").trim(),
          group_name: item.group_name == null ? null : String(item.group_name),
        }))
        .filter((item) => item.entity_id.length > 0 && item.name.length > 0),
    [cacheQuery.data],
  );
  const accounts = useMemo(
    () =>
      (cacheQuery.data ?? [])
        .filter((item) => item.entity_type === "account")
        .map((item) => ({
          entity_id: String(item.entity_id ?? "").trim(),
          name: String(item.name ?? "").trim() || "Unknown account",
        }))
        .filter((item) => item.entity_id.length > 0),
    [cacheQuery.data],
  );
  const payees = useMemo(
    () =>
      (cacheQuery.data ?? [])
        .filter((item) => item.entity_type === "payee")
        .map((item) => ({
          entity_id: String(item.entity_id ?? "").trim(),
          name: String(item.name ?? "").trim(),
        }))
        .filter((item) => item.entity_id.length > 0 && item.name.length > 0),
    [cacheQuery.data],
  );
  const categoryIds = useMemo(() => new Set(categories.map((c) => c.entity_id)), [categories]);
  const accountIds = useMemo(() => new Set(accounts.map((a) => a.entity_id)), [accounts]);

  const validationErrors = useMemo(
    () => (draft ? validateDraft(draft, { categoryIds, accountIds }) : []),
    [draft, categoryIds, accountIds],
  );
  const dateTimeConfirmed = receiptQuery.data?.latest_twin?.confirmed_sections.date_time ?? false;
  const totalConfirmed = receiptQuery.data?.latest_twin?.confirmed_sections.total ?? false;
  // autoConfirmed: true when the localStorage marker exists for this receipt AND both sections still confirmed.
  // Using useMemo so it recomputes whenever the twin confirmation state changes.
  const autoConfirmed = useMemo(() => {
    if (!dateTimeConfirmed || !totalConfirmed) return false;
    try {
      return typeof window !== "undefined" && window.localStorage.getItem(`snappy_auto_confirm_done:${receiptId}`) === "true";
    } catch {
      return false;
    }
  }, [receiptId, dateTimeConfirmed, totalConfirmed]);
  const twinConfirmationErrors = useMemo(() => {
    const errors: string[] = [];
    if (!dateTimeConfirmed) errors.push("Confirm the date & time in Receipt details before syncing");
    if (!totalConfirmed) errors.push("Confirm the total in Receipt details before syncing");
    return errors;
  }, [dateTimeConfirmed, totalConfirmed]);

  const payeeSuggestions = useMemo(() => {
    if (!draft) return [];
    const query = draft.payee_name.trim().toLowerCase();
    if (!query) return [];
    const seen = new Set<string>();
    return payees
      .filter((payee) => {
        const normalizedName = String(payee.name ?? "").toLowerCase();
        if (!normalizedName.includes(query) || seen.has(normalizedName)) return false;
        seen.add(normalizedName);
        return true;
      })
      .slice(0, PAYEE_SUGGESTION_LIMIT);
  }, [draft, payees]);

  const isDuplicateReview = receiptQuery.data?.status === "duplicate_review";
  const config = configQuery.data ?? null;

  // Strip reasons — unified blocking list shown above ActionButtonBar
  const stripReasons = useMemo(() => {
    const reasons: string[] = [...twinConfirmationErrors, ...validationErrors];
    if (config && !config.ynab_sync_enabled) {
      reasons.push("YNAB sync is disabled");
    }
    if (dirty || saveMutation.isPending) {
      reasons.push("Unsaved changes — waiting for autosave");
    }
    return reasons;
  }, [twinConfirmationErrors, validationErrors, config, dirty, saveMutation.isPending]);

  // syncReadinessErrors kept for backward compat / canSync gate
  const syncReadinessErrors = useMemo(() => [...twinConfirmationErrors, ...validationErrors], [twinConfirmationErrors, validationErrors]);
  const canSync = !!draft && syncReadinessErrors.length === 0 && !saveMutation.isPending && !recomputeMutation.isPending && !dirty && !isDuplicateReview;

  // Confirm-dialog guard — disabled when any readiness gate is blocking
  const isConfirmDisabled =
    dirty ||
    saveMutation.isPending ||
    recomputeMutation.isPending ||
    syncReadinessErrors.length > 0 ||
    isDuplicateReview;
  const confirmDisabledReason: string | undefined = (() => {
    if (!isConfirmDisabled) return undefined;
    if (dirty || saveMutation.isPending) return "Finish saving the draft before syncing.";
    // First reason from stripReasons (twinConfirmationErrors + validationErrors + sync-disabled)
    return stripReasons[0];
  })();
  const isSplitMode = !!draft && draft.splits.length > 0;
  const splitTotal = draft ? draft.splits.reduce((sum, split) => sum + Math.round(Number(split.amount || 0) * 100), 0) / 100 : 0;
  const accountNeedsAttention = draft?.account_id === UNKNOWN_ACCOUNT_ID;
  const ambiguityFlags = useMemo(
    () => parseCategoryAmbiguityFlags((receiptQuery.data?.latest_extraction?.parsed_json ?? null) as Record<string, unknown> | null),
    [receiptQuery.data?.latest_extraction?.parsed_json],
  );
  const canResetToBaseline = useMemo(() => {
    if (!draft || !cancelBaseline) return false;
    const draftChanged = JSON.stringify(draft) !== JSON.stringify(cancelBaseline);
    const workspaceChanged = JSON.stringify(allocationWorkspace ?? null) !== JSON.stringify(cancelBaselineWorkspace ?? null);
    return draftChanged || workspaceChanged;
  }, [draft, cancelBaseline, allocationWorkspace, cancelBaselineWorkspace]);
  const duplicateMatchId = receiptQuery.data?.duplicate_of_receipt_id ?? null;
  const duplicateMatchQuery = useQuery({
    queryKey: ["receipt", duplicateMatchId],
    queryFn: () => getReceiptDetail(duplicateMatchId as string),
    enabled: Boolean(isDuplicateReview && duplicateMatchId),
    refetchInterval: 6000,
  });

  if (receiptQuery.isError) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-6">
        <Card>
          <p className="text-sm text-red-700">Failed to load receipt details. Verify the receipt ID and try again.</p>
          <Link href="/" className="mt-3 inline-flex text-sm font-semibold text-ink/70">Back to queue</Link>
        </Card>
      </main>
    );
  }

  if (receiptQuery.isLoading || !receiptQuery.data || !draft) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-6">
        <p className="text-sm">Loading receipt...</p>
      </main>
    );
  }

  const receipt = receiptQuery.data;
  const correctionHistory = Array.isArray(receipt.correction_history) ? receipt.correction_history : [];
  const isSyncing = syncMutation.isPending || receipt.status === "syncing";
  const syncButtonLabel = isSyncing ? "Syncing" : receipt.has_successful_sync ? "Resync to YNAB" : "Sync to YNAB";
  const resetDraft = () => {
    if (!cancelBaseline) return;
    setDirty(false);
    setPayeeMenuOpen(false);
    setDraft(cancelBaseline);
    setAllocationWorkspace(cancelBaselineWorkspace);
    setAllocationWarnings(cancelBaselineWorkspace?.warnings ?? []);
    setSelectedAllocationItemIds(new Set());
    saveMutation.mutate({ nextDraft: cancelBaseline, nextWorkspace: cancelBaselineWorkspace });
  };
  const runAllocationRecompute = (
    mode: "discard_manual_amounts" | "keep_manual_amounts",
    workspaceOverride?: AllocationWorkspace,
  ) => {
    if (!draft || !allocationWorkspace) return;
    const sourceWorkspace = workspaceOverride ?? allocationWorkspace;
    recomputeMutation.mutate(
      { workspace: sourceWorkspace, mode },
      {
        onSuccess: (result) => {
          const nextDraft = {
            ...toDraftFromPayload(
              result.payload as unknown as Record<string, unknown>,
              draft.payee_name,
            ),
            // Recompute rewrites splits — this is not a loaded memory assignment,
            // so clear the provenance chip regardless of what the payload carries.
            category_source: undefined,
          };
          const nextWorkspace = workspaceFromApi(
            result.workspace,
            nextDraft,
            receiptQuery.data?.latest_twin ?? null,
          );
          setDraft(nextDraft);
          setAllocationWorkspace(nextWorkspace);
          setAllocationWarnings(result.warnings ?? nextWorkspace.warnings ?? []);
          setDirty(true);
        },
        onError: (error) => {
          const message = error instanceof Error && error.message.trim() ? error.message : "Failed to recompute allocations";
          setAllocationWarnings((previous) => [...previous, message]);
        },
      },
    );
  };
  const handleMoveAllocatedItems = (itemIds: string[], laneId: string) => {
    if (!allocationWorkspace || itemIds.length === 0) return;
    const nextWorkspace = moveWorkspaceItems(allocationWorkspace, itemIds, laneId);
    setAllocationWorkspace(nextWorkspace);
    setSelectedAllocationItemIds(new Set());
    setDirty(true);
    runAllocationRecompute("keep_manual_amounts", nextWorkspace);
  };
  const handleSplitAmountPinned = (index: number, amount: number) => {
    setAllocationWorkspace((previous) => {
      if (!previous) return previous;
      return setWorkspaceLanePinnedAmount(previous, `split-${index}`, amount);
    });
    setDirty(true);
  };
  const handleWorkspaceChange = (next: AllocationWorkspace) => {
    setAllocationWorkspace(next);
    setDirty(true);
  };
  const handleRefreshFromTwin = () => {
    if (!draft) return;
    const twin = receiptQuery.data?.latest_twin ?? null;
    // Rebuild from current twin items, preserving pins for lanes that still exist.
    const fresh = buildFallbackWorkspace(draft, twin);
    if (allocationWorkspace) {
      const existingPins = new Map(
        allocationWorkspace.lanes.map((lane) => [lane.lane_id, lane.pinned_amount ?? null]),
      );
      fresh.lanes = fresh.lanes.map((lane) => ({
        ...lane,
        pinned_amount: existingPins.get(lane.lane_id) ?? null,
      }));
    }
    const nextWarnings = (fresh.warnings ?? []).filter((w) => !w.includes("Line items changed"));
    setAllocationWorkspace({ ...fresh, warnings: nextWarnings });
    setAllocationWarnings(nextWarnings);
    setDirty(true);
  };
  const refreshReceiptContext = () => {
    queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
    queryClient.invalidateQueries({ queryKey: ["receipts"] });
    queryClient.invalidateQueries({ queryKey: ["stats"] });
  };
  const handleConfirmDuplicate = () => {
    if (!window.confirm("Confirm duplicate and permanently discard the incoming receipt data and scan?")) {
      return;
    }
    confirmDuplicateMutation.mutate(undefined, {
      onSuccess: (result) => {
        router.push(`/receipts/${result.kept_receipt_id}`);
      },
    });
  };
  const handleOverrideDuplicate = () => {
    if (!window.confirm("Confirm this duplicate detection is inaccurate and continue to manual editing?")) {
      return;
    }
    overrideDuplicateMutation.mutate();
  };

  // Derive last dry-run transaction for the preview dialog (non-hook; defined after early-return guards)
  const latestSync = receipt.latest_sync;
  const lastDryRunTransaction: Record<string, unknown> | null = (() => {
    if (!latestSync || latestSync.status !== "dry_run") return null;
    const rawReq = latestSync.raw_request;
    if (!rawReq) return null;
    const txn = (rawReq as Record<string, unknown>).transaction;
    return txn && typeof txn === "object" ? (txn as Record<string, unknown>) : null;
  })();

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-4 px-4 pb-28 pt-4">
      <header className="animate-reveal rounded-3xl bg-white/90 p-4 shadow-float">
        <Link href="/" className="inline-flex items-center gap-1.5 rounded-full border border-ink/20 bg-white/80 px-4 py-2 text-sm font-medium text-ink/80 hover:bg-white transition shadow-sm">
          <ArrowLeft className="h-4 w-4" />
          Back
        </Link>
        <div className="mt-3 flex items-start justify-between gap-2">
          <div>
            <h1 className="font-[var(--font-heading)] text-xl font-bold">{receipt.display_payee_name ?? receipt.original_filename}</h1>
            <p className="mt-1 text-sm text-ink/70">
              Total{" "}
              {receipt.display_total_milliunits != null && draft
                ? formatSignedDollarsWithDirection(
                    signedDollars(receipt.display_total_milliunits / 1000, draft.transaction_kind),
                    draft.transaction_kind,
                  )
                : "--"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {receipt.status !== "synced" && receipt.status !== "syncing" ? (
              <Button
                variant="outline"
                size="sm"
                data-testid="forget-receipt-button"
                className="h-8 gap-1 border-red-200 text-red-600 hover:bg-red-50"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete receipt
              </Button>
            ) : null}
            <StatusBadge status={receipt.status} />
          </div>
        </div>
        {receipt.status_reason ? <p className="mt-2 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{receipt.status_reason}</p> : null}
        {receipt.status === "error_extract" ? (
          <p className="mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Something went wrong reading this receipt. Fix the fields below (the edits save automatically) to recover it — or delete it if it’s not worth keeping.
          </p>
        ) : null}
        {receipt.correction_message ? (
          <p className="mt-2 inline-flex items-start gap-1 rounded-xl border border-slate-300 bg-slate-100 px-3 py-2 text-xs text-slate-800">
            <Flame className="mt-0.5 h-3.5 w-3.5 text-slate-500" />
            {receipt.correction_message}
          </p>
        ) : null}
      </header>

      {isDuplicateReview ? (
        <DuplicateReviewSection
          receipt={receipt}
          matchedReceipt={duplicateMatchQuery.data ?? null}
          isLoadingMatch={duplicateMatchQuery.isLoading}
          onConfirmDuplicate={handleConfirmDuplicate}
          onOverrideDuplicate={handleOverrideDuplicate}
          isConfirmingDuplicate={confirmDuplicateMutation.isPending}
          isOverridingDuplicate={overrideDuplicateMutation.isPending}
        />
      ) : (
        <>
          <TwinAndScanSection
            receiptId={receiptId}
            receipt={receipt}
            mobileView={mobileView}
            setMobileView={setMobileView}
            mobilePanelRef={mobilePanelRef}
            onTwinUpdated={() => { setDirty(false); refreshReceiptContext(); }}
            autoConfirmed={autoConfirmed}
          />

          <PayeeAccountCard
            draft={draft}
            setDraft={setDraft}
            setDirty={setDirty}
            accounts={accounts}
            payeeSuggestions={payeeSuggestions}
            payeeMenuOpen={payeeMenuOpen}
            setPayeeMenuOpen={setPayeeMenuOpen}
            accountNeedsAttention={accountNeedsAttention}
            cardLastFour={
              (receiptQuery.data?.latest_extraction?.parsed_json as Record<string, unknown> | null)?.card_last_four as string | null ?? null
            }
            latestValidationPayload={receiptQuery.data?.latest_validation?.payload ?? null}
          />

          {ambiguityFlags.length > 0 ? (
            <section className="animate-reveal rounded-2xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900" style={{ animationDelay: "95ms" }}>
              <p className="inline-flex items-center gap-1 font-semibold">
                <Droplets className="h-3.5 w-3.5" />
                Not sure about a category? Picking the right one earns water
              </p>
              {ambiguityFlags.slice(0, 3).map((flag, index) => (
                <p key={`${flag.line_item}-${index}`} className="mt-1 text-[11px] text-sky-800">
                  {flag.line_item || "Item"}: {flag.note || "Could belong to multiple categories."}
                </p>
              ))}
            </section>
          ) : null}

          <MemoCard
            draft={draft}
            setDraft={setDraft}
            setDirty={setDirty}
          />

          <CategorySplitCard
            draft={draft}
            setDraft={setDraft}
            setDirty={setDirty}
            categories={categories}
            isSplitMode={isSplitMode}
            splitTotal={splitTotal}
            onSplitAmountEdited={handleSplitAmountPinned}
          />

          {allocationWorkspace ? (
            <AllocationBoard
              workspace={allocationWorkspace}
              categories={categories}
              selectedItemIds={selectedAllocationItemIds}
              onToggleItem={(itemId) => {
                setSelectedAllocationItemIds((previous) => {
                  const next = new Set(previous);
                  if (next.has(itemId)) next.delete(itemId);
                  else next.add(itemId);
                  return next;
                });
              }}
              onClearSelection={() => setSelectedAllocationItemIds(new Set())}
              onMoveItems={handleMoveAllocatedItems}
              onRecomputeDiscard={() => {
                if (!allocationWorkspace) return;
                const nextWorkspace = clearWorkspacePins(allocationWorkspace);
                setAllocationWorkspace(nextWorkspace);
                runAllocationRecompute("discard_manual_amounts", nextWorkspace);
              }}
              onRecomputeKeep={() => runAllocationRecompute("keep_manual_amounts")}
              isRecomputing={recomputeMutation.isPending}
              warnings={allocationWarnings}
              onWorkspaceChange={handleWorkspaceChange}
              onRefreshFromTwin={handleRefreshFromTwin}
            />
          ) : null}

          <ValidationStatusSection
            isAutosaving={saveMutation.isPending}
            dirty={dirty}
            lockWarnings={lockWarnings}
            correctionHistory={correctionHistory}
          />

          <SyncStatusStrip reasons={stripReasons} />

          {/* Accuracy celebration — fires on synced state edge, never on button press */}
          {showSyncCelebration ? (
            <div className="flex justify-center py-1">
              <SnappyCelebration />
            </div>
          ) : null}

          <ActionButtonBar
            isSyncing={isSyncing}
            onReset={resetDraft}
            canReset={canResetToBaseline}
            isAutosaving={saveMutation.isPending}
            onSync={() => {
              let skipEnabled = false;
              try {
                skipEnabled = typeof window !== "undefined" && window.localStorage.getItem("snappy_skip_preview") === "true";
              } catch {
                // SSR or privacy mode — default to showing the dialog
              }
              if (shouldSkipPreview({ stripReasons, lockWarnings, ambiguityFlags, skipEnabled })) {
                syncMutation.mutate();
                toast({
                  variant: "success",
                  message: "Synced without preview",
                  action: {
                    label: "Show previews again",
                    onClick: () => {
                      try {
                        if (typeof window !== "undefined") {
                          window.localStorage.removeItem("snappy_skip_preview");
                        }
                      } catch {
                        // SSR or privacy mode
                      }
                    },
                  },
                });
              } else {
                setPreviewOpen(true);
              }
            }}
            canSync={canSync}
            syncButtonLabel={syncButtonLabel}
            stripReasons={stripReasons}
          />

          {/* Sync preview/confirm dialog */}
          <SyncPreviewDialog
            open={previewOpen}
            onClose={() => setPreviewOpen(false)}
            draft={draft}
            accounts={accounts}
            categories={categories}
            hasSuccessfulSync={receipt.has_successful_sync}
            mode={{
              dryRun: config?.ynab_dry_run ?? true,
              syncEnabled: config?.ynab_sync_enabled ?? false,
              budgetName: config?.ynab_budget_name ?? null,
              budgetId: config?.ynab_budget_id ?? null,
              newFlagColor: config?.new_transaction_flag_color ?? "green",
              updatedFlagColor: config?.updated_transaction_flag_color ?? "blue",
            }}
            lastDryRunTransaction={lastDryRunTransaction}
            isConfirmDisabled={isConfirmDisabled}
            confirmDisabledReason={confirmDisabledReason}
            isSyncing={isSyncing}
            dateTimeConfirmed={dateTimeConfirmed}
            totalConfirmed={totalConfirmed}
            onConfirm={() => syncMutation.mutate()}
          />
        </>
      )}
    </main>
  );
}
