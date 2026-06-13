import * as React from "react";

import { cn } from "@/lib/utils";

type ButtonVariant = "solid" | "ghost" | "outline" | "danger" | "success";
type ButtonSize = "sm" | "md" | "lg";

// Solid uses a subtle top-lit gradient on ink so the primary action reads as a
// raised key, not a flat black slab. Every variant shares the same press
// micro-interaction (settle on active) for a consistent, tactile feel.
const variantMap: Record<ButtonVariant, string> = {
  solid:
    "bg-gradient-to-b from-[#243038] to-ink text-sand shadow-float hover:shadow-lift hover:-translate-y-px",
  ghost: "bg-transparent text-ink hover:bg-ink/[0.06]",
  outline: "border border-ink/15 bg-surface text-ink shadow-soft hover:border-ink/30 hover:bg-cream/60",
  danger: "bg-gradient-to-b from-red-500 to-red-600 text-white shadow-float hover:shadow-lift hover:-translate-y-px",
  success:
    "bg-gradient-to-b from-emerald-500 to-emerald-600 text-white shadow-float hover:shadow-lift hover:-translate-y-px",
};

const sizeMap: Record<ButtonSize, string> = {
  sm: "min-h-9 gap-1.5 py-2 px-3 text-sm",
  md: "min-h-11 gap-2 py-2.5 px-4 text-sm",
  lg: "min-h-12 gap-2 py-3 px-5 text-base",
};

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "solid", size = "md", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex select-none items-center justify-center rounded-xl2 font-semibold transition-all duration-150 ease-out active:translate-y-0 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-sand",
          variantMap[variant],
          sizeMap[size],
          className,
        )}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
