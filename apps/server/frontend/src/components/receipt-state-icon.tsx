import { cn } from "@/lib/utils";

// The five states a week / receipt can be in. The icon tells the timeliness
// story as a receipt that ages: crisp when filed on time, dog-eared when a bit
// late, crumpled & stained when very late, confetti when shredded (a reward),
// and a pile of ash once a week burns.
export type ReceiptIconState = "green" | "yellow" | "brown" | "shredded" | "burnt";

// At-a-glance tint for each state — reused by the trail legend and any accents
// so colour stays a single source of truth.
export const TINT: Record<ReceiptIconState, string> = {
  green: "#34d399",
  yellow: "#facc15",
  brown: "#a16207",
  shredded: "#a78bfa",
  burnt: "#78716c",
};

export function ReceiptStateIcon({
  state,
  className,
}: {
  state: ReceiptIconState;
  className?: string;
}) {
  const svg = cn("h-5 w-5", className);

  // ── GREEN — crisp, upright receipt with a soft glow ────────────────────────
  // A portrait viewBox crops the padding so the tall receipt fills its (square)
  // box vertically — the trail cells are short and wide, so height is what counts.
  if (state === "green") {
    return (
      <svg viewBox="4.5 1 15 20" className={svg} aria-hidden="true">
        <defs>
          <filter id="rcpt-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feDropShadow dx="0" dy="0" stdDeviation="1.1" floodColor="#34d399" floodOpacity="0.75" />
          </filter>
        </defs>
        <g filter="url(#rcpt-glow)">
          <path
            d="M6 2.5h12a1 1 0 0 1 1 1v16l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3v-16a1 1 0 0 1 1-1Z"
            fill="#34d399"
            stroke="#059669"
            strokeWidth="1.25"
          />
          <path d="M9 8.5h6M9 11.5h6M9 14.5h4" stroke="#065f46" strokeLinecap="round" strokeWidth="1.25" />
        </g>
      </svg>
    );
  }

  // ── YELLOW — a bit late: tilted, one dog-eared corner, gentle wavy bottom ───
  if (state === "yellow") {
    return (
      <svg viewBox="3.5 1.5 17 19" className={svg} aria-hidden="true">
        <g transform="rotate(-6 12 12)">
          {/* body with a folded top-right corner and a softly waving torn edge */}
          <path
            d="M6 3.5h9l3 3v12.5q-1.5 1.4-3 0-1.5-1.4-3 0-1.5 1.4-3 0-1.5-1.4-3 0V4.5a1 1 0 0 1 0-1Z"
            fill="#fde68a"
            stroke="#d97706"
            strokeWidth="1.2"
          />
          {/* the dog-ear fold */}
          <path d="M15 3.5v3h3Z" fill="#fcd34d" stroke="#d97706" strokeWidth="1.2" strokeLinejoin="round" />
          <path d="M9 9.5h5M9 12.5h5M9 15h3" stroke="#92400e" strokeLinecap="round" strokeWidth="1.2" opacity="0.8" />
        </g>
      </svg>
    );
  }

  // ── BROWN — very late: crumpled, stained, faded/broken print, drooping ──────
  if (state === "brown") {
    return (
      <svg viewBox="3.5 2.5 17 19" className={svg} aria-hidden="true">
        <g transform="rotate(5 12 12) translate(0 0.5)">
          {/* crinkled, irregular outline */}
          <path
            d="M6 4.2l2-0.5 2 0.6 2-0.6 2 0.6 1.8-0.5 0.4 0.6v12.4l-1 0.9-2-0.8-2 0.8-2-0.8-2 0.8-1.3-0.7Z"
            fill="#a16207"
            stroke="#57534e"
            strokeWidth="1.2"
            strokeLinejoin="round"
          />
          {/* crease lines suggesting the crumple */}
          <path d="M8 5l1.5 12M14.5 5.2l-1 12" stroke="#7c5410" strokeWidth="0.7" opacity="0.5" />
          {/* broken, faded print lines */}
          <path
            d="M8.5 9h6M8.5 12h6M8.5 14.6h3.5"
            stroke="#44403c"
            strokeLinecap="round"
            strokeWidth="1.1"
            strokeDasharray="2 1.4"
            opacity="0.5"
          />
          {/* stain */}
          <ellipse cx="13.5" cy="13" rx="2.4" ry="1.8" fill="#57534e" opacity="0.22" />
        </g>
      </svg>
    );
  }

  // ── SHREDDED — celebratory confetti (a reward); tone-independent ────────────
  if (state === "shredded") {
    return (
      <svg viewBox="0 0 24 24" className={svg} aria-hidden="true">
        <rect x="3" y="4" width="6" height="3" rx="0.75" fill="#34d399" transform="rotate(-24 6 5.5)" />
        <rect x="14" y="3" width="6" height="3" rx="0.75" fill="#f59e0b" transform="rotate(28 17 4.5)" />
        <rect x="4" y="15" width="6" height="3" rx="0.75" fill="#38bdf8" transform="rotate(18 7 16.5)" />
        <rect x="14" y="16" width="6" height="3" rx="0.75" fill="#f472b6" transform="rotate(-30 17 17.5)" />
        <rect x="9.5" y="9.5" width="5" height="3" rx="0.75" fill="#a78bfa" transform="rotate(12 12 11)" />
        <circle cx="12" cy="4.5" r="1.4" fill="#f472b6" />
        <circle cx="3.8" cy="10.5" r="1.4" fill="#f59e0b" />
        <circle cx="20.2" cy="10.5" r="1.4" fill="#34d399" />
        <circle cx="12" cy="19.5" r="1.4" fill="#38bdf8" />
      </svg>
    );
  }

  // ── BURNT — a pile of ash with a couple of glowing embers and a smoke wisp ──
  return (
    <svg viewBox="1 3 22 19" className={svg} aria-hidden="true">
      {/* smoke wisp */}
      <path
        d="M12 11q2.6-2 0-4 2.6-2 0-4"
        fill="none"
        stroke="#a8a29e"
        strokeWidth="1.1"
        strokeLinecap="round"
        strokeDasharray="1.8 2"
        opacity="0.5"
      />
      {/* mound — taller so it reads at small sizes */}
      <path d="M2 21Q12 8 22 21Z" fill="#78716c" />
      {/* darker lumps */}
      <ellipse cx="8" cy="19.4" rx="3" ry="1.7" fill="#57534e" />
      <ellipse cx="15.5" cy="19" rx="3.4" ry="1.9" fill="#57534e" />
      {/* embers */}
      <circle cx="8.5" cy="19.6" r="1" fill="#fb923c" className="animate-fire-fade" />
      <circle cx="15" cy="19.2" r="1" fill="#f59e0b" className="animate-fire-fade" style={{ animationDelay: "200ms" }} />
    </svg>
  );
}
