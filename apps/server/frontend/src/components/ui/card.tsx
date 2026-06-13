import { cn } from "@/lib/utils";
import type { HTMLAttributes, ReactNode } from "react";

type CardProps = {
  className?: string;
  children: ReactNode;
} & HTMLAttributes<HTMLElement>;

export function Card({ className, children, ...props }: CardProps) {
  return (
    <article
      className={cn(
        "rounded-3xl border border-ink/[0.07] bg-surface/95 p-4 shadow-soft backdrop-blur-[2px]",
        className,
      )}
      {...props}
    >
      {children}
    </article>
  );
}
