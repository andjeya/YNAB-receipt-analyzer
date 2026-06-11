"use client";

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Focus-trap helpers
// ---------------------------------------------------------------------------

const FOCUSABLE_SELECTORS = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS)).filter(
    (el) => !el.closest("[inert]"),
  );
}

// ---------------------------------------------------------------------------
// Dialog props
// ---------------------------------------------------------------------------

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  /** id that matches the h2/heading inside the dialog */
  labelledById: string;
  /** id of an optional description element */
  describedById?: string;
  /** data-testid forwarded to the panel div */
  "data-testid"?: string;
  children: ReactNode;
}

// ---------------------------------------------------------------------------
// Dialog component
// ---------------------------------------------------------------------------

export function Dialog({
  open,
  onClose,
  labelledById,
  describedById,
  "data-testid": dataTestId,
  children,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<Element | null>(null);

  // Lock body scroll and store previous focus element on open
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement;
    document.body.style.overflow = "hidden";

    // Focus first focusable element in the panel
    const frame = requestAnimationFrame(() => {
      if (!panelRef.current) return;
      const focusable = getFocusableElements(panelRef.current);
      if (focusable.length > 0) {
        focusable[0].focus();
      } else {
        panelRef.current.focus();
      }
    });

    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = "";
      // Restore focus on close
      const el = restoreRef.current;
      if (el && typeof (el as HTMLElement).focus === "function") {
        (el as HTMLElement).focus();
      }
      restoreRef.current = null;
    };
  }, [open]);

  // Keyboard handler: Tab trap + Escape
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      if (!panelRef.current) return;

      const focusable = getFocusableElements(panelRef.current);
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey) {
        // Shift+Tab: wrap from first → last
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        // Tab: wrap from last → first
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[80] bg-black/45"
        aria-hidden="true"
        onClick={onClose}
      />
      {/* Panel */}
      <div className="fixed inset-0 z-[81] flex items-center justify-center p-4">
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={labelledById}
          aria-describedby={describedById}
          data-testid={dataTestId}
          tabIndex={-1}
          className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-ink/15 bg-white shadow-float outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </div>
      </div>
    </>
  );
}
