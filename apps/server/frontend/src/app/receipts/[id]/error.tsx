"use client";

import { useEffect } from "react";
import Link from "next/link";

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
      <div className="rounded border border-red-200 bg-red-50 p-6">
        <h2 className="mb-2 text-base font-semibold text-red-800">Failed to render receipt</h2>
        <p className="mb-4 text-sm text-red-700">An unexpected error occurred while displaying this receipt.</p>
        <div className="flex items-center gap-4">
          <button
            onClick={reset}
            className="rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-800"
          >
            Try again
          </button>
          <Link href="/" className="text-sm font-medium text-gray-700 hover:underline">
            Back to queue
          </Link>
        </div>
      </div>
    </main>
  );
}
