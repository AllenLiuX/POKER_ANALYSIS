"use client";

import type { DevCat, DevConf, DevFlag } from "@/lib/opponents";

// 色相=方向（蓝紧/黄黏/红凶）；深浅=置信度（浅初判、深可信）。
const CAT_CLS: Record<DevCat, Record<DevConf, string>> = {
  tight: {
    low: "bg-transparent text-sky-400/75 ring-sky-500/20",
    high: "bg-sky-500/35 text-sky-100 ring-sky-300/60 font-semibold",
  },
  loose: {
    low: "bg-transparent text-amber-400/75 ring-amber-500/20",
    high: "bg-amber-500/35 text-amber-100 ring-amber-300/60 font-semibold",
  },
  aggro: {
    low: "bg-transparent text-red-400/75 ring-red-500/20",
    high: "bg-red-500/35 text-red-100 ring-red-300/60 font-semibold",
  },
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
          className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1 ${CAT_CLS[f.cat][f.conf]}`}
        >
          <span className={`inline-block size-1.5 rounded-full ${CAT_DOT[f.cat]}`} />
          {f.label}
          {f.conf === "low" && <span className="text-[8px] opacity-70">初</span>}
        </span>
      ))}
    </div>
  );
}
