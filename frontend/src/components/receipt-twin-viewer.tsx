"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Pencil, RefreshCw, Save, X } from "lucide-react";

import { confirmTwinSection, retryTwinExtraction, saveReceiptTwin } from "@/lib/api";
import { ReceiptLineItem, ReceiptTwin, ReceiptTwinPayload } from "@/lib/types";
import { cloneTwinPayload, computeTwinEditWarnings, normalizeTwinTimeForInput } from "@/lib/receipt-twin";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type TwinSection = "date_time" | "total";

function toErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return "Request failed";
}

function formatCurrency(amount: number | null | undefined): string {
  if (amount == null || !Number.isFinite(amount)) return "--";
  return `$${amount.toFixed(2)}`;
}

function lineItemClassName(item: ReceiptLineItem): string {
  const kind = (item.item_type || "").toLowerCase();
  if (kind === "discount") return "text-red-700 italic";
  if (kind === "tax") return "text-ink/65";
  if (kind === "subtotal" || kind === "total") return "font-semibold";
  return "";
}

function patchNumberField(
  payload: ReceiptTwinPayload,
  index: number,
  key: "quantity" | "unit_price" | "line_total",
  rawValue: string,
): ReceiptTwinPayload {
  const next = cloneTwinPayload(payload);
  const parsed = rawValue.trim() === "" ? null : Number(rawValue);
  if (parsed !== null && Number.isNaN(parsed)) {
    return payload;
  }
  next.line_items[index] = { ...next.line_items[index], [key]: parsed };
  return next;
}

export function ReceiptTwinViewer({
  receiptId,
  twin,
  onUpdated,
}: {
  receiptId: string;
  twin: ReceiptTwin | null;
  onUpdated: () => void;
}) {
  const [editMode, setEditMode] = useState(false);
  const [sectionEditMode, setSectionEditMode] = useState<TwinSection | null>(null);
  const [allowRawEdit, setAllowRawEdit] = useState(false);
  const [draft, setDraft] = useState<ReceiptTwinPayload | null>(twin ? cloneTwinPayload(twin.payload) : null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!editMode && !sectionEditMode) {
      setDraft(twin ? cloneTwinPayload(twin.payload) : null);
      setAllowRawEdit(false);
      setActionError(null);
    }
  }, [editMode, sectionEditMode, twin]);

  const isDirty = useMemo(() => {
    if (!twin || !draft) return false;
    return JSON.stringify(draft) !== JSON.stringify(twin.payload);
  }, [draft, twin]);

  useEffect(() => {
    if (!editMode || !isDirty) return;

    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [editMode, isDirty]);

  const saveMutation = useMutation({
    mutationFn: (payload: ReceiptTwinPayload) =>
      saveReceiptTwin(receiptId, {
        base_version: twin?.version ?? 0,
        payload,
        source: "user",
      }),
    onSuccess: () => {
      setEditMode(false);
      setActionError(null);
      onUpdated();
    },
    onError: (error) => {
      setActionError(toErrorMessage(error));
    },
  });

  const confirmMutation = useMutation({
    mutationFn: ({ section, confirmed }: { section: "date_time" | "total"; confirmed: boolean }) =>
      confirmTwinSection(receiptId, { section, confirmed }),
    onSuccess: () => {
      setActionError(null);
      onUpdated();
    },
    onError: (error) => {
      setActionError(toErrorMessage(error));
    },
  });

  const retryMutation = useMutation({
    mutationFn: () => retryTwinExtraction(receiptId),
    onSuccess: () => {
      setActionError(null);
      onUpdated();
    },
    onError: (error) => {
      setActionError(toErrorMessage(error));
    },
  });

  const warnings = useMemo(() => {
    if (!draft) return [];
    return computeTwinEditWarnings(draft);
  }, [draft]);

  if (!twin || !draft) {
    return (
      <Card className="space-y-3">
        <div>
          <h2 className="font-semibold">Receipt Twin</h2>
          <p className="mt-1 text-xs text-ink/70">Twin unavailable for this receipt.</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-2"
          onClick={() => retryMutation.mutate()}
          disabled={retryMutation.isPending}
        >
          <RefreshCw className="h-4 w-4" />
          {retryMutation.isPending ? "Retrying extraction..." : "Retry extraction"}
        </Button>
        {actionError ? <p className="text-xs text-red-700">{actionError}</p> : null}
      </Card>
    );
  }

  const dateTimeConfirmed = twin.confirmed_sections.date_time;
  const totalConfirmed = twin.confirmed_sections.total;
  const isDateTimeEditing = editMode || sectionEditMode === "date_time";
  const isTotalEditing = editMode || sectionEditMode === "total";
  const isDateTimeDirty =
    !!draft &&
    ((draft.transaction_date ?? null) !== (twin.payload.transaction_date ?? null) ||
      (draft.transaction_time ?? null) !== (twin.payload.transaction_time ?? null));
  const isTotalDirty = !!draft && draft.total_amount !== twin.payload.total_amount;

  const resetScopedEdits = () => {
    setSectionEditMode(null);
    setDraft(cloneTwinPayload(twin.payload));
    setActionError(null);
  };

  const saveScopedSection = async (section: TwinSection) => {
    if (editMode) return;
    const sectionChanged = section === "date_time" ? isDateTimeDirty : isTotalDirty;
    if (sectionChanged) {
      await saveMutation.mutateAsync(draft);
    }
    setSectionEditMode(null);
    setActionError(null);
  };

  const handleConfirm = async (section: TwinSection) => {
    try {
      if (!editMode && sectionEditMode === section) {
        await saveScopedSection(section);
      }
      await confirmMutation.mutateAsync({ section, confirmed: true });
      setSectionEditMode(null);
      setActionError(null);
    } catch (error) {
      setActionError(toErrorMessage(error));
    }
  };

  const handleNeedsEdit = async (section: TwinSection) => {
    try {
      await confirmMutation.mutateAsync({ section, confirmed: false });
      if (!editMode) {
        setSectionEditMode(section);
      }
      setActionError(null);
    } catch (error) {
      setActionError(toErrorMessage(error));
    }
  };

  return (
    <Card className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">Receipt Twin</h2>
          <p className="text-sm text-ink/70">{draft.store_name || "Unknown store"}</p>
          {draft.store_address ? <p className="text-xs text-ink/60">{draft.store_address}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          {!editMode ? (
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => {
                setSectionEditMode(null);
                setEditMode(true);
              }}
            >
              <Pencil className="h-4 w-4" /> Edit
            </Button>
          ) : (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (isDirty && !window.confirm("Discard unsaved twin edits?")) {
                    return;
                  }
                  setSectionEditMode(null);
                  setEditMode(false);
                }}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                className="gap-1"
                onClick={() => saveMutation.mutate(draft)}
                disabled={saveMutation.isPending || !isDirty}
              >
                <Save className="h-4 w-4" />
                {saveMutation.isPending ? "Saving..." : "Save"}
              </Button>
            </>
          )}
        </div>
      </div>

      <section
        className={`rounded-xl border p-3 ${
          dateTimeConfirmed ? "border-emerald-300 bg-emerald-50/50" : "border-amber-300 bg-amber-50 shadow-[0_0_0_1px_rgba(245,158,11,0.35)]"
        }`}
      >
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Date + Time</p>
          <div className="flex gap-2">
            <Button
              variant={dateTimeConfirmed ? "outline" : "solid"}
              size="sm"
              className="h-7 px-2"
              onClick={() => {
                void handleConfirm("date_time");
              }}
              disabled={confirmMutation.isPending || saveMutation.isPending}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2"
              onClick={() => {
                void handleNeedsEdit("date_time");
              }}
              disabled={confirmMutation.isPending || saveMutation.isPending}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
            {!editMode && sectionEditMode === "date_time" ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={resetScopedEdits}
                  disabled={saveMutation.isPending}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => {
                    void saveScopedSection("date_time");
                  }}
                  disabled={saveMutation.isPending || !isDateTimeDirty}
                >
                  Save
                </Button>
              </>
            ) : null}
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Input
            type="date"
            value={draft.transaction_date ?? ""}
            readOnly={!isDateTimeEditing}
            onChange={(event) => setDraft({ ...draft, transaction_date: event.target.value || null })}
          />
          <Input
            type="time"
            value={normalizeTwinTimeForInput(draft.transaction_time)}
            readOnly={!isDateTimeEditing}
            onChange={(event) => {
              const raw = event.target.value.trim();
              setDraft({ ...draft, transaction_time: raw ? raw : null });
            }}
          />
        </div>
      </section>

      <section
        className={`rounded-xl border p-3 ${
          totalConfirmed ? "border-emerald-300 bg-emerald-50/50" : "border-amber-300 bg-amber-50 shadow-[0_0_0_1px_rgba(245,158,11,0.35)]"
        }`}
      >
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Total</p>
          <div className="flex gap-2">
            <Button
              variant={totalConfirmed ? "outline" : "solid"}
              size="sm"
              className="h-7 px-2"
              onClick={() => {
                void handleConfirm("total");
              }}
              disabled={confirmMutation.isPending || saveMutation.isPending}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2"
              onClick={() => {
                void handleNeedsEdit("total");
              }}
              disabled={confirmMutation.isPending || saveMutation.isPending}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
            {!editMode && sectionEditMode === "total" ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={resetScopedEdits}
                  disabled={saveMutation.isPending}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => {
                    void saveScopedSection("total");
                  }}
                  disabled={saveMutation.isPending || !isTotalDirty}
                >
                  Save
                </Button>
              </>
            ) : null}
          </div>
        </div>
        {isTotalEditing ? (
          <Input
            type="number"
            step="0.01"
            value={draft.total_amount}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              if (Number.isNaN(parsed)) return;
              setDraft({ ...draft, total_amount: parsed });
            }}
          />
        ) : (
          <p className="text-sm font-semibold">{formatCurrency(draft.total_amount)}</p>
        )}
      </section>

      {editMode ? (
        <label className="inline-flex items-center gap-2 text-xs text-ink/70">
          <input
            type="checkbox"
            checked={allowRawEdit}
            onChange={(event) => setAllowRawEdit(event.target.checked)}
          />
          Edit original raw text
        </label>
      ) : null}

      <section className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Line items</p>
        <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
          {draft.line_items.map((item, index) => (
            <div key={`${item.index}-${index}`} className={`rounded-xl border border-ink/10 p-2 text-xs ${lineItemClassName(item)}`}>
              {editMode ? (
                <div className="space-y-2">
                  <Input
                    value={item.raw_text}
                    readOnly={!allowRawEdit}
                    onChange={(event) => {
                      const next = cloneTwinPayload(draft);
                      next.line_items[index] = { ...next.line_items[index], raw_text: event.target.value };
                      setDraft(next);
                    }}
                  />
                  <Input
                    value={item.translated_text ?? ""}
                    placeholder="Translated text"
                    onChange={(event) => {
                      const next = cloneTwinPayload(draft);
                      next.line_items[index] = { ...next.line_items[index], translated_text: event.target.value };
                      setDraft(next);
                    }}
                  />
                  <div className="grid gap-2 sm:grid-cols-4">
                    <Input
                      type="number"
                      step="0.01"
                      value={item.quantity ?? ""}
                      placeholder="Qty"
                      onChange={(event) => setDraft(patchNumberField(draft, index, "quantity", event.target.value))}
                    />
                    <Input
                      type="number"
                      step="0.01"
                      value={item.unit_price ?? ""}
                      placeholder="Unit"
                      onChange={(event) => setDraft(patchNumberField(draft, index, "unit_price", event.target.value))}
                    />
                    <Input
                      type="number"
                      step="0.01"
                      value={item.line_total ?? ""}
                      placeholder="Line total"
                      onChange={(event) => setDraft(patchNumberField(draft, index, "line_total", event.target.value))}
                    />
                    <Input
                      value={item.tax_code ?? ""}
                      placeholder="Tax code"
                      onChange={(event) => {
                        const next = cloneTwinPayload(draft);
                        next.line_items[index] = {
                          ...next.line_items[index],
                          tax_code: event.target.value.trim() ? event.target.value : null,
                        };
                        setDraft(next);
                      }}
                    />
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p>{item.raw_text || item.translated_text || `Line ${item.index + 1}`}</p>
                    {item.translated_text && item.translated_text !== item.raw_text ? (
                      <p className="text-[11px] text-ink/60">{item.translated_text}</p>
                    ) : null}
                  </div>
                  <div className="text-right text-[11px] text-ink/70">
                    <p>
                      {item.quantity ?? "--"} × {item.unit_price ?? "--"}
                    </p>
                    <p className="font-semibold">{item.line_total == null ? "?" : formatCurrency(item.line_total)}</p>
                    {item.tax_code ? <p>{item.tax_code}</p> : null}
                  </div>
                </div>
              )}
              {item.line_total == null ? <p className="mt-1 text-[11px] text-amber-700">Uncertain amount</p> : null}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl bg-sand/60 p-2 text-xs text-ink/70">
        <p>Subtotal: {formatCurrency(draft.subtotal)}</p>
        <p>Tax total: {formatCurrency(draft.tax_total)}</p>
        <p>Payment: {draft.payment_method || "--"}</p>
      </section>

      {editMode && warnings.length > 0 ? (
        <section className="space-y-1 rounded-xl border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          {warnings.map((warning) => (
            <p key={warning}>- {warning}</p>
          ))}
        </section>
      ) : null}

      {actionError ? <p className="text-xs text-red-700">{actionError}</p> : null}
    </Card>
  );
}
