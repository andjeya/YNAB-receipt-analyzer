"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";

import { enqueueSync, getReceiptDetail, getYnabCache, receiptFileUrl, saveDraft } from "@/lib/api";
import { ReceiptDetail, ValidationPayloadInput } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";

function toDraft(receipt: ReceiptDetail): ValidationPayloadInput {
  const payload = (receipt.latest_validation?.payload ?? receipt.latest_extraction?.parsed_json ?? {}) as Record<string, unknown>;
  const splitsSource = Array.isArray(payload.splits) ? payload.splits : [];

  return {
    payee_name: String(payload.payee_name ?? receipt.display_payee_name ?? ""),
    account_id: String(payload.account_id ?? ""),
    transaction_date: String(payload.transaction_date ?? new Date().toISOString().slice(0, 10)),
    memo: String(payload.memo ?? ""),
    total_amount: Number(payload.total_amount ?? 0),
    splits: splitsSource.map((split) => {
      const record = split as Record<string, unknown>;
      return {
        category_id: String(record.category_id ?? ""),
        amount: Number(record.amount ?? 0),
        memo: String(record.memo ?? ""),
      };
    }),
  };
}

function validateDraft(draft: ValidationPayloadInput): string[] {
  const errors: string[] = [];

  if (!draft.payee_name.trim()) errors.push("Payee is required");
  if (!draft.account_id.trim()) errors.push("Account is required");
  if (!draft.transaction_date.trim()) errors.push("Date is required");
  if (!Number.isFinite(draft.total_amount) || draft.total_amount <= 0) errors.push("Total must be > 0");

  if (!draft.splits.length) {
    errors.push("At least one split is required");
  } else {
    const splitTotal = draft.splits.reduce((sum, split) => sum + Number(split.amount || 0), 0);
    if (Math.abs(splitTotal - draft.total_amount) > 0.01) {
      errors.push("Split amounts must equal total amount");
    }
    draft.splits.forEach((split, index) => {
      if (!split.category_id) {
        errors.push(`Split ${index + 1}: category is required`);
      }
    });
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
  const [dirty, setDirty] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [forceCreate, setForceCreate] = useState(false);

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
    if (!receiptQuery.data || dirty) {
      return;
    }
    setDraft(toDraft(receiptQuery.data));
  }, [receiptQuery.data, dirty]);

  const saveMutation = useMutation({
    mutationFn: (nextDraft: ValidationPayloadInput) => saveDraft(receiptId, nextDraft),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => enqueueSync(receiptId, { force_create: forceCreate, allow_update_match: !forceCreate }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["receipt", receiptId] });
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
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

  const categories = useMemo(() => cacheQuery.data?.filter((item) => item.entity_type === "category") ?? [], [cacheQuery.data]);
  const accounts = useMemo(() => cacheQuery.data?.filter((item) => item.entity_type === "account") ?? [], [cacheQuery.data]);
  const payees = useMemo(() => cacheQuery.data?.filter((item) => item.entity_type === "payee") ?? [], [cacheQuery.data]);

  const validationErrors = useMemo(() => (draft ? validateDraft(draft) : []), [draft]);
  const canSync = !!draft && validationErrors.length === 0 && !saveMutation.isPending && !dirty;

  if (receiptQuery.isLoading || !receiptQuery.data || !draft) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-6">
        <p className="text-sm">Loading receipt...</p>
      </main>
    );
  }

  const receipt = receiptQuery.data;

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
        <Button variant="outline" size="sm" className="mt-3" onClick={() => setPreviewOpen(true)}>
          Open Receipt Preview
        </Button>
      </header>

      <Card className="animate-reveal space-y-3" style={{ animationDelay: "70ms" }}>
        <h2 className="font-semibold">Payee + Account</h2>
        <div className="grid gap-3">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Payee</label>
            <Input
              list="payee-options"
              value={draft.payee_name}
              onChange={(event) => {
                setDraft({ ...draft, payee_name: event.target.value });
                setDirty(true);
              }}
            />
            <datalist id="payee-options">
              {payees.map((payee) => (
                <option key={payee.entity_id} value={payee.name} />
              ))}
            </datalist>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink/70">Account</label>
            <Select
              value={draft.account_id}
              onChange={(event) => {
                setDraft({ ...draft, account_id: event.target.value });
                setDirty(true);
              }}
            >
              <option value="">Select account</option>
              {accounts.map((account) => (
                <option key={account.entity_id} value={account.entity_id}>
                  {account.name}
                </option>
              ))}
            </Select>
          </div>
        </div>
      </Card>

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
          <h2 className="font-semibold">Splits</h2>
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
              <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Item {index + 1}</p>
              <button
                type="button"
                className="inline-flex items-center text-red-600"
                onClick={() => {
                  const nextSplits = draft.splits.filter((_, splitIndex) => splitIndex !== index);
                  setDraft({ ...draft, splits: nextSplits });
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

              <Select
                value={split.category_id}
                onChange={(event) => {
                  const nextSplits = [...draft.splits];
                  nextSplits[index] = { ...split, category_id: event.target.value };
                  setDraft({ ...draft, splits: nextSplits });
                  setDirty(true);
                }}
              >
                <option value="">Move item to category</option>
                {categories.map((category) => (
                  <option key={category.entity_id} value={category.entity_id}>
                    {category.group_name ? `${category.group_name} / ` : ""}
                    {category.name}
                  </option>
                ))}
              </Select>

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
      </Card>

      <section className="animate-reveal rounded-2xl bg-white/80 p-3 text-xs text-ink/70" style={{ animationDelay: "210ms" }}>
        <p className="mb-2 font-semibold text-ink/70">
          {saveMutation.isPending ? "Autosaving..." : dirty ? "Changes pending autosave" : "Draft saved"}
        </p>
        <label className="mb-2 flex items-center gap-2 text-ink/80">
          <input type="checkbox" checked={forceCreate} onChange={(event) => setForceCreate(event.target.checked)} />
          Force create new YNAB transaction (skip match/update)
        </label>
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

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink/15 bg-white/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <Button
            variant="outline"
            className="flex-1"
            onClick={() => saveMutation.mutate(draft)}
            disabled={saveMutation.isPending}
          >
            Save Draft
          </Button>
          <Button
            className="flex-1"
            onClick={() => syncMutation.mutate()}
            disabled={!canSync || syncMutation.isPending}
          >
            Sync to YNAB
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
                <img src={receiptFileUrl(receiptId)} alt={receipt.original_filename} className="h-full w-full object-contain" />
              ) : (
                <iframe src={receiptFileUrl(receiptId)} title="Receipt preview" className="h-full w-full border-0" />
              )}
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
