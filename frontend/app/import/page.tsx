"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getHealth,
  postExploit,
  postIngestAnalyze,
  postIngestExtract,
  type Analysis,
  type Deviation,
  type ExploitProfileInput,
  type ExploitResult,
  type IngestItem,
  type IngestPlayerObs,
  type ObservationFacts,
  type Reconstruction,
} from "@/lib/api";
import {
  addImportEntries,
  buildEntry,
  clearImportHistory,
  loadImportHistory,
  patchImportItems,
  removeImportEntry,
  type ImportEntry,
} from "@/lib/importHistory";
import PlayingCard from "@/components/PlayingCard";

const TYPE_LABEL: Record<string, string> = {
  hand_replay: "手牌回放",
  result_summary: "结算画面",
  unknown: "未知类型",
};
const TYPE_STYLE: Record<string, string> = {
  hand_replay: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  result_summary: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  unknown: "bg-neutral-700/40 text-neutral-300 ring-neutral-600/40",
};

const RECON_STATUS: Record<string, { label: string; cls: string }> = {
  validated: { label: "重建已校验", cls: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30" },
  needs_review: { label: "重建待复核", cls: "bg-amber-500/15 text-amber-300 ring-amber-500/30" },
  needs_user: { label: "需人工确认", cls: "bg-neutral-700/40 text-neutral-300 ring-neutral-600/40" },
};

const GRADE_STYLE: Record<string, string> = {
  optimal: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  acceptable: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  mistake: "bg-red-500/15 text-red-300 ring-red-500/30",
};

const MAX_BYTES = 8 * 1024 * 1024;
const MAX_FILES = 12;

interface Picked {
  file: File;
  url: string;
}

export default function ImportPage() {
  const [picked, setPicked] = useState<Picked[]>([]);
  const [history, setHistory] = useState<ImportEntry[]>([]);
  const [runErrors, setRunErrors] = useState<IngestItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [ingestEnabled, setIngestEnabled] = useState<boolean | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setIngestEnabled(Boolean(h.capabilities?.screenshot_ingest)))
      .catch(() => setIngestEnabled(null));
  }, []);

  // 刷新后从本地恢复历史
  useEffect(() => {
    setHistory(loadImportHistory());
  }, []);

  // 卸载时释放所有预览 URL
  useEffect(() => {
    return () => picked.forEach((p) => URL.revokeObjectURL(p.url));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addFiles = useCallback((list: FileList | File[] | null) => {
    if (!list) return;
    setErr(null);
    const incoming = Array.from(list);
    const rejected: string[] = [];
    setPicked((prev) => {
      const next = [...prev];
      for (const f of incoming) {
        if (!f.type.startsWith("image/")) {
          rejected.push(`${f.name}（非图片）`);
          continue;
        }
        if (f.size > MAX_BYTES) {
          rejected.push(`${f.name}（超 8MB）`);
          continue;
        }
        if (next.some((p) => p.file.name === f.name && p.file.size === f.size)) continue;
        if (next.length >= MAX_FILES) {
          rejected.push(`${f.name}（超 ${MAX_FILES} 张上限）`);
          continue;
        }
        next.push({ file: f, url: URL.createObjectURL(f) });
      }
      return next;
    });
    if (rejected.length) setErr(`已跳过：${rejected.join("、")}`);
  }, []);

  const removeAt = useCallback((idx: number) => {
    setPicked((prev) => {
      const p = prev[idx];
      if (p) URL.revokeObjectURL(p.url);
      return prev.filter((_, i) => i !== idx);
    });
  }, []);

  const clearAll = useCallback(() => {
    setPicked((prev) => {
      prev.forEach((p) => URL.revokeObjectURL(p.url));
      return [];
    });
    setErr(null);
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  const run = useCallback(async () => {
    if (picked.length === 0) return;
    setLoading(true);
    setErr(null);
    setRunErrors([]);
    const files = picked.map((p) => p.file);
    try {
      const res = await postIngestExtract(files);
      // 结果与文件同序：ok 的入历史（含缩略图），硬失败的作为本次临时错误展示
      const okItems = res.results.filter((r) => r.ok);
      const failed = res.results.filter((r) => !r.ok);
      const entries = await Promise.all(
        okItems.map((item) => {
          const idx = res.results.indexOf(item);
          return buildEntry(item, files[idx] ?? null);
        }),
      );
      if (entries.length) setHistory(addImportEntries(entries));
      setRunErrors(failed);
      // 已入历史，重置上传区
      setPicked((prev) => {
        prev.forEach((p) => URL.revokeObjectURL(p.url));
        return [];
      });
      if (inputRef.current) inputRef.current.value = "";
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [picked]);

  const deleteEntry = useCallback((id: string) => {
    setHistory(removeImportEntry(id));
  }, []);

  const clearHistory = useCallback(() => {
    setHistory(clearImportHistory());
  }, []);

  const [exploit, setExploit] = useState<{
    loading: boolean;
    data: ExploitResult | null;
    error: string | null;
  }>({ loading: false, data: null, error: null });

  const runExploit = useCallback(async () => {
    setExploit({ loading: true, data: null, error: null });
    try {
      // 回填：为早于 S3 的历史条目（有重建但无偏离标注）按需补算，并持久化，使其卡片也能展示偏离
      const cur = loadImportHistory();
      const missing = cur.filter(
        (e) => e.item?.ok && e.item?.recognized && e.item?.reconstruction && !e.item?.analysis,
      );
      if (missing.length) {
        const patches: Record<string, IngestItem> = {};
        await Promise.all(
          missing.map(async (e) => {
            try {
              const { analysis } = await postIngestAnalyze(e.item.facts, e.item.reconstruction ?? null);
              patches[e.id] = { ...e.item, analysis };
            } catch {
              /* 单条失败不影响整体 */
            }
          }),
        );
        if (Object.keys(patches).length) setHistory(patchImportItems(patches));
      }

      const { opponents, hero, groundedTotal } = buildExploitInputs(loadImportHistory());
      if (groundedTotal === 0) {
        setExploit({
          loading: false,
          data: null,
          error: "暂无可接地的决策点（多为未摊牌/位置缺失）。多导入几张带底牌摊牌的手牌回放再试。",
        });
        return;
      }
      const data = await postExploit({ opponents, hero });
      setExploit({ loading: false, data, error: null });
    } catch (e) {
      setExploit({ loading: false, data: null, error: String(e instanceof Error ? e.message : e) });
    }
  }, []);

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[420px] bg-gradient-to-b from-emerald-500/10 via-emerald-500/[0.02] to-transparent"
      />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <header className="mb-8 mt-2">
          <div className="mb-2 flex items-center gap-2 text-xs">
            <span className="rounded-full bg-emerald-900/60 px-2 py-0.5 text-emerald-300">
              Beta
            </span>
          </div>
          <h1 className="text-3xl font-black tracking-tight sm:text-4xl">
            WePoker 截图 →{" "}
            <span className="bg-gradient-to-r from-emerald-300 to-teal-300 bg-clip-text text-transparent">
              观测事实 + 重建
            </span>
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-neutral-400">
            可一次拖入多张微扑克手牌回放截图。多模态模型先转写「看得见的事实」，引擎再把逐街动作
            重建为下注序列并校验筹码守恒。若某张图不是手牌截图，会单独给出提示，不影响其它图。
          </p>
        </header>

        {ingestEnabled === false && (
          <div className="mb-6 rounded-xl border border-amber-700/50 bg-amber-950/30 px-4 py-3 text-sm text-amber-200">
            后端未配置 LLM 视觉能力（MODEL_GATEWAY_KEY / OPENAI_API_KEY）。上传可用，但解析会返回 503。
          </div>
        )}

        {/* 上传区 */}
        <section>
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              addFiles(e.dataTransfer.files);
            }}
            className={`flex min-h-[160px] cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-4 transition ${
              dragging
                ? "border-emerald-500 bg-emerald-950/30"
                : "border-neutral-800 bg-neutral-900/40 hover:border-neutral-700"
            }`}
          >
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
            <div className="px-6 text-center">
              <div className="text-3xl">📸</div>
              <p className="mt-2 text-sm font-medium text-neutral-300">
                点击选择 或 拖拽多张截图到这里
              </p>
              <p className="mt-1 text-xs text-neutral-500">
                PNG / JPG / WebP · 单张 ≤ 8MB · 最多 {MAX_FILES} 张
              </p>
            </div>
          </label>

          {picked.length > 0 && (
            <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5 md:grid-cols-6">
              {picked.map((p, i) => (
                <div
                  key={`${p.file.name}-${i}`}
                  className="group relative aspect-[3/4] overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={p.url} alt={p.file.name} className="h-full w-full object-cover" />
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      removeAt(i);
                    }}
                    className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-black/70 text-xs text-neutral-200 opacity-0 transition group-hover:opacity-100"
                    title="移除"
                  >
                    ✕
                  </button>
                  <span className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 py-0.5 text-[9px] text-neutral-300">
                    {p.file.name}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={run}
              disabled={picked.length === 0 || loading}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:opacity-40"
            >
              {loading
                ? "解析中…"
                : picked.length > 1
                  ? `解析 ${picked.length} 张`
                  : "解析截图"}
            </button>
            {picked.length > 0 && (
              <button
                onClick={clearAll}
                disabled={loading}
                className="rounded-lg border border-neutral-700 px-3 py-2 text-sm text-neutral-300 transition hover:bg-neutral-800 disabled:opacity-40"
              >
                清空
              </button>
            )}
            {picked.length > 0 && (
              <span className="ml-auto text-xs text-neutral-500">已选 {picked.length} 张</span>
            )}
          </div>

          {err && (
            <p className="mt-3 rounded-lg border border-red-800/50 bg-red-950/30 px-3 py-2 text-sm text-red-300">
              {err}
            </p>
          )}
        </section>

        {/* 结果区 */}
        <section className="mt-8 space-y-4">
          {loading && (
            <div className="flex items-center justify-center rounded-2xl border border-neutral-800 bg-neutral-900/40 py-10 text-sm text-neutral-500">
              <span className="animate-pulse">
                模型识别中，共 {picked.length} 张，通常每张几秒…
              </span>
            </div>
          )}

          {/* 本次硬失败（非图片 / 过大 / 服务端异常）——不入历史 */}
          {runErrors.map((item, i) => (
            <ResultCard key={`err-${item.filename}-${i}`} item={item} />
          ))}

          {/* 剥削分析（聚合全部历史的偏离标注 → 逐对手倾向 + LLM 建议） */}
          {history.length > 0 && (
            <ExploitPanel state={exploit} onRun={runExploit} disabled={loading} />
          )}

          {/* 历史记录（本地持久化，默认折叠，点开再渲染详情） */}
          {history.length > 0 && (
            <div className="flex items-center gap-2 pt-1">
              <h2 className="text-sm font-semibold text-neutral-300">
                历史记录
                <span className="ml-1.5 text-neutral-500">（{history.length}）</span>
              </h2>
              <button
                onClick={clearHistory}
                className="ml-auto rounded-lg border border-neutral-800 px-2.5 py-1 text-xs text-neutral-400 transition hover:border-red-800/60 hover:text-red-300"
              >
                清空历史
              </button>
            </div>
          )}

          {history.map((e) => (
            <HistoryItem key={e.id} entry={e} onDelete={() => deleteEntry(e.id)} />
          ))}

          {!loading && history.length === 0 && runErrors.length === 0 && picked.length === 0 && (
            <div className="flex items-center justify-center rounded-2xl border border-dashed border-neutral-800 bg-neutral-900/20 px-6 py-10 text-center text-sm text-neutral-600">
              解析结果会显示在这里：截图类型、玩家、公共牌、逐街动作、净额，以及重建校验。
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function fmtTime(ts?: number): string | null {
  if (!ts) return null;
  try {
    return new Date(ts).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return null;
  }
}

function statusBadge(item: IngestItem): { label: string; cls: string } {
  if (!item.ok) return { label: "失败", cls: "bg-red-500/15 text-red-300 ring-red-500/30" };
  if (item.recognized === false)
    return { label: "未识别", cls: "bg-amber-500/15 text-amber-300 ring-amber-500/30" };
  if (item.reconstruction) return RECON_STATUS[item.reconstruction.status] ?? RECON_STATUS.needs_user;
  const t = item.facts?.screenshot_type ?? "unknown";
  return { label: TYPE_LABEL[t] ?? t, cls: TYPE_STYLE[t] ?? TYPE_STYLE.unknown };
}

/** 历史条目：默认折叠，只显示轻量摘要；点开时才渲染完整详情（事实 + 重建）。 */
function HistoryItem({ entry, onDelete }: { entry: ImportEntry; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const { item, ts, thumb, filename } = entry;
  const badge = statusBadge(item);
  const time = fmtTime(ts);
  return (
    <div className="overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900/50">
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        className="flex cursor-pointer items-center gap-3 p-3 transition hover:bg-neutral-900/70"
      >
        <span
          className={`shrink-0 text-neutral-500 transition-transform ${open ? "rotate-90" : ""}`}
          aria-hidden
        >
          ▸
        </span>
        {thumb && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={thumb}
            alt={filename}
            className="h-10 w-10 shrink-0 rounded-md border border-neutral-800 object-cover"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-neutral-200">{filename}</div>
          {time && <div className="text-[11px] text-neutral-500">{time}</div>}
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${badge.cls}`}>
          {badge.label}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="shrink-0 rounded-lg border border-neutral-800 px-2 py-1 text-xs text-neutral-500 transition hover:border-red-800/60 hover:text-red-300"
          title="从历史中删除"
        >
          删除
        </button>
      </div>
      {open && (
        <div className="border-t border-neutral-800 p-4 sm:p-5">
          <ResultDetail item={item} />
        </div>
      )}
    </div>
  );
}

/** 结果详情主体（不含头部）：硬失败 / 未识别 / 已识别三种。 */
function ResultDetail({ item }: { item: IngestItem }) {
  if (!item.ok) {
    return <p className="text-sm text-red-300">{item.error ?? "解析失败"}</p>;
  }
  if (item.recognized === false) {
    return (
      <>
        <p className="text-sm leading-relaxed text-amber-200/90">
          {item.facts?.notes ||
            "这张图似乎不是微扑克手牌截图，或画面不清晰。请上传「手牌回放/详情」截图（底部有播放条、逐街动作与净额）。"}
        </p>
        {item.raw_model_output && (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-neutral-500 hover:text-neutral-300">
              查看模型原始输出
            </summary>
            <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-neutral-950 p-3 text-[11px] leading-relaxed text-neutral-400">
              {item.raw_model_output}
            </pre>
          </details>
        )}
      </>
    );
  }
  return (
    <>
      {item.facts && (
        <FactsView facts={item.facts} note={item.note ?? ""} raw={item.raw_model_output ?? ""} />
      )}
      {item.reconstruction && <ReconstructionView recon={item.reconstruction} />}
      {item.analysis && item.analysis.supported && <DeviationView analysis={item.analysis} />}
    </>
  );
}

function DeviationView({ analysis }: { analysis: Analysis }) {
  const { graded, mistakes } = analysis.counts;
  return (
    <div className="mt-5 border-t border-neutral-800 pt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] uppercase tracking-wider text-neutral-500">GTO 偏离标注</span>
        <span className="rounded-full bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300">
          已接地 {graded} · 偏离 {mistakes}
        </span>
      </div>
      <div className="space-y-2">
        {analysis.players.map((p, i) => (
          <div
            key={i}
            className={`rounded-lg border px-3 py-2 ${
              p.is_hero ? "border-emerald-600/40 bg-emerald-950/20" : "border-neutral-800 bg-neutral-900/40"
            }`}
          >
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-sm">
              <span className="font-medium text-neutral-100">{p.alias ?? "（未知）"}</span>
              {p.is_hero && (
                <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">我</span>
              )}
              {p.position && (
                <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-300">
                  {p.position}
                </span>
              )}
            </div>
            <div className="space-y-1.5">
              {p.deviations.map((d, j) => (
                <DeviationRow key={j} d={d} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-neutral-600">{analysis.note}</p>
    </div>
  );
}

function DeviationRow({ d }: { d: Deviation }) {
  if (d.spot === "postflop") {
    return (
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-neutral-400">
        <span className="rounded bg-neutral-800/80 px-1.5 py-0.5 text-[10px] text-neutral-400">
          {d.spot_label}
        </span>
        <span className="rounded bg-neutral-700/40 px-1.5 py-0.5 text-[10px] text-neutral-500">近似</span>
        <span className="text-neutral-400">{d.note}</span>
      </div>
    );
  }
  const gradeCls = GRADE_STYLE[d.grade ?? "mistake"] ?? GRADE_STYLE.mistake;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="rounded bg-neutral-800/80 px-1.5 py-0.5 text-[10px] text-neutral-300">
        {d.spot_label}
      </span>
      {d.hand_class && (
        <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] font-medium text-neutral-200">
          {d.hand_class}
        </span>
      )}
      <span className="text-neutral-400">
        选 <span className="text-neutral-200">{d.actual_label}</span>
      </span>
      <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${gradeCls}`}>
        {d.grade_label}
      </span>
      {d.grade === "mistake" && d.optimal_label && (
        <span className="text-neutral-400">
          应 <span className="text-emerald-300">{d.optimal_label}</span>
        </span>
      )}
      {d.deviation_label && (
        <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
          {d.deviation_label}
        </span>
      )}
      {typeof d.ev_loss_proxy === "number" && d.ev_loss_proxy > 0 && (
        <span className="text-[10px] text-neutral-500" title="频率差近似，非真实 EV">
          损失≈{Math.round(d.ev_loss_proxy * 100)}%
        </span>
      )}
      {d.note && <span className="w-full text-[10px] leading-relaxed text-neutral-500">{d.note}</span>}
    </div>
  );
}

const LEAK_LABEL: Record<string, string> = {
  too_tight: "过紧",
  too_loose: "过松",
  too_passive: "太被动",
  too_aggressive: "太激进",
  line_error: "线路偏差",
};

function toExploitDecisions(devs: Deviation[]) {
  return devs
    .filter((d) => d.grounded && (d.spot === "RFI" || d.spot === "vs_RFI"))
    .map((d) => ({
      street: d.street,
      spot: d.spot,
      spot_label: d.spot_label,
      hand_class: d.hand_class,
      actual: d.actual ?? "",
      optimal_action: d.optimal_action,
      grade: d.grade ?? "",
      deviation_type: d.deviation_type ?? null,
      grounded: true,
    }));
}

/** 把全部历史里的偏离标注按对手 alias 聚合成剥削请求（英雄单独聚合）。 */
function buildExploitInputs(history: ImportEntry[]): {
  opponents: ExploitProfileInput[];
  hero: ExploitProfileInput | null;
  groundedTotal: number;
} {
  const oppMap = new Map<string, ExploitProfileInput>();
  let hero: ExploitProfileInput | null = null;
  for (const e of history) {
    const players = e.item?.analysis?.players;
    if (!players) continue;
    for (const pl of players) {
      const decs = toExploitDecisions(pl.deviations);
      if (pl.is_hero) {
        if (!hero) hero = { alias: pl.alias || "我", hands: 0, net: 0, decisions: [] };
        hero.hands += 1;
        if (typeof pl.net === "number") hero.net = (hero.net ?? 0) + pl.net;
        hero.decisions.push(...decs);
      } else {
        const alias = (pl.alias || "").trim();
        if (!alias) continue;
        let prof = oppMap.get(alias);
        if (!prof) {
          prof = { alias, hands: 0, net: 0, decisions: [] };
          oppMap.set(alias, prof);
        }
        prof.hands += 1;
        if (typeof pl.net === "number") prof.net = (prof.net ?? 0) + pl.net;
        prof.decisions.push(...decs);
      }
    }
  }
  const opponents = Array.from(oppMap.values()).filter((o) => o.decisions.length > 0);
  const heroFinal = hero && hero.decisions.length > 0 ? hero : null;
  const groundedTotal =
    opponents.reduce((s, o) => s + o.decisions.length, 0) + (heroFinal ? heroFinal.decisions.length : 0);
  return { opponents, hero: heroFinal, groundedTotal };
}

function ExploitPanel({
  state,
  onRun,
  disabled,
}: {
  state: { loading: boolean; data: ExploitResult | null; error: string | null };
  onRun: () => void;
  disabled?: boolean;
}) {
  const { loading, data, error } = state;
  return (
    <div className="rounded-2xl border border-fuchsia-800/40 bg-gradient-to-b from-fuchsia-950/20 to-neutral-900/40 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold text-neutral-100">🎯 剥削分析</h2>
        <span className="rounded-full bg-fuchsia-900/50 px-2 py-0.5 text-[11px] text-fuchsia-200">
          逐对手 · 基于偏离标注
        </span>
        <button
          onClick={onRun}
          disabled={loading || disabled}
          className="ml-auto rounded-lg bg-fuchsia-500 px-3.5 py-1.5 text-sm font-semibold text-fuchsia-950 transition hover:bg-fuchsia-400 disabled:opacity-40"
        >
          {loading ? "分析中…" : data ? "重新生成" : "生成剥削分析"}
        </button>
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-neutral-400">
        汇总你导入过的所有手牌里、每个对手（及你自己）相对 GTO 的偏离，给出针对性剥削建议。
        统计仅含底牌可见、可接地的翻前决策；样本天然偏向摊牌手，小样本仅供倾向参考。
      </p>

      {error && (
        <p className="mt-3 rounded-lg border border-amber-800/50 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          {error}
        </p>
      )}

      {data && (
        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {data.hero && <ProfileCard s={data.hero} isHero />}
            {data.profiles
              .slice()
              .sort((a, b) => b.sample - a.sample)
              .map((s) => (
                <ProfileCard key={s.alias} s={s} />
              ))}
          </div>
          <div className="rounded-xl border border-neutral-800 bg-neutral-950/50 p-4">
            <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fuchsia-300/80">
              剥削建议（AI · 接地于上方统计）
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-neutral-200">{data.report}</p>
          </div>
          <p className="text-[11px] leading-relaxed text-neutral-600">{data.note}</p>
        </div>
      )}
    </div>
  );
}

function ProfileCard({
  s,
  isHero,
}: {
  s: ExploitResult["profiles"][number];
  isHero?: boolean;
}) {
  const leaks = Object.entries(s.leaks || {}).sort((a, b) => b[1] - a[1]);
  return (
    <div
      className={`rounded-xl border px-3.5 py-3 ${
        isHero ? "border-emerald-600/40 bg-emerald-950/20" : "border-neutral-800 bg-neutral-900/50"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-sm font-semibold text-neutral-100">{isHero ? "我" : s.alias}</span>
        <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-400">
          样本 {s.sample}
        </span>
        {typeof s.net === "number" && s.net !== 0 && (
          <span
            className={`ml-auto text-xs font-semibold ${
              s.net > 0 ? "text-emerald-400" : "text-red-400"
            }`}
          >
            净 {s.net > 0 ? "+" : ""}
            {s.net}
          </span>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-neutral-400">
        {s.accuracy != null && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">
            合规率 {Math.round(s.accuracy * 100)}%（偏离 {s.mistakes}）
          </span>
        )}
        {s.defend_count > 0 && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">
            防守 {s.defend_count}·弃 {s.fold_vs_open}·3bet {s.threebet_count}
          </span>
        )}
        {s.rfi_count > 0 && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">开池 {s.rfi_count}</span>
        )}
      </div>
      {leaks.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {leaks.map(([k, v]) => (
            <span key={k} className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
              {LEAK_LABEL[k] ?? k}×{v}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function CardHeader({
  item,
  ts,
  thumb,
  onDelete,
  extra,
}: {
  item: IngestItem;
  ts?: number;
  thumb?: string | null;
  onDelete?: () => void;
  extra?: React.ReactNode;
}) {
  const time = fmtTime(ts);
  return (
    <div className="mb-3 flex items-center gap-2">
      {thumb && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={thumb}
          alt={item.filename}
          className="h-9 w-9 shrink-0 rounded-md border border-neutral-800 object-cover"
        />
      )}
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-neutral-200">{item.filename}</div>
        {time && <div className="text-[11px] text-neutral-500">{time}</div>}
      </div>
      {extra}
      {onDelete && (
        <button
          onClick={onDelete}
          className="ml-auto shrink-0 rounded-lg border border-neutral-800 px-2 py-1 text-xs text-neutral-500 transition hover:border-red-800/60 hover:text-red-300"
          title="从历史中删除"
        >
          删除
        </button>
      )}
    </div>
  );
}

function ResultCard({
  item,
  ts,
  thumb,
  onDelete,
}: {
  item: IngestItem;
  ts?: number;
  thumb?: string | null;
  onDelete?: () => void;
}) {
  // 单图硬失败（非图片 / 过大 / 后端异常）
  if (!item.ok) {
    return (
      <div className="rounded-2xl border border-red-900/50 bg-red-950/20 p-4">
        <div className="mb-1 text-sm font-semibold text-neutral-200">{item.filename}</div>
        <p className="text-sm text-red-300">{item.error ?? "解析失败"}</p>
      </div>
    );
  }

  // 已解析但不是手牌截图 → 友好提示（不报错）
  if (item.recognized === false) {
    return (
      <div className="rounded-2xl border border-amber-800/50 bg-amber-950/20 p-4">
        <CardHeader
          item={item}
          ts={ts}
          thumb={thumb}
          onDelete={onDelete}
          extra={
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300 ring-1 ring-amber-500/30">
              未识别为手牌截图
            </span>
          }
        />
        <p className="text-sm leading-relaxed text-amber-200/90">
          {item.facts?.notes ||
            "这张图似乎不是微扑克手牌截图，或画面不清晰。请上传「手牌回放/详情」截图（底部有播放条、逐街动作与净额）。"}
        </p>
        {item.raw_model_output && (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-neutral-500 hover:text-neutral-300">
              查看模型原始输出
            </summary>
            <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-neutral-950 p-3 text-[11px] leading-relaxed text-neutral-400">
              {item.raw_model_output}
            </pre>
          </details>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-neutral-800 bg-neutral-900/50 p-5">
      <CardHeader item={item} ts={ts} thumb={thumb} onDelete={onDelete} />
      {item.facts && (
        <FactsView facts={item.facts} note={item.note ?? ""} raw={item.raw_model_output ?? ""} />
      )}
      {item.reconstruction && <ReconstructionView recon={item.reconstruction} />}
    </div>
  );
}

function FactsView({
  facts,
  note,
  raw,
}: {
  facts: ObservationFacts;
  note: string;
  raw: string;
}) {
  const conf = Math.round((facts.extraction_confidence || 0) * 100);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${
            TYPE_STYLE[facts.screenshot_type] ?? TYPE_STYLE.unknown
          }`}
        >
          {TYPE_LABEL[facts.screenshot_type] ?? facts.screenshot_type}
        </span>
        {facts.blinds && (
          <span className="rounded-md bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300">
            盲注 {facts.blinds}
          </span>
        )}
        {facts.pot != null && (
          <span className="rounded-md bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300">
            底池 {facts.pot}
          </span>
        )}
        {facts.hand_id && (
          <span className="rounded-md bg-neutral-800 px-2 py-0.5 text-xs text-neutral-500">
            #{facts.hand_id}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 text-xs text-neutral-500">
          置信度
          <span className="inline-block h-1.5 w-16 overflow-hidden rounded-full bg-neutral-800 align-middle">
            <span className="block h-full bg-emerald-500" style={{ width: `${conf}%` }} />
          </span>
          {conf}%
        </span>
      </div>

      {facts.board.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-neutral-500">
            公共牌
          </div>
          <div className="flex gap-1.5">
            {facts.board.map((c) => (
              <PlayingCard key={c} card={c} size="sm" />
            ))}
          </div>
        </div>
      )}

      <div className="mt-4">
        <div className="mb-1.5 text-[11px] uppercase tracking-wider text-neutral-500">
          玩家（{facts.players.length}）
        </div>
        <div className="space-y-2">
          {facts.players.map((p, i) => (
            <PlayerRow key={i} p={p} />
          ))}
          {facts.players.length === 0 && (
            <p className="text-sm text-neutral-600">未识别到玩家行。</p>
          )}
        </div>
      </div>

      {facts.notes && (
        <p className="mt-4 rounded-lg bg-neutral-800/50 px-3 py-2 text-xs text-neutral-400">
          备注：{facts.notes}
        </p>
      )}

      {note && <p className="mt-3 text-[11px] leading-relaxed text-neutral-600">{note}</p>}

      {raw && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-neutral-500 hover:text-neutral-300">
            查看模型原始输出
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-neutral-950 p-3 text-[11px] leading-relaxed text-neutral-400">
            {raw}
          </pre>
        </details>
      )}
    </div>
  );
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs ${
        ok ? "bg-emerald-500/10 text-emerald-300" : "bg-red-500/10 text-red-300"
      }`}
    >
      <span>{ok ? "✓" : "✗"}</span>
      {label}
    </span>
  );
}

function ReconstructionView({ recon }: { recon: Reconstruction }) {
  const st = RECON_STATUS[recon.status] ?? RECON_STATUS.needs_user;
  const c = recon.checks;
  return (
    <div className="mt-5 border-t border-neutral-800 pt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] uppercase tracking-wider text-neutral-500">下注序列重建</span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${st.cls}`}>
          {st.label}
        </span>
        <span className="text-xs text-neutral-500">
          置信度 {Math.round(recon.confidence * 100)}%
        </span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Check
          ok={c.net_ok}
          label={`净额守恒${c.net_sum != null ? `（Σ=${c.net_sum}）` : ""}`}
        />
        <Check
          ok={c.rows_consistent}
          label={
            c.rows_consistent
              ? "动作与净额一致"
              : `${c.uncertain_count} 行动作与净额对不上（可能未完整识别）`
          }
        />
      </div>

      <div className="space-y-1.5">
        {recon.players.map((p, i) => (
          <div
            key={i}
            className={`rounded-lg border px-3 py-2 text-sm ${
              p.is_hero
                ? "border-emerald-600/40 bg-emerald-950/20"
                : "border-neutral-800 bg-neutral-900/40"
            }`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-neutral-100">{p.alias ?? "（未知）"}</span>
              {p.is_hero && (
                <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">
                  我
                </span>
              )}
              {p.is_winner && (
                <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-300">
                  赢家
                </span>
              )}
              {p.position && (
                <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-300">
                  {p.position}
                </span>
              )}
              {p.uncertain && (
                <span
                  className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300 ring-1 ring-amber-500/30"
                  title="逐街动作金额之和与净额对不上，可能有动作未被识别"
                >
                  动作待复核
                </span>
              )}
              <span className="ml-auto text-xs text-neutral-500">
                投入 {p.invested}
                {p.net != null && (
                  <span
                    className={`ml-2 font-semibold ${
                      p.net > 0 ? "text-emerald-400" : p.net < 0 ? "text-red-400" : "text-neutral-400"
                    }`}
                  >
                    净 {p.net > 0 ? "+" : ""}
                    {p.net}
                  </span>
                )}
              </span>
            </div>
            {p.actions.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {p.actions.map((a, j) => (
                  <span
                    key={j}
                    className="rounded bg-neutral-800/80 px-1.5 py-0.5 text-[11px] text-neutral-300"
                  >
                    {a.street && <span className="text-neutral-500">{a.street}·</span>}
                    {a.label}
                    {a.amount != null ? ` ${a.amount}` : ""}
                  </span>
                ))}
              </div>
            )}
            {p.uncertain && (
              <p className="mt-1.5 text-[11px] leading-relaxed text-amber-300/80">
                逐街动作之和 {p.parsed_invested}，按净额应约 {p.invested}——可能有一街动作未被识别，已按净额校正投入。
              </p>
            )}
          </div>
        ))}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-neutral-600">{recon.note}</p>
    </div>
  );
}

function PlayerRow({ p }: { p: IngestPlayerObs }) {
  const net = p.net;
  return (
    <div
      className={`rounded-xl border px-3 py-2.5 ${
        p.is_hero
          ? "border-emerald-600/50 bg-emerald-950/20"
          : "border-neutral-800 bg-neutral-900/40"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="truncate font-semibold text-neutral-100">
          {p.alias ?? "（未知）"}
        </span>
        {p.is_hero && (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
            我
          </span>
        )}
        {p.position && (
          <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-300">
            {p.position}
          </span>
        )}
        {p.hole_cards.length > 0 && (
          <span className="flex gap-1">
            {p.hole_cards.map((c) => (
              <PlayingCard key={c} card={c} size="sm" />
            ))}
          </span>
        )}
        {p.made_hand && (
          <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-amber-300">
            {p.made_hand}
          </span>
        )}
        {net != null && (
          <span
            className={`ml-auto text-sm font-semibold ${
              net > 0 ? "text-emerald-400" : net < 0 ? "text-red-400" : "text-neutral-400"
            }`}
          >
            {net > 0 ? "+" : ""}
            {net}
          </span>
        )}
      </div>
      {p.actions_raw && <p className="mt-1 text-xs text-neutral-400">{p.actions_raw}</p>}
    </div>
  );
}
