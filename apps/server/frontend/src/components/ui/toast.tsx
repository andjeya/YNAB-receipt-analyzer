"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ToastVariant = "success" | "error";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: string;
  variant: ToastVariant;
  message: string;
  title?: string;
  action?: ToastAction;
  durationMs?: number;
}

interface ToastContextValue {
  toast: (options: { variant: ToastVariant; message: string; title?: string; action?: ToastAction; durationMs?: number }) => void;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const ToastContext = createContext<ToastContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

let _nextId = 0;
function nextId(): string {
  _nextId += 1;
  return String(_nextId);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const toast = useCallback(
    ({ variant, message, title, action, durationMs }: { variant: ToastVariant; message: string; title?: string; action?: ToastAction; durationMs?: number }) => {
      const id = nextId();
      const item: ToastItem = { id, variant, message, title, action, durationMs };
      setToasts((prev) => [...prev, item]);

      const delay = durationMs ?? (variant === "error" ? 5000 : 3000);
      const timer = setTimeout(() => {
        dismiss(id);
      }, delay);
      timersRef.current.set(id, timer);
    },
    [dismiss],
  );

  // Cleanup timers on unmount
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
    };
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      {/* Fixed toast region */}
      <div
        aria-live="polite"
        aria-label="Notifications"
        className="pointer-events-none fixed bottom-[max(1rem,env(safe-area-inset-bottom))] left-4 right-4 z-[90] flex flex-col gap-2 items-end"
      >
        {toasts.map((item) => (
          <div
            key={item.id}
            role="status"
            data-testid="toast"
            data-variant={item.variant}
            className={cn(
              "pointer-events-auto animate-toast-in flex w-80 max-w-full items-start gap-3 rounded-2xl border px-4 py-3 shadow-lift text-sm",
              item.variant === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                : "border-red-200 bg-red-50 text-red-900",
            )}
          >
            <div className="min-w-0 flex-1">
              {item.title ? (
                <p className="font-semibold leading-snug">{item.title}</p>
              ) : null}
              <p className={cn("leading-snug break-words", item.title ? "mt-0.5 text-xs opacity-85" : "font-medium")}>
                {item.message}
              </p>
              {item.action ? (
                <button
                  type="button"
                  className={cn(
                    "mt-1.5 text-xs font-semibold underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
                    item.variant === "success" ? "text-emerald-800 focus-visible:ring-emerald-600" : "text-red-800 focus-visible:ring-red-600",
                  )}
                  onClick={() => {
                    item.action!.onClick();
                    dismiss(item.id);
                  }}
                >
                  {item.action.label}
                </button>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Dismiss notification"
              className={cn(
                "mt-0.5 shrink-0 rounded-full p-0.5 transition hover:opacity-70",
                item.variant === "success" ? "text-emerald-700" : "text-red-700",
              )}
              onClick={() => dismiss(item.id)}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return ctx;
}
