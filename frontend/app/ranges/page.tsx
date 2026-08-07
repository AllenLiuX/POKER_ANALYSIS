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
const SPOT_TABS: { id: string; label: string }[] = [
  { id: "RFI", label: "开池 (RFI)" },
  { id: "vs_RFI", label: "防守 (面对开池)" },
];

const ACTION_COLOR: Record<string, string> = {
  raise: "#dc2626",
  call: "#0284c7",
  allin: "#d97706",
};
const FOLD_COLOR = "#3f3f46";
const ACTION_LABEL: Record<string, string> = {
  raise: "加注 (3bet)",
  call: "跟注",
  fold: "弃牌",
  allin: "全下",
};

function matchupLabel(pos: string): string {
  return pos.replace("_vs_", " vs ");
}

// 由各动作频率拼一条从底部向上填充的渐变：raise → call → (剩余) fold
function cellBackground(freqs: Record<string, number>): string {
  const segs: string[] = [];
  let acc = 0;
  for (const a of ["raise", "call", "allin"]) {
    const f = freqs[a] ?? 0;
    if (f > 0) {
      segs.push(`${ACTION_COLOR[a]} ${acc * 100}% ${(acc + f) * 100}%`);
      acc += f;
    }
  }
  segs.push(`${FOLD_COLOR} ${acc * 100}% 100%`);
  return `linear-gradient(to top, ${segs.join(", ")})`;
}

export default function RangesPage() {
  const [index, setIndex] = useState<SpotIndexEntry[]>([]);
  const [spot, setSpot] = useState<string>("RFI");
  const [position, setPosition] = useState<string>("");
  const [grid, setGrid] = useState<RangeGrid | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getRangeIndex().then(setIndex).catch((e) => setErr(String(e)));
  }, []);

  // 当前 spot 下可选的位置/对局
  const positions = useMemo(() => {
    const forSpot = index.filter((s) => s.spot === spot);
    if (spot === "RFI") {
      return POSITION_ORDER.filter((p) => forSpot.some((s) => s.position === p));
    }
    return forSpot.map((s) => s.position);
  }, [index, spot]);

  // spot 或可选项变化时，确保 position 合法
  useEffect(() => {
    if (positions.length && !positions.includes(position)) {
      setPosition(positions[0]);
    }
  }, [positions, position]);

  useEffect(() => {
    if (!position) return;
    setErr(null);
    getRangeGrid("6max_100bb", spot, position)
      .then(setGrid)
      .catch((e) => setErr(String(e)));
  }, [spot, position]);

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← 返回
        </Link>
        <h1 className="text-2xl font-bold">
          翻前范围表{" "}
          <span className="text-sm font-normal text-neutral-500">6-max · 100bb</span>
        </h1>
      </div>

      {/* spot 类型 */}
      <div className="mb-3 flex gap-2">
        {SPOT_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setSpot(t.id)}
            className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
              t.id === spot
                ? "bg-neutral-100 text-neutral-900"
                : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 位置 / 对局 */}
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
            {spot === "RFI" ? p : matchupLabel(p)}
          </button>
        ))}
      </div>

      {err && <p className="text-red-400">错误：{err}</p>}

      {grid && (
        <>
          <RangeGridView grid={grid} />
          <Legend actions={grid.actions} />
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
        const freqs = cell.freqs;
        const nonFold = grid.actions.reduce((s, a) => s + (freqs[a] ?? 0), 0);
        const mixed =
          grid.actions.filter((a) => (freqs[a] ?? 0) > 0.001).length +
            ((freqs.fold ?? 0) > 0.001 ? 1 : 0) >
          1;
        const title =
          `${cell.hand_class} · ` +
          [...grid.actions, "fold"]
            .map((a) => `${ACTION_LABEL[a] ?? a} ${((freqs[a] ?? 0) * 100).toFixed(0)}%`)
            .join(" / ");
        return (
          <div
            key={i}
            title={title}
            className="flex aspect-square items-center justify-center rounded-[3px] text-[10px] font-semibold sm:text-xs"
            style={{ backgroundImage: cellBackground(freqs) }}
          >
            <span
              className="text-neutral-50"
              style={{
                textShadow: "0 1px 2px rgba(0,0,0,0.9)",
                fontWeight: mixed && nonFold > 0 ? 700 : 500,
              }}
            >
              {cell.hand_class}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Legend({ actions }: { actions: string[] }) {
  const items = [...actions, "fold"];
  return (
    <div className="mt-3 flex items-center gap-4 text-xs text-neutral-400">
      {items.map((a) => (
        <span key={a} className="flex items-center gap-1.5">
          <span
            className="inline-block h-3 w-3 rounded-sm"
            style={{ backgroundColor: a === "fold" ? FOLD_COLOR : ACTION_COLOR[a] }}
          />
          {ACTION_LABEL[a] ?? a}
        </span>
      ))}
      <span className="ml-2 text-neutral-600">格子按频率上下分区（下=进攻）</span>
    </div>
  );
}
