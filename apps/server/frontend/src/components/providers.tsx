"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState } from "react";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/toast";

export function Providers({ children }: { children: ReactNode }) {
  const showDevtools = process.env.NODE_ENV !== "production";
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
        {showDevtools ? <ReactQueryDevtools initialIsOpen={false} /> : null}
      </ToastProvider>
    </QueryClientProvider>
  );
}
