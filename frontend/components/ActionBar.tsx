"use client";

import { useEffect, useState } from "react";

const ACTION_STYLE: Record<string, string> = {
  fold: "bg-neutral-700 hover:bg-neutral-600",
  call: "bg-sky-600 hover:bg-sky-500",
  raise: "bg-red-600 hover:bg-red-500",
  allin: "bg-amber-600 hover:bg-amber-500",
  check: "bg-slate-600 hover:bg-slate-500",
  bet: "bg-emerald-600 hover:bg-emerald-500",
};

// 每个动作的键盘快捷键（同一场景里 call / check 不会同时出现，可共用 C）
const HOTKEY: Record<string, string> = {
  fold: "f",
  call: "c",
  check: "c",
  bet: "b",
  raise: "r",
  allin: "a",
};

export interface SizeOption {
  id: string;
  label: string;
  amount_bb?: number;
}

export default function ActionBar({
  actions,
  labels,
  sizes,
  disabled,
  enableHotkeys = true,
  onAct,
}: {
  actions: string[];
  labels: Record<string, string>;
  /** 需要选尺度的动作 → 尺度选项（如 bet / raise）。留空则点击即出招。 */
  sizes?: Record<string, SizeOption[]>;
  disabled?: boolean;
  enableHotkeys?: boolean;
  onAct: (action: string, size?: string) => void;
}) {
  // 当前展开选尺度的动作
  const [open, setOpen] = useState<string | null>(null);
  const openSizes = open ? sizes?.[open] : undefined;

  useEffect(() => {
    setOpen(null);
  }, [actions]);

  useEffect(() => {
    if (!enableHotkeys || disabled) return;
    function onKey(e: KeyboardEvent) {
      const k = e.key.toLowerCase();
      if (open && openSizes && openSizes.length) {
        const n = parseInt(k, 10);
        if (!Number.isNaN(n) && n >= 1 && n <= openSizes.length) {
          e.preventDefault();
          const opt = openSizes[n - 1];
          setOpen(null);
          onAct(open, opt.id);
          return;
        }
        if (k === "escape" || k === "backspace") {
          e.preventDefault();
          setOpen(null);
        }
        return;
      }
      for (const a of actions) {
        if (HOTKEY[a] === k) {
          e.preventDefault();
          if (sizes?.[a]?.length) setOpen(a);
          else onAct(a);
          return;
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [actions, sizes, open, openSizes, enableHotkeys, disabled, onAct]);

  if (open && openSizes && openSizes.length) {
    return (
      <div className="flex flex-col items-center gap-2">
        <div className="text-xs text-neutral-500">
          {labels[open] ?? open} 多少？
          <span className="ml-1 text-neutral-600">
            按 1–{openSizes.length} 选择 · Esc 返回
          </span>
        </div>
        <div className="flex flex-wrap justify-center gap-3">
          {openSizes.map((opt, i) => (
            <button
              key={opt.id}
              disabled={disabled}
              onClick={() => {
                setOpen(null);
                onAct(open, opt.id);
              }}
              className={`min-w-[92px] rounded-xl px-5 py-2.5 text-white shadow transition disabled:cursor-not-allowed disabled:opacity-40 ${
                ACTION_STYLE[open] ?? "bg-neutral-700 hover:bg-neutral-600"
              }`}
            >
              <div className="text-base font-semibold leading-tight">{opt.label}</div>
              {opt.amount_bb != null && (
                <div className="text-[11px] opacity-80">{opt.amount_bb}bb</div>
              )}
              <div className="mt-0.5 text-[10px] opacity-60">键 {i + 1}</div>
            </button>
          ))}
          <button
            onClick={() => setOpen(null)}
            className="min-w-[64px] rounded-xl bg-neutral-800 px-4 py-2.5 text-sm text-neutral-300 transition hover:bg-neutral-700"
          >
            返回
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap justify-center gap-3">
      {actions.map((a) => {
        const hasSizes = Boolean(sizes?.[a]?.length);
        return (
          <button
            key={a}
            disabled={disabled}
            onClick={() => (hasSizes ? setOpen(a) : onAct(a))}
            className={`min-w-[96px] rounded-xl px-6 py-3 text-base font-semibold text-white shadow transition disabled:cursor-not-allowed disabled:opacity-40 ${
              ACTION_STYLE[a] ?? "bg-neutral-700 hover:bg-neutral-600"
            }`}
          >
            {labels[a] ?? a}
            {hasSizes && <span className="ml-1 opacity-70">▸</span>}
          </button>
        );
      })}
    </div>
  );
}
