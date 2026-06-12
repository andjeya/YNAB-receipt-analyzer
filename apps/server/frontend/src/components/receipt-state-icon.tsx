import { cn } from "@/lib/utils";

type ReceiptTone = "green" | "yellow" | "brown";

const TONE_STYLES: Record<ReceiptTone, { fill: string; stroke: string; line: string }> = {
  green: {
    fill: "#34d399",
    stroke: "#059669",
    line: "#065f46",
  },
  yellow: {
    fill: "#facc15",
    stroke: "#d97706",
    line: "#92400e",
  },
  brown: {
    fill: "#a16207",
    stroke: "#57534e",
    line: "#44403c",
  },
};

export function ReceiptStateIcon({
  tone,
  shredded = false,
  className,
}: {
  tone: ReceiptTone;
  shredded?: boolean;
  className?: string;
}) {
  const style = TONE_STYLES[tone];

  if (shredded) {
    // Shredding is a reward (spending an earned token), so the icon is
    // celebratory confetti — deliberately multicolored and circle-free so it
    // can't be mistaken for the brown "very late" dot. `tone` is ignored.
    return (
      <svg viewBox="0 0 24 24" className={cn("h-5 w-5", className)} aria-hidden="true">
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

  return (
    <svg viewBox="0 0 24 24" className={cn("h-5 w-5", className)} aria-hidden="true">
      <path
        d="M6 2.5h12a1 1 0 0 1 1 1v16l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3v-16a1 1 0 0 1 1-1Z"
        fill={style.fill}
        stroke={style.stroke}
        strokeWidth="1.25"
      />
      <path d="M9 8.5h6M9 11.5h6M9 14.5h4" stroke={style.line} strokeLinecap="round" strokeWidth="1.25" />
    </svg>
  );
}
