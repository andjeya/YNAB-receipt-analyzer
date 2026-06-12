/**
 * Skip-preview eligibility logic.
 *
 * Extracted as a pure function so it can be unit-tested without a React
 * environment.  The receipt-detail component imports and calls this.
 */

export interface SkipPreviewArgs {
  /** Reasons the sync strip is showing (validation errors, twin errors, etc.) */
  stripReasons: string[];
  /** Lock warnings returned by the last SaveDraftResponse (locked/reconciled period) */
  lockWarnings: string[];
  /** Category ambiguity flags parsed from the extraction payload */
  ambiguityFlags: { line_item: string; confidence: number }[];
  /** Whether the user has enabled skip-preview in localStorage */
  skipEnabled: boolean;
}

/**
 * Returns true only when it is safe to fire sync directly without showing the
 * preview dialog.  Any warning present — lock warnings, ambiguity flags, or
 * strip blocking reasons — forces the dialog to appear.
 */
export function shouldSkipPreview({
  stripReasons,
  lockWarnings,
  ambiguityFlags,
  skipEnabled,
}: SkipPreviewArgs): boolean {
  if (!skipEnabled) return false;
  if (stripReasons.length > 0) return false;
  if (lockWarnings.length > 0) return false;
  if (ambiguityFlags.length > 0) return false;
  return true;
}
