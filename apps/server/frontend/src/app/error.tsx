"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Snappy } from "@/components/snappy/snappy";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled error:", error);
  }, [error]);

  return (
    <main className="mx-auto flex min-h-[80vh] max-w-md flex-col items-center justify-center px-4 py-16 text-center">
      <Card className="flex w-full flex-col items-center gap-1 p-8">
        <Snappy pose="concerned" size="h-24 w-24" />
        <h1 className="mt-4 text-xl font-bold text-ink">Oops — Snappy tripped over something</h1>
        <p className="mt-2 text-sm text-ink/65">Nothing was lost. Try again, or head back to your receipts.</p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Button onClick={reset}>Try again</Button>
          <Link
            href="/"
            className="inline-flex min-h-11 items-center rounded-xl2 px-4 text-sm font-semibold text-ink/70 transition hover:bg-ink/[0.06] hover:text-ink"
          >
            Back to receipts
          </Link>
        </div>
        {/* Raw error text is developer-only; tucked away so the crash page never
            greets the user with an exception message. */}
        {error.message || error.digest ? (
          <details className="mt-6 w-full text-left">
            <summary className="cursor-pointer text-xs text-ink/40 transition hover:text-ink/60">
              Details for the developer
            </summary>
            {error.message ? (
              <p className="mt-2 rounded-xl border border-ink/10 bg-cream/60 px-3 py-2 text-xs text-ink/60">{error.message}</p>
            ) : null}
            {error.digest ? <p className="mt-1 text-[11px] text-ink/40">digest: {error.digest}</p> : null}
          </details>
        ) : null}
      </Card>
    </main>
  );
}
