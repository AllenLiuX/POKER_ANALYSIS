"use client";

import type { DevCat, DevFlag } from "@/lib/opponents";

// 偏移方向配色：偏紧/被动=蓝，偏松/黏=橙，偏凶=红。
const CAT_CLS: Record<DevCat, string> = {
  tight: "bg-sky-500/15 text-sky-300 ring-sky-500/25",
  loose: "bg-amber-500/15 text-amber-300 ring-amber-500/25",
  aggro: "bg-red-500/15 text-red-300 ring-red-500/25",
};
const CAT_DOT: Record<DevCat, string> = {
  tight: "bg-sky-400",
  loose: "bg-amber-400",
  aggro: "bg-red-400",
};

/** GTO 偏移标签行：颜色区分偏紧/偏松/偏凶，悬停显示剥削建议。 */
export default function DeviationFlags({
  flags,
  className = "",
}: {
  flags: DevFlag[];
  className?: string;
}) {
  if (!flags.length) return null;
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {flags.map((f) => (
        <span
          key={f.key}
          title={f.conf === "low" ? `${f.hint}（初判 · 样本少）` : f.hint}
          className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1 ${CAT_CLS[f.cat]} ${
            f.conf === "low" ? "opacity-70" : ""
          }`}
        >
          <span className={`inline-block size-1.5 rounded-full ${CAT_DOT[f.cat]}`} />
          {f.label}
          {f.conf === "low" && <span className="text-[8px] opacity-70">初</span>}
        </span>
      ))}
    </div>
  );
}
