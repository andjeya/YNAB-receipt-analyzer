import * as React from "react";

import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={cn(
          "w-full rounded-xl2 border border-ink/15 bg-surface px-3.5 py-2.5 text-sm text-ink shadow-soft outline-none transition placeholder:text-ink/35 hover:border-ink/25 focus:border-mint focus:ring-4 focus:ring-mint/20",
          className,
        )}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";
