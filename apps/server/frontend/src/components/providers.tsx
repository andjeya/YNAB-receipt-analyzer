"use client";

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState } from "react";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/toast";
import { getAppConfig } from "@/lib/api";

/**
 * Query devtools (the floating palm-tree button) are developer tooling — only
 * mounted when the backend debug-tools toggle is on, never for the end user.
 */
function DevtoolsGate() {
  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: () => getAppConfig(),
    staleTime: 60_000,
  });
  if (process.env.NODE_ENV === "production") return null;
  if (!configQuery.data?.debug_tools_enabled) return null;
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
