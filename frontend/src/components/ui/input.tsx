import * as React from "react";

import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => {
    return (
      <input
        ref={ref}
        className={cn(
          "h-11 w-full rounded-xl2 border border-ink/20 bg-white px-3 text-sm text-ink shadow-sm outline-none transition focus:border-ink/50 focus:ring-2 focus:ring-mint/60",
          className,
        )}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";
