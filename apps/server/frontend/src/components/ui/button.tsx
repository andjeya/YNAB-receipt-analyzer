import * as React from "react";

import { cn } from "@/lib/utils";

type ButtonVariant = "solid" | "ghost" | "outline" | "danger" | "success";
type ButtonSize = "sm" | "md" | "lg";

const variantMap: Record<ButtonVariant, string> = {
  solid: "bg-ink text-sand shadow-float hover:opacity-95",
  ghost: "bg-transparent text-ink hover:bg-ink/5",
  outline: "border border-ink/25 bg-white text-ink hover:bg-ink/5",
  danger: "bg-red-600 text-white hover:bg-red-700",
  success: "bg-emerald-600 text-white shadow-float hover:bg-emerald-700",
};

const sizeMap: Record<ButtonSize, string> = {
  sm: "min-h-9 py-2 px-3 text-sm",
  md: "min-h-11 py-2.5 px-4 text-sm",
  lg: "min-h-12 py-3 px-5 text-base",
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
          "inline-flex items-center justify-center rounded-xl2 font-semibold transition disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint/70 focus-visible:ring-offset-2 focus-visible:ring-offset-sand",
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
