"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Snappy } from "@/components/snappy/snappy";
import { Button } from "@/components/ui/button";

export default function ReceiptDetailError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Receipt detail error:", error);
  }, [error]);

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <div className="flex flex-col items-center rounded-3xl border border-amber-200 bg-amber-50/80 p-8 text-center shadow-soft">
        <Snappy pose="concerned" size="h-20 w-20" />
        <h2 className="mb-2 mt-4 text-lg font-bold text-ink">This receipt wouldn&apos;t open</h2>
        <p className="mb-5 text-sm text-ink/70">Nothing was lost. Try again, or head back to your receipts.</p>
        <div className="mb-4 flex items-center gap-3">
          <Button size="sm" onClick={reset}>Try again</Button>
          <Link
            href="/"
            className="inline-flex min-h-9 items-center rounded-xl2 px-3 text-sm font-semibold text-ink/70 transition hover:bg-ink/[0.06] hover:text-ink"
          >
            Back to receipts
          </Link>
        </div>
        {/* Raw error text is developer-only; tucked away so the crash page never
            greets the user with an exception message. */}
        {error.message || error.digest ? (
          <details className="w-full max-w-md text-left">
            <summary className="cursor-pointer text-xs text-ink/40 hover:text-ink/60">
              Details for the developer
            </summary>
            {error.message ? (
              <p className="mt-2 rounded border border-ink/10 bg-white px-3 py-2 text-xs text-ink/60">{error.message}</p>
            ) : null}
            {error.digest ? <p className="mt-1 text-[11px] text-ink/40">digest: {error.digest}</p> : null}
          </details>
        ) : null}
      </div>
    </main>
  );
}
