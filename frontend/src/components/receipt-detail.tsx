"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Droplets, Flame, Plus, Trash2 } from "lucide-react";

import { enqueueSync, getReceiptDetail, getYnabCache, receiptFileUrl, rejectReceipt, saveDraft } from "@/lib/api";
import { ReceiptDetail, ValidationPayloadInput } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";

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

  return (
    <div className="relative">
      <Input
        value={inputValue}
        placeholder={placeholder}
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
        <div className="absolute z-20 mt-1 max-h-60 w-full overflow-y-auto rounded-xl border border-ink/15 bg-white shadow-float">
          {filteredCategories.length ? (
            filteredCategories.map((category) => (
              <button
                key={category.entity_id}
                type="button"
                className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-sand/70"
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

function toDraftFromPayload(payload: Record<string, unknown>, fallbackPayee: string): ValidationPayloadInput {
  const splitsSource = Array.isArray(payload.splits) ? payload.splits : [];
  const parsedSplits = splitsSource.map((split) => {
    const record = split as Record<string, unknown>;
    return {
      category_id: String(record.category_id ?? ""),
      amount: Number(record.amount ?? 0),
      memo: String(record.memo ?? ""),
    };
  });

  let categoryId = String(payload.category_id ?? "");
  const splits = parsedSplits;

  if (splits.length > 0) {
    categoryId = "";
  }

  const transactionTimeRaw = String(payload.transaction_time ?? "").trim();
  const transactionTime = transactionTimeRaw ? transactionTimeRaw.slice(0, 5) : "";

  return {
    payee_name: String(payload.payee_name ?? fallbackPayee ?? ""),
    account_id: String(payload.account_id ?? ""),
    transaction_date: String(payload.transaction_date ?? ""),
    transaction_time: transactionTime,
    memo: String(payload.memo ?? ""),
    total_amount: Number(payload.total_amount ?? 0),
    category_id: categoryId,
    splits,
  };
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

function toModelBaselineDraft(receipt: ReceiptDetail): ValidationPayloadInput {
  const payload = (receipt.model_validation?.payload ?? receipt.latest_extraction?.parsed_json ?? {}) as Record<string, unknown>;
  return toDraftFromPayload(payload, receipt.display_payee_name ?? "");
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
  if (!draft.transaction_date.trim()) errors.push("Date is required");
  if (!Number.isFinite(draft.total_amount) || draft.total_amount <= 0) errors.push("Total must be > 0");

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

function formatAmount(value: number | null): string {
  if (value == null) return "--";
  return `$${Math.abs(value / 1000).toFixed(2)}`;
}

export function ReceiptDetailView({ receiptId }: { receiptId: string }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ValidationPayloadInput | null>(null);
  const [cancelBaseline, setCancelBaseline] = useState<ValidationPayloadInput | null>(null);
  const [baselineReceiptId, setBaselineReceiptId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [payeeMenuOpen, setPayeeMenuOpen] = useState(false);

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

  useEffect(() => {
    if (!receiptQuery.data) {
      return;
    }
    if (baselineReceiptId !== receiptQuery.data.id) {
      setBaselineReceiptId(receiptQuery.data.id);
      setCancelBaseline(toModelBaselineDraft(receiptQuery.data));
      setDraft(toDraft(receiptQuery.data));
      setDirty(false);
      return;
    }
    if (!dirty) {
      setDraft(toDraft(receiptQuery.data));
    }
  }, [receiptQuery.data, baselineReceiptId, dirty]);

  const saveMutation = useMutation({
    mutationFn: (nextDraft: ValidationPayloadInput) =>
      saveDraft(receiptId, {
        ...nextDraft,
        transaction_time: nextDraft.transaction_time?.trim() ? nextDraft.transaction_time : null,
      }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => enqueueSync(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => rejectReceipt(receiptId),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["game-dashboard"] });
    },
  });

  useEffect(() => {
    if (!draft || !dirty) {
      return;
    }
    const timer = setTimeout(() => {
      saveMutation.mutate(draft);
    }, 900);
    return () => clearTimeout(timer);
  }, [draft, dirty, saveMutation]);

  const categories = useMemo(
    () => (cacheQuery.data?.filter((item) => item.entity_type === "category") ?? []) as CategoryOption[],
    [cacheQuery.data],
  );
  const accounts = useMemo(() => cacheQuery.data?.filter((item) => item.entity_type === "account") ?? [], [cacheQuery.data]);
  const payees = useMemo(() => cacheQuery.data?.filter((item) => item.entity_type === "payee") ?? [], [cacheQuery.data]);

  const categoryIds = useMemo(() => new Set(categories.map((category) => category.entity_id)), [categories]);
  const accountIds = useMemo(() => new Set(accounts.map((account) => account.entity_id)), [accounts]);

  const validationErrors = useMemo(
    () => (draft ? validateDraft(draft, { categoryIds, accountIds }) : []),
    [draft, categoryIds, accountIds],
  );
  const payeeSuggestions = useMemo(() => {
    if (!draft) return [];
    const query = draft.payee_name.trim().toLowerCase();
    if (!query) return [];
    const seen = new Set<string>();
    return payees
      .filter((payee) => {
        const normalizedName = payee.name.toLowerCase();
        if (!normalizedName.includes(query) || seen.has(normalizedName)) {
          return false;
        }
        seen.add(normalizedName);
        return true;
      })
      .slice(0, PAYEE_SUGGESTION_LIMIT);
  }, [draft, payees]);
  const canSync = !!draft && validationErrors.length === 0 && !saveMutation.isPending && !dirty;
  const isSplitMode = !!draft && draft.splits.length > 0;
  const splitTotal = draft ? draft.splits.reduce((sum, split) => sum + Number(split.amount || 0), 0) : 0;
  const accountNeedsAttention = draft?.account_id === UNKNOWN_ACCOUNT_ID;
  const ambiguityFlags = useMemo(
    () => parseCategoryAmbiguityFlags((receiptQuery.data?.latest_extraction?.parsed_json ?? null) as Record<string, unknown> | null),
    [receiptQuery.data?.latest_extraction?.parsed_json],
  );
  const canResetToBaseline =
    !!draft &&
    !!cancelBaseline &&
    JSON.stringify(draft) !== JSON.stringify(cancelBaseline);

  if (receiptQuery.isError) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-6">
        <Card>
          <p className="text-sm text-red-700">Failed to load receipt details. Verify the receipt ID and try again.</p>
          <Link href="/" className="mt-3 inline-flex text-sm font-semibold text-ink/70">
            Back to queue
          </Link>
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
  const isSyncing = syncMutation.isPending || receipt.status === "syncing";
  const hadPriorSync = receipt.has_successful_sync;
  const syncButtonLabel = isSyncing ? "Syncing" : hadPriorSync ? "Resync to YNAB" : "Sync to YNAB";
  const resetDraft = () => {
    if (!cancelBaseline) {
      return;
    }
    setDirty(false);
    setPayeeMenuOpen(false);
    setDraft(cancelBaseline);
    saveMutation.mutate(cancelBaseline);
  };

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 pb-28 pt-4">
      <header className="animate-reveal rounded-3xl bg-white/90 p-4 shadow-float">
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-ink/70">
          <ArrowLeft className="h-4 w-4" /> Back
        </Link>
        <div className="mt-3 flex items-start justify-between gap-2">
          <div>
            <h1 className="font-[var(--font-heading)] text-xl font-bold">{receipt.display_payee_name ?? receipt.original_filename}</h1>
            <p className="mt-1 text-sm text-ink/70">Total {formatAmount(receipt.display_total_milliunits)}</p>
          </div>
          <StatusBadge status={receipt.status} />
        </div>
        {receipt.status_reason ? <p className="mt-2 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{receipt.status_reason}</p> : null}
        {receipt.correction_message ? (
          <p className="mt-2 inline-flex items-start gap-1 rounded-xl border border-slate-300 bg-slate-100 px-3 py-2 text-xs text-slate-800">
            <Flame className="mt-0.5 h-3.5 w-3.5 text-slate-500" />
            {receipt.correction_message}
          </p>
        ) : null}
        <Button variant="outline" size="sm" className="mt-3" onClick={() => setPreviewOpen(true)}>
          Open Receipt Preview
        </Button>
      </header>

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "70ms" }}>
        <h2 className="font-semibold">Payee + Account</h2>
        <div className="grid gap-3">
          <div className="relative">
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Payee</label>
            <Input
              value={draft.payee_name}
              onFocus={() => setPayeeMenuOpen(true)}
              onBlur={() => {
                setTimeout(() => setPayeeMenuOpen(false), 120);
              }}
              onChange={(event) => {
                setDraft({ ...draft, payee_name: event.target.value });
                setDirty(true);
                setPayeeMenuOpen(true);
              }}
            />
            {payeeMenuOpen && payeeSuggestions.length > 0 ? (
              <div className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-xl border border-ink/15 bg-white shadow-float">
                {payeeSuggestions.map((payee) => (
                  <button
                    key={payee.entity_id}
                    type="button"
                    className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-sand/70"
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
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Account</label>
            <Select
              value={draft.account_id}
              className={accountNeedsAttention ? "border-amber-500 bg-amber-50 text-amber-900 focus:ring-amber-300" : undefined}
              onChange={(event) => {
                setDraft({ ...draft, account_id: event.target.value });
                setDirty(true);
              }}
            >
              <option value="">Select account</option>
              <option value={UNKNOWN_ACCOUNT_ID}>Unknown (needs review)</option>
              {accounts.map((account) => (
                <option key={account.entity_id} value={account.entity_id}>
                  {account.name}
                </option>
              ))}
            </Select>
            {accountNeedsAttention ? (
              <p className="mt-1 text-xs font-semibold text-amber-700">Unknown account selected. Sync is disabled until this is fixed.</p>
            ) : null}
          </div>
        </div>
      </Card>

      {ambiguityFlags.length > 0 ? (
        <section className="animate-reveal rounded-2xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900" style={{ animationDelay: "95ms" }}>
          <p className="inline-flex items-center gap-1 font-semibold">
            <Droplets className="h-3.5 w-3.5" />
            Extra water opportunity: category ambiguity detected
          </p>
          {ambiguityFlags.slice(0, 3).map((flag, index) => (
            <p key={`${flag.line_item}-${index}`} className="mt-1 text-[11px] text-sky-800">
              {flag.line_item || "Item"} ({Math.round(flag.confidence * 100)}%):{" "}
              {flag.note || "Could belong to multiple categories."}
            </p>
          ))}
        </section>
      ) : null}

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "120ms" }}>
        <h2 className="font-semibold">Date + Total</h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Date</label>
            <Input
              type="date"
              value={draft.transaction_date}
              onChange={(event) => {
                setDraft({ ...draft, transaction_date: event.target.value });
                setDirty(true);
              }}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Receipt time (optional)</label>
            <Input
              type="time"
              value={draft.transaction_time ?? ""}
              onChange={(event) => {
                setDraft({ ...draft, transaction_time: event.target.value || "" });
                setDirty(true);
              }}
            />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-1">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Total</label>
            <Input
              type="number"
              step="0.01"
              value={draft.total_amount}
              onChange={(event) => {
                setDraft({ ...draft, total_amount: Number(event.target.value) || 0 });
                setDirty(true);
              }}
            />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Memo</label>
          <Textarea
            rows={2}
            value={draft.memo}
            onChange={(event) => {
              setDraft({ ...draft, memo: event.target.value });
              setDirty(true);
            }}
          />
        </div>
      </Card>

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "170ms" }}>
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">{isSplitMode ? "Split Categories" : "Category"}</h2>
          <Button
            variant="outline"
            size="sm"
            className="gap-1"
            onClick={() => {
              if (isSplitMode) {
                const fallbackCategory = draft.splits.find((split) => split.category_id)?.category_id ?? draft.category_id;
                setDraft({ ...draft, category_id: fallbackCategory, splits: [] });
                setDirty(true);
                return;
              }

              setDraft({
                ...draft,
                category_id: "",
                splits: [{ category_id: draft.category_id, amount: draft.total_amount, memo: "" }],
              });
              setDirty(true);
            }}
          >
            {isSplitMode ? "Use Single Category" : "Split Transaction"}
          </Button>
        </div>

        {!isSplitMode ? (
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Category</label>
            <CategorySearchSelect
              value={draft.category_id}
              categories={categories}
              placeholder="Select or search category"
              onChange={(nextCategoryId) => {
                setDraft({ ...draft, category_id: nextCategoryId });
                setDirty(true);
              }}
            />
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between rounded-xl bg-sand/50 px-3 py-2 text-xs text-ink/75">
              <span>Split total: ${splitTotal.toFixed(2)}</span>
              <Button
                variant="outline"
                size="sm"
                className="gap-1"
                onClick={() => {
                  setDraft({
                    ...draft,
                    splits: [...draft.splits, { category_id: "", amount: 0, memo: "" }],
                  });
                  setDirty(true);
                }}
              >
                <Plus className="h-4 w-4" /> Add split
              </Button>
            </div>

            {draft.splits.map((split, index) => (
              <div key={`${index}-${split.category_id}`} className="rounded-2xl border border-ink/10 bg-sand/70 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Split {index + 1}</p>
                  <button
                    type="button"
                    className="inline-flex items-center text-red-600"
                    onClick={() => {
                      const nextSplits = draft.splits.filter((_, splitIndex) => splitIndex !== index);
                      if (nextSplits.length === 0) {
                        const fallbackCategory = split.category_id || draft.category_id;
                        setDraft({ ...draft, category_id: fallbackCategory, splits: [] });
                        setDirty(true);
                        return;
                      }
                      setDraft({ ...draft, category_id: "", splits: nextSplits });
                      setDirty(true);
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>

                <div className="grid gap-2">
                  <Input
                    type="number"
                    step="0.01"
                    value={split.amount}
                    onChange={(event) => {
                      const nextSplits = [...draft.splits];
                      nextSplits[index] = { ...split, amount: Number(event.target.value) || 0 };
                      setDraft({ ...draft, splits: nextSplits });
                      setDirty(true);
                    }}
                  />

                  <CategorySearchSelect
                    value={split.category_id}
                    categories={categories}
                    placeholder="Move item to category"
                    onChange={(nextCategoryId) => {
                      const nextSplits = [...draft.splits];
                      nextSplits[index] = { ...split, category_id: nextCategoryId };
                      setDraft({ ...draft, splits: nextSplits });
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

      <section className="animate-reveal rounded-2xl bg-white/80 p-3 text-xs text-ink/70" style={{ animationDelay: "210ms" }}>
        <p className="mb-2 font-semibold text-ink/70">
          {saveMutation.isPending ? "Autosaving..." : dirty ? "Changes pending autosave" : "Draft saved"}
        </p>
        {validationErrors.length ? (
          <ul className="space-y-1 text-red-700">
            {validationErrors.map((error) => (
              <li key={error}>- {error}</li>
            ))}
          </ul>
        ) : (
          <p>Validation passes. Sync can run.</p>
        )}
      </section>

      {receipt.correction_history.length > 0 ? (
        <section className="animate-reveal rounded-2xl border border-black/20 bg-black/90 p-3 text-xs text-white" style={{ animationDelay: "225ms" }}>
          <p className="font-semibold">Correction history</p>
          {receipt.correction_history.slice(0, 3).map((item) => (
            <p key={item.id} className="mt-1 text-[11px] text-slate-200">
              {new Date(item.detected_at).toLocaleDateString()}: {item.note?.split("| sig=", 1)[0] ?? "Category corrected in YNAB"}
            </p>
          ))}
        </section>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink/15 bg-white/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <Button
            variant="outline"
            className="flex-1 border-red-300 text-red-700 hover:bg-red-50"
            onClick={() => rejectMutation.mutate()}
            disabled={rejectMutation.isPending || isSyncing}
          >
            {rejectMutation.isPending ? "Rejecting..." : "Reject"}
          </Button>
          <Button
            variant="outline"
            className="flex-1"
            onClick={resetDraft}
            disabled={!canResetToBaseline || saveMutation.isPending || rejectMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            className="flex-1"
            variant={isSyncing ? "outline" : "solid"}
            onClick={() => syncMutation.mutate()}
            disabled={!canSync || isSyncing}
          >
            {syncButtonLabel}
          </Button>
        </div>
      </div>

      {previewOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="relative flex h-full w-full max-w-4xl flex-col rounded-2xl bg-black/90 p-2">
            <Button variant="outline" size="sm" className="self-end bg-white" onClick={() => setPreviewOpen(false)}>
              Close
            </Button>
            <div className="mt-2 flex-1 overflow-hidden rounded-xl bg-white">
              {receipt.mime_type.startsWith("image/") ? (
                <div className="relative h-full w-full">
                  <Image
                    src={receiptFileUrl(receiptId)}
                    alt={receipt.original_filename}
                    fill
                    unoptimized
                    className="object-contain"
                  />
                </div>
              ) : (
                <object
                  data={`${receiptFileUrl(receiptId)}#toolbar=1&view=FitH`}
                  type="application/pdf"
                  className="h-full w-full"
                >
                  <iframe src={receiptFileUrl(receiptId)} title="Receipt preview" className="h-full w-full border-0" />
                </object>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
