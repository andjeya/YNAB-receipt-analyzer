"use client";

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/toast";
import { getAppConfig } from "@/lib/api";

// ---------------------------------------------------------------------------
// Devtools localStorage store — allows live toggle without a page reload.
// The storage key is "snappy_query_devtools"; value "1" = enabled.
// ---------------------------------------------------------------------------

const DEVTOOLS_STORAGE_KEY = "snappy_query_devtools";

function readDevtoolsEnabled(): boolean {
  try {
    return localStorage.getItem(DEVTOOLS_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Subscribers notified when the devtools preference changes in this tab. */
const devtoolsSubscribers = new Set<() => void>();

export function notifyDevtoolsChange(): void {
  for (const cb of devtoolsSubscribers) cb();
}

function subscribeDevtools(cb: () => void): () => void {
  devtoolsSubscribers.add(cb);
  return () => { devtoolsSubscribers.delete(cb); };
}

/**
 * Query devtools (the floating palm-tree button) are developer tooling — only
 * mounted when the backend debug-tools toggle is on AND the user has opted in
 * by toggling "Show query devtools" in the debug panel.
 *
 * Two gates required (both must be true):
 *   1. debug_tools_enabled from /api/config (backend flag, false in prod)
 *   2. localStorage "snappy_query_devtools" === "1" (user opt-in, default off)
 */
function DevtoolsGate() {
  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: () => getAppConfig(),
    staleTime: 60_000,
  });
  // Live subscription: re-renders when notifyDevtoolsChange() is called.
  const devtoolsEnabled = useSyncExternalStore(subscribeDevtools, readDevtoolsEnabled, () => false);

  if (process.env.NODE_ENV === "production") return null;
  if (!configQuery.data?.debug_tools_enabled) return null;
  if (!devtoolsEnabled) return null;
  return <ReactQueryDevtools initialIsOpen={false} />;
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        {children}
        <DevtoolsGate />
      </ToastProvider>
    </QueryClientProvider>
  );
}
