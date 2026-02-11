import * as React from "react";

import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={cn(
          "w-full rounded-xl2 border border-ink/20 bg-white px-3 py-2 text-sm text-ink shadow-sm outline-none transition focus:border-ink/50 focus:ring-2 focus:ring-mint/60",
          className,
        )}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";
