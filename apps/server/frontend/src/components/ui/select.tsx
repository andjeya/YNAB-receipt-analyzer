import * as React from "react";

import { cn } from "@/lib/utils";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => {
    return (
      <select
        ref={ref}
        // appearance-none + a custom chevron replaces the inconsistent native
        // arrow (a classic unstyled-form tell) with an on-brand caret.
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23172026' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E\")",
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 0.85rem center",
        }}
        className={cn(
          "h-11 w-full appearance-none rounded-xl2 border border-ink/15 bg-surface pl-3.5 pr-10 text-sm text-ink shadow-soft outline-none transition hover:border-ink/25 focus:border-mint focus:ring-4 focus:ring-mint/20",
          className,
        )}
        {...props}
      >
        {children}
      </select>
    );
  },
);
Select.displayName = "Select";
