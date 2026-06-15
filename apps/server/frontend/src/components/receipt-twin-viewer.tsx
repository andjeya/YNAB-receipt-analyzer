"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Check, Pencil, RefreshCw, Save, X } from "lucide-react";

import { confirmTwinSection, retryTwinExtraction, saveReceiptTwin } from "@/lib/api";
import { ReceiptLineItem, ReceiptTwin, ReceiptTwinPayload } from "@/lib/types";
import { cloneTwinPayload, computeTwinEditWarnings, isRealLineItem, normalizeTwinTimeForInput } from "@/lib/receipt-twin";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";

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

function isDiscountItem(item: ReceiptLineItem): boolean {
  return (item.item_type || "").toLowerCase() === "discount";
}

function lineItemClassName(item: ReceiptLineItem): string {
  const kind = (item.item_type || "").toLowerCase();
  // Extraction artifacts (only shown in edit mode for raw fidelity): de-emphasize, never alarm.
  if (!isRealLineItem(item)) return "text-ink/40 italic";
  if (kind === "discount") return "text-emerald-700 italic";
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
  autoConfirmed,
}: {
  receiptId: string;
  twin: ReceiptTwin | null;
  onUpdated: () => void;
  /** True when both sections were auto-confirmed for this receipt and are still confirmed. */
  autoConfirmed?: boolean;
}) {
  const { toast } = useToast();
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
      toast({ variant: "error", message: toErrorMessage(error), title: "Twin save failed" });
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
      toast({ variant: "error", message: toErrorMessage(error), title: "Confirm failed" });
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
      toast({ variant: "error", message: toErrorMessage(error), title: "Retry failed" });
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
          <h2 className="font-semibold">Receipt details</h2>
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
  const isScopedDateTimeEditing = !editMode && sectionEditMode === "date_time";
  const isScopedTotalEditing = !editMode && sectionEditMode === "total";
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
    <Card className="space-y-4" data-testid="twin-viewer">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">Receipt details</h2>
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

      {autoConfirmed && dateTimeConfirmed && totalConfirmed ? (
        <p className="text-xs text-ink/55 -mb-1">
          ✓ Checked automatically — tap ✗ if something looks off
        </p>
      ) : null}

      <section
        className={`rounded-xl border p-3 ${
          dateTimeConfirmed ? "border-emerald-300 bg-emerald-50/50" : "border-amber-300 bg-amber-50 shadow-[0_0_0_1px_rgba(245,158,11,0.35)]"
        }`}
      >
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Date + Time</p>
          <div className="flex gap-2">
            {!isScopedDateTimeEditing ? (
              <>
                <Button
                  variant={dateTimeConfirmed ? "outline" : "solid"}
                  size="sm"
                  className={dateTimeConfirmed ? "h-9 min-w-[36px] gap-1 px-2 text-xs" : "h-9 gap-1.5 px-3 text-xs"}
                  data-testid="confirm-date-time"
                  aria-label={dateTimeConfirmed ? "Date and time confirmed" : "Confirm date and time"}
                  onClick={() => {
                    void handleConfirm("date_time");
                  }}
                  disabled={!draft.transaction_date || confirmMutation.isPending || saveMutation.isPending}
                >
                  <Check className="h-3.5 w-3.5" />
                  {!dateTimeConfirmed ? <span>Looks right</span> : null}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className={dateTimeConfirmed ? "h-9 min-w-[36px] gap-1 px-2 text-xs" : "h-9 gap-1.5 px-3 text-xs"}
                  aria-label="Edit date and time"
                  onClick={() => {
                    void handleNeedsEdit("date_time");
                  }}
                  disabled={confirmMutation.isPending || saveMutation.isPending}
                >
                  <X className="h-3.5 w-3.5" />
                  {!dateTimeConfirmed ? <span>Edit</span> : null}
                </Button>
              </>
            ) : (
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
                    void handleConfirm("date_time");
                  }}
                  disabled={!draft.transaction_date || saveMutation.isPending || confirmMutation.isPending}
                >
                  Save
                </Button>
              </>
            )}
          </div>
        </div>
        {/* min-w-0 lets the date/time inputs shrink inside the grid track so the
            native iOS/WebKit value pseudo can't force overflow (see globals.css). */}
        <div className="grid min-w-0 gap-2 sm:grid-cols-2">
          <Input
            type="date"
            className="min-w-0"
            value={draft.transaction_date ?? ""}
            readOnly={!isDateTimeEditing}
            onChange={(event) => setDraft({ ...draft, transaction_date: event.target.value || null })}
          />
          <Input
            type="time"
            className="min-w-0"
            value={normalizeTwinTimeForInput(draft.transaction_time)}
            readOnly={!isDateTimeEditing}
            onChange={(event) => {
              const raw = event.target.value.trim();
              setDraft({ ...draft, transaction_time: raw ? raw : null });
            }}
          />
        </div>
        {!dateTimeConfirmed && draft.date_source === "ai_guess" && draft.date_note ? (
          <p
            className="mt-2 flex items-start gap-1.5 rounded-lg bg-amber-100/80 px-2.5 py-2 text-xs font-medium text-amber-900"
            data-testid="date-guess-note"
            role="status"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>
              {draft.date_note} Check the date, then tap “Looks right” to confirm.
            </span>
          </p>
        ) : null}
      </section>

      <section
        className={`rounded-xl border p-3 ${
          totalConfirmed ? "border-emerald-300 bg-emerald-50/50" : "border-amber-300 bg-amber-50 shadow-[0_0_0_1px_rgba(245,158,11,0.35)]"
        }`}
      >
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">Total</p>
          <div className="flex gap-2">
            {!isScopedTotalEditing ? (
              <>
                <Button
                  variant={totalConfirmed ? "outline" : "solid"}
                  size="sm"
                  className={totalConfirmed ? "h-9 min-w-[36px] gap-1 px-2 text-xs" : "h-9 gap-1.5 px-3 text-xs"}
                  data-testid="confirm-total"
                  aria-label={totalConfirmed ? "Total confirmed" : "Confirm total"}
                  onClick={() => {
                    void handleConfirm("total");
                  }}
                  disabled={!(draft.total_amount > 0) || confirmMutation.isPending || saveMutation.isPending}
                >
                  <Check className="h-3.5 w-3.5" />
                  {!totalConfirmed ? <span>Looks right</span> : null}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className={totalConfirmed ? "h-9 min-w-[36px] gap-1 px-2 text-xs" : "h-9 gap-1.5 px-3 text-xs"}
                  aria-label="Edit total"
                  onClick={() => {
                    void handleNeedsEdit("total");
                  }}
                  disabled={confirmMutation.isPending || saveMutation.isPending}
                >
                  <X className="h-3.5 w-3.5" />
                  {!totalConfirmed ? <span>Edit</span> : null}
                </Button>
              </>
            ) : (
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
                    void handleConfirm("total");
                  }}
                  disabled={!(draft.total_amount > 0) || saveMutation.isPending || confirmMutation.isPending}
                >
                  Save
                </Button>
              </>
            )}
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
          {draft.line_items.filter((item) => editMode || isRealLineItem(item)).map((item, index) => (
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
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
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
                    <p className="font-medium">{item.translated_text || item.raw_text || `Line ${index + 1}`}</p>
                    {isDiscountItem(item) ? <p className="text-[11px]">discount / credit</p> : null}
                  </div>
                  <div className="text-right text-[11px] text-ink/70">
                    {item.quantity != null && item.unit_price != null ? (
                      <p>
                        {item.quantity} × {formatCurrency(item.unit_price)}
                      </p>
                    ) : null}
                    <p className="font-semibold">
                      {item.line_total == null
                        ? "?"
                        : isDiscountItem(item)
                          ? `−${formatCurrency(Math.abs(item.line_total))}`
                          : formatCurrency(item.line_total)}
                    </p>
                    {item.tax_code ? <p className="text-[10px] text-ink/50">Tax code {item.tax_code}</p> : null}
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
        {draft.payment_method && draft.payment_method.toLowerCase() !== "unknown" ? (
          <p>Payment: {draft.payment_method}</p>
        ) : null}
      </section>

      {warnings.length > 0 ? (
        <section
          className="space-y-1 rounded-xl border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900"
        >
          <p className="font-semibold">Worth a second look</p>
          {warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
          <p className="text-amber-800/80">
            Compare with the original scan, then tap Edit to fix anything that&apos;s off. What
            syncs to YNAB is the total above — make sure that matches the receipt.
          </p>
        </section>
      ) : null}

      {actionError ? <p className="text-xs text-red-700">{actionError}</p> : null}
    </Card>
  );
}
