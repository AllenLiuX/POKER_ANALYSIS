import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/** 统一表面：细边框 + 轻微背景，圆角一致。accent 用于强调面板。 */
export function Card({
  className,
  accent,
  ...props
}: HTMLAttributes<HTMLDivElement> & { accent?: "emerald" | "fuchsia" | "sky" | null }) {
  const accentCls =
    accent === "emerald"
      ? "border-emerald-600/30 bg-gradient-to-b from-emerald-950/20 to-neutral-900/40"
      : accent === "fuchsia"
        ? "border-fuchsia-700/30 bg-gradient-to-b from-fuchsia-950/20 to-neutral-900/40"
        : accent === "sky"
          ? "border-sky-700/30 bg-gradient-to-b from-sky-950/20 to-neutral-900/40"
          : "border-white/[0.07] bg-neutral-900/50";
  return (
    <div
      className={cn(
        "rounded-2xl border shadow-sm shadow-black/20 backdrop-blur-[2px]",
        accentCls,
        className,
      )}
      {...props}
    />
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4 sm:p-5", className)} {...props} />;
}

/** 小节标题：可选图标 + 大写字距标签。 */
export function SectionLabel({
  icon,
  children,
  className,
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-neutral-500", className)}>
      {icon}
      {children}
    </div>
  );
}
