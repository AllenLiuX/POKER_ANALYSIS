import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const badge = cva(
  "inline-flex items-center gap-1 rounded-full font-medium ring-1 whitespace-nowrap [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "bg-white/[0.04] text-neutral-300 ring-white/10",
        success: "bg-emerald-500/12 text-emerald-300 ring-emerald-500/25",
        warning: "bg-amber-500/12 text-amber-300 ring-amber-500/25",
        danger: "bg-red-500/12 text-red-300 ring-red-500/25",
        info: "bg-sky-500/12 text-sky-300 ring-sky-500/25",
        accent: "bg-fuchsia-500/12 text-fuchsia-300 ring-fuchsia-500/25",
      },
      size: {
        sm: "px-1.5 py-0.5 text-[10px]",
        md: "px-2 py-0.5 text-xs",
      },
    },
    defaultVariants: { variant: "neutral", size: "md" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badge> {}

export function Badge({ className, variant, size, ...props }: BadgeProps) {
  return <span className={cn(badge({ variant, size }), className)} {...props} />;
}

export { badge };
