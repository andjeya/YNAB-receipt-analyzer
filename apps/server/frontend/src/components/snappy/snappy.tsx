"use client";

// Snappy — the YNAB receipt mascot.
// Concept: a cute receipt-slip character (NOT any existing brand likeness).
// viewBox="0 0 64 80" shared across all poses.
// Theme tokens: sand body (#fff8ed), ink outline+face (#172026), mint lines (#7ad6c2), ember cheeks (#f78b5d).

export type SnappyPose = "idle" | "happy" | "concerned" | "celebrating" | "asleep";

interface SnappyProps {
  pose?: SnappyPose;
  /** Tailwind size classes e.g. "h-16 w-16". Default h-16 w-16 */
  size?: string;
  className?: string;
}

// Shared body paths -------------------------------------------------------

/** Torn bottom edge: 5-triangle zig-zag hem along the bottom of the body */
const TornHem = () => (
  <polyline
    points="12,66 18,58 24,66 30,58 36,66 42,58 48,66 52,66"
    fill="none"
    stroke="#172026"
    strokeWidth="2"
    strokeLinejoin="round"
    strokeLinecap="round"
  />
);

/** 2 short mint "text" lines tucked at the top so the face is the focal point */
const MintLines = () => (
  <>
    <rect x="21" y="15" width="22" height="2.6" rx="1.3" fill="#7ad6c2" opacity="0.9" />
    <rect x="21" y="20" width="15" height="2.6" rx="1.3" fill="#7ad6c2" opacity="0.7" />
  </>
);

/** Ember cheek dots — always on, gives warmth */
const Cheeks = () => (
  <>
    <circle cx="20" cy="49" r="4.5" fill="#f78b5d" opacity="0.4" />
    <circle cx="44" cy="49" r="4.5" fill="#f78b5d" opacity="0.4" />
  </>
);

/** Receipt body: rounded-corner rectangle with sand fill */
const Body = () => (
  <rect x="12" y="8" width="40" height="58" rx="11" fill="#fff8ed" stroke="#172026" strokeWidth="2.5" />
);

/** Two little stubby feet for character */
const Feet = () => (
  <>
    <rect x="22" y="66" width="7" height="5" rx="2.5" fill="#fff8ed" stroke="#172026" strokeWidth="2" />
    <rect x="35" y="66" width="7" height="5" rx="2.5" fill="#fff8ed" stroke="#172026" strokeWidth="2" />
  </>
);

/** A round eye with a catchlight — the key cuteness lever */
const Eye = ({ cx }: { cx: number }) => (
  <>
    <circle cx={cx} cy="43" r="5" fill="#172026" />
    <circle cx={cx - 1.6} cy="41.2" r="1.6" fill="#fff8ed" />
  </>
);

// Pose-specific face parts ------------------------------------------------

function IdleFace() {
  return (
    <>
      {/* Big round eyes with catchlights */}
      <Eye cx={24} />
      <Eye cx={40} />
      {/* Warm little smile */}
      <path d="M 25 53 Q 32 59 39 53" fill="none" stroke="#172026" strokeWidth="2.4" strokeLinecap="round" />
    </>
  );
}

function HappyFace() {
  return (
    <>
      {/* Upward-arc "happy" eyes */}
      <path d="M 21 46 Q 24 41 27 46" fill="none" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M 37 46 Q 40 41 43 46" fill="none" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      {/* Bigger smile */}
      <path d="M 22 52 Q 32 61 42 52" fill="none" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
    </>
  );
}

function ConcernedFace() {
  return (
    <>
      {/* One raised eyebrow (left) */}
      <path d="M 19 35 Q 24 32 29 35" fill="none" stroke="#172026" strokeWidth="2" strokeLinecap="round" />
      {/* Big round eyes with catchlights */}
      <Eye cx={24} />
      <Eye cx={40} />
      {/* Slight worried frown */}
      <path d="M 25 55 Q 32 51 39 55" fill="none" stroke="#172026" strokeWidth="2.2" strokeLinecap="round" />
      {/* Amber "needs your eyes" dot badge top-right */}
      <circle cx="50" cy="10" r="5" fill="#f78b5d" />
      <text x="50" y="14" textAnchor="middle" fontSize="7" fontWeight="bold" fill="#172026">!</text>
    </>
  );
}

function CelebratingFace() {
  return (
    <>
      {/* ^^ eyes */}
      <path d="M 20 45 Q 24 40 28 45" fill="none" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M 36 45 Q 40 40 44 45" fill="none" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      {/* Wide smile */}
      <path d="M 21 52 Q 32 62 43 52" fill="none" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      {/* Ember sparkle ticks */}
      <path d="M 8 20 L 8 26 M 5 23 L 11 23" stroke="#f78b5d" strokeWidth="2" strokeLinecap="round" />
      <path d="M 55 15 L 55 21 M 52 18 L 58 18" stroke="#f78b5d" strokeWidth="2" strokeLinecap="round" />
      <path d="M 57 32 L 59 36 M 55 34 L 61 34" stroke="#f78b5d" strokeWidth="1.5" strokeLinecap="round" />
    </>
  );
}

function AsleepFace() {
  return (
    <>
      {/* Closed eyes — flat lines */}
      <path d="M 20 44 L 28 44" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M 36 44 L 44 44" stroke="#172026" strokeWidth="2.5" strokeLinecap="round" />
      {/* Tiny mouth */}
      <circle cx="32" cy="52" r="1.5" fill="#172026" />
      {/* Floating z z */}
      <text x="44" y="26" fontSize="7" fontWeight="bold" fill="#172026" opacity="0.4">z</text>
      <text x="49" y="18" fontSize="5" fontWeight="bold" fill="#172026" opacity="0.25">z</text>
    </>
  );
}

// Pose animation class map ------------------------------------------------
const POSE_ANIM: Record<SnappyPose, string> = {
  idle: "animate-snappy-bob",
  happy: "animate-snappy-pop",
  concerned: "animate-snappy-lean",
  celebrating: "animate-snappy-pop",
  asleep: "animate-snappy-breathe",
};

// Pose tilt transform (concerned body tilts ~4°) --------------------------
const POSE_TRANSFORM: Partial<Record<SnappyPose, string>> = {
  concerned: "rotate(4, 32, 44)",
};

export function Snappy({ pose = "idle", size = "h-16 w-16", className = "" }: SnappyProps) {
  const animClass = POSE_ANIM[pose];
  const bodyTransform = POSE_TRANSFORM[pose];

  return (
    <svg
      viewBox="0 0 64 80"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      data-testid="snappy"
      data-pose={pose}
      className={`${size} ${animClass} ${className}`.trim()}
    >
      {/* Little feet drawn behind the body so they peek out below the hem */}
      <Feet />

      {/* Receipt body group — may rotate for concerned */}
      <g transform={bodyTransform}>
        <Body />
        <TornHem />
        <MintLines />
        <Cheeks />
      </g>

      {/* Face is drawn independently so the badge stays outside the tilt */}
      {pose === "idle" && <IdleFace />}
      {pose === "happy" && <HappyFace />}
      {pose === "concerned" && <ConcernedFace />}
      {pose === "celebrating" && <CelebratingFace />}
      {pose === "asleep" && <AsleepFace />}
    </svg>
  );
}
