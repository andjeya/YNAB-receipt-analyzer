"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteCardMapping, getYnabCache, listCardMappings, upsertCardMapping } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

// ---------------------------------------------------------------------------
// CardMappingPanel
// ---------------------------------------------------------------------------

export function CardMappingPanel({
  open,
  onClose,
  debugToolsEnabled,
}: {
  open: boolean;
  onClose: () => void;
  debugToolsEnabled: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Lazy-fetch card mappings (only when panel is open and debug mode is on)
  const mappingsQuery = useQuery({
    queryKey: ["card-mappings"],
    queryFn: listCardMappings,
    enabled: open && debugToolsEnabled,
    staleTime: 0,
  });

  // Lazy-fetch YNAB accounts (only when panel is open)
  const accountsQuery = useQuery({
    queryKey: ["ynab-cache", "account"],
    queryFn: () => getYnabCache("account"),
    enabled: open,
    staleTime: 30_000,
  });

  const accounts = (accountsQuery.data ?? []).map((item) => ({
    entity_id: String(item.entity_id ?? "").trim(),
    name: String(item.name ?? "").trim() || "Unknown account",
  })).filter((item) => item.entity_id.length > 0);

  const accountById = new Map(accounts.map((a) => [a.entity_id, a.name]));

  // "Add mapping" footer state
  const [addCardInput, setAddCardInput] = useState("");
  const [addAccountId, setAddAccountId] = useState("");

  const invalidateMappings = () => {
    queryClient.invalidateQueries({ queryKey: ["card-mappings"] });
  };

  const upsertMutation = useMutation({
    mutationFn: upsertCardMapping,
    onSuccess: () => {
      invalidateMappings();
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to save card mapping",
      });
    },
  });

  const upsertRowMutation = useMutation({
    mutationFn: upsertCardMapping,
    onSuccess: () => {
      invalidateMappings();
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to update card mapping",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteCardMapping(id),
    onSuccess: () => {
      invalidateMappings();
    },
    onError: (e) => {
      toast({
        variant: "error",
        message: e instanceof Error && e.message ? e.message : "Failed to delete card mapping",
      });
    },
  });

  const handleAdd = () => {
    const card = addCardInput.trim();
    if (!card || card.length !== 4 || !/^\d{4}$/.test(card)) {
      toast({ variant: "error", message: "Card last 4 must be exactly 4 digits." });
      return;
    }
    if (!addAccountId) {
      toast({ variant: "error", message: "Select a YNAB account." });
      return;
    }
    upsertMutation.mutate(
      { card_last_four: card, account_id: addAccountId },
      {
        onSuccess: () => {
          setAddCardInput("");
          setAddAccountId("");
        },
      },
    );
  };

  const items = mappingsQuery.data?.items ?? [];

  return (
    <Dialog open={open} onClose={onClose} labelledById="card-mapping-heading" data-testid="card-mapping-panel">
      <Card className="w-full max-w-lg space-y-4 animate-incident-enter border-0 shadow-none">
        {/* Header */}
        <div>
          <h2 id="card-mapping-heading" className="text-base font-semibold">
            Card &rarr; Account mappings
          </h2>
          <p className="mt-1 text-xs text-ink/60">
            The account remembered for each card&apos;s last 4 digits. Updated automatically when you sync; edit or remove here.
          </p>
        </div>

        {/* Loading / error states */}
        {mappingsQuery.isLoading ? (
          <p className="text-sm text-ink/70">Loading mappings...</p>
        ) : null}
        {mappingsQuery.isError ? (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            Failed to load card mappings.
          </p>
        ) : null}

        {/* Table */}
        {!mappingsQuery.isLoading && !mappingsQuery.isError ? (
          items.length === 0 ? (
            <p className="rounded-xl bg-ink/5 px-4 py-4 text-sm text-ink/60">
              No card mappings yet — they&apos;re created automatically when you sync a receipt.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-ink/10 text-left text-xs font-semibold uppercase tracking-wide text-ink/60">
                    <th className="pb-2 pr-4">Card (last 4)</th>
                    <th className="pb-2 pr-2">YNAB account</th>
                    <th className="pb-2" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((mapping) => {
                    const isDeletedAccount =
                      mapping.account_name === null && !accountById.has(mapping.account_id);
                    const currentName = accountById.get(mapping.account_id) ?? null;

                    // Options: all known accounts + possibly the current account_id if it's
                    // missing from the cache (deleted account)
                    const selectOptions =
                      isDeletedAccount
                        ? [
                            { entity_id: mapping.account_id, name: "(deleted account)" },
                            ...accounts,
                          ]
                        : accounts;

                    return (
                      <tr key={mapping.id} className="border-b border-ink/5 last:border-0">
                        <td className="py-2 pr-4">
                          <code className="rounded bg-ink/5 px-1.5 py-0.5 font-mono text-xs">
                            {mapping.card_last_four}
                          </code>
                        </td>
                        <td className="py-2 pr-2">
                          <div className="space-y-1">
                            <Select
                              value={mapping.account_id}
                              onChange={(e) => {
                                upsertRowMutation.mutate({
                                  card_last_four: mapping.card_last_four,
                                  account_id: e.target.value,
                                });
                              }}
                              disabled={upsertRowMutation.isPending || deleteMutation.isPending}
                              className="h-9"
                            >
                              {selectOptions.map((opt) => (
                                <option key={opt.entity_id} value={opt.entity_id}>
                                  {opt.name}
                                </option>
                              ))}
                            </Select>
                            {isDeletedAccount ? (
                              <p className="text-[11px] font-semibold text-amber-700">
                                The mapped account no longer exists in YNAB. Select a valid account.
                              </p>
                            ) : currentName === null ? (
                              <p className="text-[11px] font-semibold text-amber-700">
                                Account ID not in YNAB cache — fetch YNAB updates to refresh.
                              </p>
                            ) : null}
                          </div>
                        </td>
                        <td className="py-2">
                          <button
                            type="button"
                            aria-label={`Delete mapping for card ${mapping.card_last_four}`}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-ink/50 transition hover:bg-red-50 hover:text-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-40"
                            onClick={() => deleteMutation.mutate(mapping.id)}
                            disabled={deleteMutation.isPending || upsertRowMutation.isPending}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}

        {/* Add mapping row */}
        <div className="space-y-2 border-t border-ink/10 pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/60">Add mapping</p>
          <div className="flex gap-2">
            <label htmlFor="card-mapping-add-input" className="sr-only">
              Card last 4 digits
            </label>
            <Input
              id="card-mapping-add-input"
              value={addCardInput}
              onChange={(e) => setAddCardInput(e.target.value.replace(/\D/g, "").slice(0, 4))}
              maxLength={4}
              inputMode="numeric"
              placeholder="Last 4"
              className="h-9 w-24 shrink-0"
            />
            <Select
              value={addAccountId}
              onChange={(e) => setAddAccountId(e.target.value)}
              disabled={accountsQuery.isLoading || upsertMutation.isPending}
              className="h-9"
            >
              <option value="">Select account...</option>
              {accounts.map((account) => (
                <option key={account.entity_id} value={account.entity_id}>
                  {account.name}
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              onClick={handleAdd}
              disabled={upsertMutation.isPending || !addCardInput || !addAccountId}
              className="shrink-0"
            >
              {upsertMutation.isPending ? "Adding..." : "Add"}
            </Button>
          </div>
        </div>

        {/* Footer close */}
        <div className="flex justify-end border-t border-ink/10 pt-3">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </div>
      </Card>
    </Dialog>
  );
}
