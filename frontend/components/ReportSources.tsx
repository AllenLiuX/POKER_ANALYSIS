"use client";

import { BookOpen } from "lucide-react";
import type { KbSource } from "@/lib/api";

/** AI 报告接地的知识库来源清单（德州策略参考资料）。空则不渲染。 */
export default function ReportSources({ sources }: { sources?: KbSource[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="mt-2 border-t border-white/[0.06] pt-2">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-neutral-500">
        <BookOpen className="size-3" />
        参考资料
      </div>
      <ol className="space-y-0.5">
        {sources.map((s) => (
          <li key={s.n} className="flex items-start gap-1.5 text-[11px] leading-snug">
            <span className="mt-px shrink-0 text-neutral-600">[{s.n}]</span>
            <span className="shrink-0 rounded bg-neutral-800/70 px-1 text-[10px] text-neutral-400">{s.concept}</span>
            {s.url ? (
              <a
                href={s.url}
                target="_blank"
                rel="noreferrer"
                className="truncate text-sky-400 hover:text-sky-300 hover:underline"
                title={s.title}
              >
                {s.title || s.url}
              </a>
            ) : (
              <span className="truncate text-neutral-400">{s.title}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
