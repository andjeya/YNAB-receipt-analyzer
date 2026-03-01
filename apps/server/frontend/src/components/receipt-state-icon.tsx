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
    return (
      <svg viewBox="0 0 24 24" className={cn("h-5 w-5", className)} aria-hidden="true">
        <path d="M4 3h4a1 1 0 0 1 1 1v13l-1-0.8-1 0.8-1-0.8-1 0.8V4a1 1 0 0 1 1-1Z" fill={style.fill} stroke={style.stroke} strokeWidth="1.25" />
        <path d="M10 4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v13l-1-0.8-1 0.8-1-0.8-1 0.8V4Z" fill={style.fill} stroke={style.stroke} strokeWidth="1.25" />
        <path d="M17 4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v13l-0.7-0.6-0.7 0.6-0.6-0.6-0.6 0.6V4Z" fill={style.fill} stroke={style.stroke} strokeWidth="1.25" />
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
