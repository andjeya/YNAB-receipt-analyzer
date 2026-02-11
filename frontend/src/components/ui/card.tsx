import { cn } from "@/lib/utils";
import type { HTMLAttributes, ReactNode } from "react";

type CardProps = {
  className?: string;
  children: ReactNode;
} & HTMLAttributes<HTMLElement>;

export function Card({ className, children, ...props }: CardProps) {
  return (
    <article className={cn("rounded-3xl border border-ink/10 bg-white/95 p-4 shadow-float", className)} {...props}>
      {children}
    </article>
  );
}
