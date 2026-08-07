"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  getRangeGrid,
  getRangeIndex,
  type RangeGrid,
  type SpotIndexEntry,
} from "@/lib/api";

const POSITION_ORDER = ["UTG", "MP", "CO", "BTN", "SB", "BB"];

export default function RangesPage() {
  const [index, setIndex] = useState<SpotIndexEntry[]>([]);
  const [position, setPosition] = useState<string>("CO");
  const [grid, setGrid] = useState<RangeGrid | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getRangeIndex().then(setIndex).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    setErr(null);
    getRangeGrid("6max_100bb", "RFI", position)
      .then(setGrid)
      .catch((e) => setErr(String(e)));
  }, [position]);

  const positions = useMemo(() => {
    const avail = new Set(index.map((s) => s.position));
    return POSITION_ORDER.filter((p) => avail.has(p));
  }, [index]);

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← 返回
        </Link>
        <h1 className="text-2xl font-bold">翻前范围表 <span className="text-sm font-normal text-neutral-500">6-max · 100bb · RFI</span></h1>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {positions.map((p) => (
          <button
            key={p}
            onClick={() => setPosition(p)}
            className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
              p === position
                ? "bg-emerald-600 text-white"
                : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {err && <p className="text-red-400">错误：{err}</p>}

      {grid && (
        <>
          <RangeGridView grid={grid} />
          <Legend />
          {typeof grid.meta.note === "string" && (
            <p className="mt-4 text-xs text-neutral-500">
              数据来源：{String(grid.meta.source)} · {String(grid.meta.note)}
            </p>
          )}
        </>
      )}
    </main>
  );
}

function raiseColor(raise: number): string {
  // raise 频率 -> 红色深浅；纯 fold -> 深灰
  if (raise <= 0) return "rgba(64,64,64,0.5)";
  const alpha = 0.25 + 0.75 * raise;
  return `rgba(220,38,38,${alpha})`; // red-600
}

function RangeGridView({ grid }: { grid: RangeGrid }) {
  const size = 13;
  const byRowCol = new Map<string, (typeof grid.cells)[number]>();
  grid.cells.forEach((c) => byRowCol.set(`${c.row}-${c.col}`, c));

  return (
    <div
      className="grid gap-[2px] rounded-lg bg-neutral-900 p-2"
      style={{ gridTemplateColumns: `repeat(${size}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: size * size }).map((_, i) => {
        const row = Math.floor(i / size);
        const col = i % size;
        const cell = byRowCol.get(`${row}-${col}`);
        if (!cell) return <div key={i} />;
        const raise = cell.freqs.raise ?? 0;
        const mixed = raise > 0 && raise < 1;
        return (
          <div
            key={i}
            title={`${cell.hand_class} · raise ${(raise * 100).toFixed(0)}% / fold ${(
              (cell.freqs.fold ?? 0) * 100
            ).toFixed(0)}%`}
            className="flex aspect-square items-center justify-center rounded-[3px] text-[10px] font-medium sm:text-xs"
            style={{ backgroundColor: raiseColor(raise) }}
          >
            <span className={mixed ? "text-amber-200" : "text-neutral-100"}>
              {cell.hand_class}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-3 flex items-center gap-4 text-xs text-neutral-400">
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: "rgba(220,38,38,0.9)" }} />
        raise
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-sm text-amber-200" style={{ backgroundColor: "rgba(220,38,38,0.55)" }} />
        <span className="text-amber-200">混合（黄字）</span>
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: "rgba(64,64,64,0.5)" }} />
        fold
      </span>
    </div>
  );
}
