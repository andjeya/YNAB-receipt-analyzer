"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Snappy } from "@/components/snappy/snappy";

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
    <main className="mx-auto flex max-w-3xl flex-col items-center px-4 py-16 text-center">
      <Snappy pose="concerned" size="h-20 w-20" />
      <h1 className="mb-2 mt-4 text-xl font-semibold">Oops — Snappy tripped over something</h1>
      <p className="mb-6 text-sm text-gray-600">Nothing was lost. Try again, or head back to your receipts.</p>
      <div className="mb-6 flex items-center justify-center gap-4">
        <button
          onClick={reset}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
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
          <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600">
            Details for the developer
          </summary>
          {error.message ? (
            <p className="mt-2 rounded border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">{error.message}</p>
          ) : null}
          {error.digest ? <p className="mt-1 text-[11px] text-gray-400">digest: {error.digest}</p> : null}
        </details>
      ) : null}
    </main>
  );
}
