"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Snappy } from "@/components/snappy/snappy";

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
      <div className="flex flex-col items-center rounded-3xl border border-amber-200 bg-amber-50 p-6 text-center">
        <Snappy pose="concerned" size="h-16 w-16" />
        <h2 className="mb-2 mt-3 text-base font-semibold text-ink">This receipt wouldn&apos;t open</h2>
        <p className="mb-4 text-sm text-ink/70">Nothing was lost. Try again, or head back to your receipts.</p>
        <div className="mb-4 flex items-center gap-4">
          <button
            onClick={reset}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            Try again
          </button>
          <Link href="/" className="text-sm font-medium text-gray-700 hover:underline">
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
