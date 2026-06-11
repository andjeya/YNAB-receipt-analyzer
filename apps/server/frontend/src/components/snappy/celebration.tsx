"use client";

import { useEffect, useState } from "react";
import { Snappy } from "./snappy";

/**
 * A small non-blocking, non-overlay flourish shown on a clean verified sync.
 * Renders inline near the action bar — does NOT overlay financial information.
 * Auto-dismisses after ~1.6 s. data-testid="sync-celebration" for Playwright.
 * (Timeliness and consistency celebrations are delivered separately — the green
 * tile sprout animation and the header mascot pose swap, respectively.)
 */
export function SnappyCelebration() {
  const [show, setShow] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setShow(false), 1600);
    return () => clearTimeout(timer);
  }, []);

  if (!show) return null;

  return (
    <div
      data-testid="sync-celebration"
      aria-hidden="true"
      className="pointer-events-none inline-flex items-center gap-1.5 animate-snappy-pop"
    >
      <Snappy pose="celebrating" size="h-10 w-10" />
      <span className="text-xs font-semibold text-emerald-700">Synced!</span>
    </div>
  );
}
