"use client";

import { useEffect } from "react";
import Link from "next/link";

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
    <main className="mx-auto max-w-3xl px-4 py-16 text-center">
      <h1 className="mb-2 text-xl font-semibold">Something went wrong</h1>
      <p className="mb-6 text-sm text-gray-600">An unexpected error occurred. You can try again or return to the queue.</p>
      {error.message ? (
        <p className="mb-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-left text-xs text-red-700">{error.message}</p>
      ) : null}
      {error.digest ? (
        <p className="mb-6 text-left text-[11px] text-gray-500">digest: {error.digest}</p>
      ) : null}
      <div className="flex items-center justify-center gap-4">
        <button
          onClick={reset}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          Try again
        </button>
        <Link href="/" className="text-sm font-medium text-gray-700 hover:underline">
          Back to queue
        </Link>
      </div>
    </main>
  );
}
