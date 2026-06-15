/**
 * snappy-quotes.ts
 *
 * Lines for Snappy's speech bubble. Two kinds live here:
 *
 *  1. Snappy's own voice — original quips, affectionate pop-culture riffs, and
 *     literary / wizarding-world PARODIES. These are NOT real quotes, so they
 *     carry no `author` and render as plain speech (no quotation marks, no
 *     attribution line). Do NOT attribute a parody to the work it riffs on —
 *     "It is a truth universally acknowledged that a mystery transaction…" is
 *     Snappy, not Jane Austen, and labeling it otherwise would be a
 *     misattribution.
 *
 *  2. Genuinely attributed wisdom — real quotes about money, thrift, and time.
 *     Each was checked against an authoritative primary source (Berkshire
 *     Hathaway shareholder letters, Franklin's "The Way to Wealth", Seneca's
 *     Moral Letters, Thoreau's Walden, etc.) during the 2026-06-12 research
 *     pass — see
 *     plans/2026/06/week-24/session-2026-06-12-1828-claude-mascot-quote-verification-research.md.
 *
 * For the attributed entries: DO NOT add quotes from memory or quote-aggregator
 * sites — most famous money quotes are misattributed. Verify against Quote
 * Investigator / primary sources first.
 */

export interface SnappyQuote {
  text: string;
  /** Omitted for Snappy's own lines; set only for genuinely attributed quotes. */
  author?: string;
  /** Source work + year, shown in the title tooltip on the attribution. */
  source?: string;
}

// Order is irrelevant — deriveSnappyPose picks at random. Grouped only for
// readability. Snappy's own lines carry no author and render as plain speech.
export const SNAPPY_QUOTES: SnappyQuote[] = [
  // ── Snappy originals: quips with budget / science energy ─────────────────
  { text: "Snappy has questions, but Snappy is choosing grace." },
  { text: "Snappy loves this journey for Future You." },
  { text: "Entropy increases unless Snappy intervenes." },
  { text: "The category signal has emerged from the noise." },
  { text: "Calibration complete. The vibes are within tolerance." },
  { text: "Financial uncertainty principle: the longer you wait, the fuzzier it gets." },
  { text: "The transaction has achieved orbital stability." },
  { text: "This budget has excellent signal-to-noise ratio." },
  { text: "The receipts were unstructured data. Now they have meaning." },
  { text: "A tidy budget is a cozy budget." },
  { text: "Snappy found a tiny fire and brought a tiny bucket." },
  { text: "The merchant name was cryptic, but Snappy speaks fluent chaos." },
  { text: "This receipt required detective work and emotional resilience." },
  { text: "Snappy cleaned up the mess and left only tiny paw prints." },

  // ── Pop-culture riffs (TV / film allusions) ──────────────────────────────
  { text: "Smort." },
  { text: "Love that journey for me." },
  { text: "Cool cool cool. The budget survived." },
  { text: "Snappy is folding in the receipts." },
  { text: "Best wishes, warmest ledgers." },
  { text: "The receipt has been voted off Ambiguity Island." },

  // ── Literary parodies — Snappy riffing, NOT the original authors ─────────
  { text: "Reader, the receipt was categorized." },
  { text: "It is a truth universally acknowledged that a mystery transaction must be in want of a category." },
  { text: "All happy budgets are alike; each messy receipt is messy in its own way." },
  { text: "To spend, or not to spend: that is above Snappy's pay grade." },

  // ── Wizarding-world riffs (parodies, unattributed) ───────────────────────
  { text: "Mischief managed. The receipt is categorized." },
  { text: "Expecto Categorum." },
  { text: "The receipt was hiding under an invisibility cloak." },
  { text: "Ten points to Future You." },
  { text: "Snappy solemnly swears this transaction is up to something." },

  // ── Genuinely attributed wisdom (verified against primary sources) ───────
  { text: "Waste not, want not.", author: "English proverb", source: "first recorded 1772" },
  { text: "Price is what you pay; value is what you get.", author: "Warren Buffett, crediting Ben Graham", source: "Berkshire Hathaway shareholder letter, 2008" },
  { text: "You only find out who is swimming naked when the tide goes out.", author: "Warren Buffett", source: "Berkshire Hathaway shareholder letter, 2001" },
  { text: "It is not the man who has too little, but the man who craves more, that is poor.", author: "Seneca", source: "Moral Letters to Lucilius, Letter 2 (Gummere trans.)" },
  { text: "Beware of little expenses; a small leak will sink a great ship.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "A man is rich in proportion to the number of things which he can afford to let alone.", author: "Henry David Thoreau", source: "Walden, 1854" },
  { text: "The size of that circle is not very important; knowing its boundaries, however, is vital.", author: "Warren Buffett", source: "Berkshire Hathaway shareholder letter, 1996" },
  { text: "Slow and steady wins the race.", author: "Aesop", source: "The Hare and the Tortoise" },
];
