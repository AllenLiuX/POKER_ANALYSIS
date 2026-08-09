import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const button = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 disabled:pointer-events-none disabled:opacity-40 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-emerald-500 text-emerald-950 hover:bg-emerald-400 shadow-sm shadow-emerald-500/20",
        accent: "bg-fuchsia-500 text-fuchsia-950 hover:bg-fuchsia-400 shadow-sm shadow-fuchsia-500/20",
        secondary: "border border-white/10 bg-white/[0.03] text-neutral-200 hover:bg-white/[0.07] hover:border-white/20",
        ghost: "text-neutral-400 hover:bg-white/5 hover:text-neutral-100",
        danger: "border border-red-800/50 bg-red-950/30 text-red-300 hover:bg-red-950/60",
      },
      size: {
        sm: "h-8 px-3 text-xs [&_svg]:size-3.5",
        md: "h-9 px-4 text-sm [&_svg]:size-4",
        lg: "h-11 px-5 text-sm [&_svg]:size-4",
        icon: "h-9 w-9 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";

export { Button, button };
