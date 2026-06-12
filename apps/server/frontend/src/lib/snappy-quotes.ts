/**
 * snappy-quotes.ts
 *
 * Verified historical quotes about money, thrift, and time for Snappy's
 * speech bubble. EVERY entry was checked against an authoritative source
 * (Founders Online, Project Gutenberg #43855 "Franklin's Way to Wealth",
 * Wikisource public-domain translations, Berkshire Hathaway letters, etc.)
 * during the 2026-06-12 research pass — see
 * plans/2026/06/week-24/session-2026-06-12-1828-claude-mascot-quote-verification-research.md.
 *
 * DO NOT add quotes from memory or quote-aggregator sites: most famous
 * money quotes are misattributed (e.g. Einstein never said the compound-
 * interest line; Franklin never wrote "a penny saved is a penny earned").
 * Verify against Quote Investigator / primary sources first.
 *
 * Wording is kept exactly as in the cited source (including Franklin's
 * 18th-century spelling) — authenticity over modernization.
 */

export interface SnappyQuote {
  text: string;
  author: string;
  /** Source work + year, shown in the title tooltip on the attribution. */
  source: string;
}

export const SNAPPY_QUOTES: SnappyQuote[] = [
  // ── Benjamin Franklin ────────────────────────────────────────────────
  { text: "Remember that Time is Money.", author: "Benjamin Franklin", source: "Advice to a Young Tradesman, 1748" },
  { text: "A penny saved is two pence clear.", author: "Benjamin Franklin", source: "Poor Richard's Almanack, 1737" },
  { text: "Beware of little Expences; a small Leak will sink a great Ship.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "Lost time is never found again.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "Early to bed, and early to rise, makes a man healthy, wealthy, and wise.", author: "Benjamin Franklin", source: "Poor Richard's Almanack, 1735" },
  { text: "Diligence is the mother of good luck.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "Little strokes fell great oaks.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "Many a little makes a mickle.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "He that goes a borrowing, goes a sorrowing.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "Rather go to bed supper-less, than rise in debt.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "If you would be wealthy, think of saving, as well as of getting.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "A fat kitchen makes a lean will.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },
  { text: "For age and want save while you may.", author: "Benjamin Franklin", source: "The Way to Wealth, 1758" },

  // ── Classical ────────────────────────────────────────────────────────
  { text: "Nothing, Lucilius, is ours, except time.", author: "Seneca", source: "Moral Letters to Lucilius, Letter 1 (Gummere trans.)" },
  { text: "While we are postponing, life speeds by.", author: "Seneca", source: "Moral Letters to Lucilius, Letter 1 (Gummere trans.)" },
  { text: "It is not the man who has too little, but the man who craves more, that is poor.", author: "Seneca", source: "Moral Letters to Lucilius, Letter 2 (Gummere trans.)" },
  { text: "Contented poverty is an honourable estate.", author: "Seneca", source: "Moral Letters to Lucilius, Letter 2 (Gummere trans.)" },
  { text: "Time is the most valuable thing a man can spend.", author: "Theophrastus", source: "via Diogenes Laertius, Lives of Eminent Philosophers, Book V" },
  { text: "Men do not realize how great a revenue parsimony can be.", author: "Cicero", source: "Paradoxa Stoicorum VI, 46 BC" },
  { text: "A good reputation is more valuable than money.", author: "Publilius Syrus", source: "Sententiae, 1st c. BC (Lyman trans.)" },
  { text: "Carpe diem — seize the day.", author: "Horace", source: "Odes I.11, 23 BC" },

  // ── Literary & Enlightenment ─────────────────────────────────────────
  { text: "Annual income twenty pounds, annual expenditure nineteen nineteen and six, result happiness.", author: "Mr. Micawber (Charles Dickens)", source: "David Copperfield, ch. 12, 1850" },
  { text: "A large income is the best recipe for happiness I ever heard of.", author: "Mary Crawford (Jane Austen)", source: "Mansfield Park, ch. 22, 1814" },
  { text: "A man is rich in proportion to the number of things which he can afford to let alone.", author: "Henry David Thoreau", source: "Walden, 1854" },
  { text: "Resolve not to be poor: whatever you have, spend less.", author: "Samuel Johnson", source: "Letter to Boswell, 1782" },
  { text: "Money, says the proverb, makes money. When you have got a little, it is often easy to get more.", author: "Adam Smith", source: "The Wealth of Nations, 1776" },

  // ── Scripture & traditional proverbs (honestly labeled) ─────────────
  { text: "Go to the ant, thou sluggard; consider her ways, and be wise.", author: "Book of Proverbs", source: "Proverbs 6:6, KJV" },
  { text: "The borrower is servant to the lender.", author: "Book of Proverbs", source: "Proverbs 22:7, KJV" },
  { text: "Take care of the pence, and the pounds will take care of themselves.", author: "William Lowndes", source: "quoted in Chesterfield's Letters, 1747" },
  { text: "Slow and steady wins the race.", author: "Aesop's Fables", source: "The Hare and the Tortoise (trad. moral)" },
  { text: "Little by little does the trick.", author: "Aesop's Fables", source: "The Crow and the Pitcher (Jacobs trans., 1894)" },
  { text: "A stitch in time may save nine.", author: "English proverb", source: "Thomas Fuller, Gnomologia, 1732" },
  { text: "Waste not, want not.", author: "English proverb", source: "first recorded 1772" },

  // ── Modern (primary-source letters only) ─────────────────────────────
  { text: "Price is what you pay; value is what you get.", author: "Warren Buffett, crediting Ben Graham", source: "Berkshire Hathaway shareholder letter, 2008" },
  { text: "You only find out who is swimming naked when the tide goes out.", author: "Warren Buffett", source: "Berkshire Hathaway shareholder letter, 2001" },
];
