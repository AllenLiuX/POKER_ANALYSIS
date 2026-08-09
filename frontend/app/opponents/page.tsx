"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Search, Sparkles, Users } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import Markdown from "@/components/Markdown";
import { postOpponentReport } from "@/lib/api";
import { loadImportHistory, mergeImportEntries } from "@/lib/importHistory";
import {
  cloudReady,
  fetchCloudImports,
  fetchCloudOppNotes,
  fetchOpponentAggregates,
  fetchOpponentReports,
  syncLocalImportsToCloud,
  syncLocalOppNotesToCloud,
  upsertOpponentNote,
  upsertOpponentReport,
  type OpponentReportRow,
} from "@/lib/cloud";
import {
  buildOpponentProfiles,
  deriveCloudProfile,
  loadOppNotes,
  mergeOppNotes,
  OPP_TAGS,
  saveOppNote,
  syncEntryToProfiles,
  type CloudProfile,
  type OppNotes,
  type OpponentProfile,
  type StatCell,
} from "@/lib/opponents";

const LEAK_LABEL: Record<string, string> = {
  too_tight: "过紧", too_loose: "过松", too_passive: "太被动",
  too_aggressive: "太激进", line_error: "线路偏差",
};

const ARCHETYPE_STYLE: Record<string, string> = {
  "跟注站 · 过松": "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  "过紧 · 怕事": "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  被动: "bg-neutral-700/40 text-neutral-300 ring-neutral-600/40",
  "激进 · 爱诈唬": "bg-red-500/15 text-red-300 ring-red-500/30",
  "线路混乱": "bg-fuchsia-500/15 text-fuchsia-300 ring-fuchsia-500/30",
  "翻牌易弃 · 可多偷": "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  "爱看摊牌 · 黏": "bg-violet-500/15 text-violet-300 ring-violet-500/30",
};
const archCls = (a: string) => ARCHETYPE_STYLE[a] ?? "bg-neutral-700/40 text-neutral-300 ring-neutral-600/40";

// 报告显著性门控：样本较上次生成增长超过该手数才提示"可更新"
const REPORT_STALE_AFTER = 8;

export default function OpponentsPage() {
  const [cloudProfiles, setCloudProfiles] = useState<CloudProfile[]>([]);
  const [localProfiles, setLocalProfiles] = useState<OpponentProfile[]>([]);
  const [reports, setReports] = useState<Record<string, OpponentReportRow>>({});
  const [notes, setNotes] = useState<OppNotes>({});
  const [query, setQuery] = useState("");
  const [isCloud, setIsCloud] = useState(false);
  const [backfilling, setBackfilling] = useState<{ done: number; total: number } | null>(null);

  const loadCloud = useCallback(async () => {
    const [aggs, reps] = await Promise.all([fetchOpponentAggregates(), fetchOpponentReports()]);
    setReports(reps);
    setCloudProfiles(
      aggs.map(deriveCloudProfile).sort((a, b) => b.hands - a.hands || b.sample - a.sample),
    );
  }, []);

  useEffect(() => {
    setLocalProfiles(buildOpponentProfiles(loadImportHistory()));
    setNotes(loadOppNotes());
    (async () => {
      // 备注：本地 ⇄ 云端合并
      try {
        await syncLocalOppNotesToCloud(loadOppNotes());
        const cloudNotes = await fetchCloudOppNotes();
        if (Object.keys(cloudNotes).length) setNotes(mergeOppNotes(cloudNotes));
      } catch {
        /* ignore */
      }
      // 导入历史：本地 ⇄ 云端合并（供本地回退 + 回填）
      try {
        await syncLocalImportsToCloud(loadImportHistory());
        const cloud = await fetchCloudImports();
        const merged = cloud.length ? mergeImportEntries(cloud) : loadImportHistory();
        setLocalProfiles(buildOpponentProfiles(merged));
      } catch {
        /* ignore */
      }
      // 对手画像：登录则用服务端权威聚合
      try {
        if (await cloudReady()) {
          setIsCloud(true);
          await loadCloud();
        }
      } catch {
        /* ignore */
      }
    })();
  }, [loadCloud]);

  // 把导入历史（含 Phase A 之前的）回填进云端聚合：逐条幂等 apply。
  const backfill = useCallback(async () => {
    const hist = mergeImportEntries(await fetchCloudImports().catch(() => []));
    const usable = hist.filter((e) => e.item?.ok && e.item?.recognized !== false);
    if (usable.length === 0) return;
    setBackfilling({ done: 0, total: usable.length });
    for (let i = 0; i < usable.length; i++) {
      await syncEntryToProfiles(usable[i]).catch(() => {});
      setBackfilling({ done: i + 1, total: usable.length });
    }
    setBackfilling(null);
    await loadCloud();
  }, [loadCloud]);

  const onSaveNote = (alias: string, patch: { note?: string; tag?: string }) => {
    const next = saveOppNote(alias, patch);
    setNotes({ ...next });
    upsertOpponentNote(alias, next[alias]).catch(() => {});
  };

  const genReport = useCallback(
    async (p: CloudProfile) => {
      const res = await postOpponentReport({
        alias: p.alias,
        hands: p.hands,
        net: p.net,
        counters: p.counters,
        tag: notes[p.alias]?.tag ?? null,
        note: notes[p.alias]?.note ?? null,
      });
      const row: OpponentReportRow = {
        opponentId: p.opponentId,
        report: res.report,
        model: "gpt-4o",
        basedOnHandCount: p.hands,
        createdAt: new Date().toISOString(),
      };
      setReports((r) => ({ ...r, [p.opponentId]: row }));
      upsertOpponentReport(p.opponentId, res.report, "gpt-4o", p.hands, p.counters).catch(() => {});
    },
    [notes],
  );

  const shownCloud = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? cloudProfiles.filter((p) => p.alias.toLowerCase().includes(q)) : cloudProfiles;
  }, [cloudProfiles, query]);
  const shownLocal = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? localProfiles.filter((p) => p.alias.toLowerCase().includes(q)) : localProfiles;
  }, [localProfiles, query]);

  const total = isCloud && cloudProfiles.length ? cloudProfiles.length : localProfiles.length;
  const canBackfill = isCloud && localProfiles.length > 0;

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[420px] bg-gradient-to-b from-fuchsia-500/10 via-fuchsia-500/[0.02] to-transparent"
      />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <header className="mb-6 mt-2">
          <div className="mb-2.5 flex items-center gap-2">
            <Badge variant="accent" size="sm">Beta</Badge>
            {isCloud ? (
              <Badge variant="success" size="sm">云端聚合</Badge>
            ) : (
              <Badge variant="neutral" size="sm">本地（登录后云端累计）</Badge>
            )}
          </div>
          <h1 className="flex items-center gap-2.5 text-3xl font-black tracking-tight sm:text-4xl">
            <span className="flex size-9 items-center justify-center rounded-xl bg-fuchsia-500/15 text-fuchsia-300 ring-1 ring-fuchsia-500/25">
              <Users className="size-5" />
            </span>
            对手{" "}
            <span className="bg-gradient-to-r from-fuchsia-300 to-pink-300 bg-clip-text text-transparent">
              档案
            </span>
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-neutral-400">
            每导入一手，引擎就把逐对手的可加计数器增量并入云端权威聚合（幂等、跨设备累计）。
            频率经小样本贝叶斯收缩后展示；样本天然偏向摊牌/关键手，仅供剥削倾向参考。
          </p>
        </header>

        {total > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <div className="relative w-full max-w-xs">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-neutral-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索对手昵称…"
                className="w-full rounded-lg border border-white/10 bg-neutral-900/60 py-1.5 pl-8 pr-3 text-sm text-neutral-100 focus:border-fuchsia-600 focus:outline-none"
              />
            </div>
            <span className="text-xs text-neutral-500">
              {(isCloud && cloudProfiles.length ? shownCloud.length : shownLocal.length)}/{total} 位对手
            </span>
            {canBackfill && (
              <Button
                onClick={backfill}
                disabled={!!backfilling}
                variant="secondary"
                size="sm"
                className="ml-auto"
                title="把导入历史逐手并入云端聚合（幂等，可反复点）"
              >
                <RefreshCw className={backfilling ? "animate-spin" : ""} />
                {backfilling ? `同步中 ${backfilling.done}/${backfilling.total}` : "同步历史到云端画像"}
              </Button>
            )}
          </div>
        )}

        {isCloud && cloudProfiles.length === 0 && localProfiles.length > 0 && (
          <div className="mb-4 rounded-2xl border border-fuchsia-800/40 bg-fuchsia-950/20 p-4 text-sm text-fuchsia-200/90">
            检测到 {localProfiles.length} 位对手来自更早的导入但还没进云端聚合。点上方「同步历史到云端画像」即可一次性并入（幂等）。
          </div>
        )}

        {total === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-neutral-800 bg-neutral-900/20 px-6 py-12 text-center text-sm text-neutral-500">
            还没有对手档案。先到
            <Link href="/import" className="text-fuchsia-300 underline underline-offset-2">
              截图导入
            </Link>
            里上传几手回放截图，这里就会自动累积每个对手的画像。
          </div>
        ) : isCloud && cloudProfiles.length ? (
          <div className="grid grid-cols-1 gap-3">
            {shownCloud.map((p) => (
              <CloudProfileCard
                key={p.opponentId}
                p={p}
                report={reports[p.opponentId]}
                note={notes[p.alias]?.note ?? ""}
                tag={notes[p.alias]?.tag ?? ""}
                onSave={(patch) => onSaveNote(p.alias, patch)}
                onGenReport={() => genReport(p)}
              />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {shownLocal.map((p) => (
              <LocalProfileCard
                key={p.alias}
                p={p}
                note={notes[p.alias]?.note ?? ""}
                tag={notes[p.alias]?.tag ?? ""}
                onSave={(patch) => onSaveNote(p.alias, patch)}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

// ---------- 展示：单个统计格（收缩频率 + 样本量）----------
function Stat({ label, cell, tone }: { label: string; cell: StatCell; tone?: "warn" | "good" }) {
  if (cell.n <= 0) return null;
  const pct = Math.round((cell.shrunk ?? 0) * 100);
  const small = cell.n < 8;
  const toneCls = tone === "warn" ? "text-amber-300" : tone === "good" ? "text-emerald-300" : "text-neutral-200";
  return (
    <div
      className="rounded-lg border border-white/[0.06] bg-neutral-950/50 px-2 py-1.5"
      title={`原始 ${cell.k}/${cell.n} = ${cell.pct != null ? Math.round(cell.pct * 100) : "—"}%，收缩后 ${pct}%`}
    >
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className={`text-sm font-semibold ${toneCls}`}>{pct}%</span>
        <span className="text-[10px] text-neutral-600">n{cell.n}{small ? "·小" : ""}</span>
      </div>
    </div>
  );
}

function CloudProfileCard({
  p,
  report,
  note,
  tag,
  onSave,
  onGenReport,
}: {
  p: CloudProfile;
  report?: OpponentReportRow;
  note: string;
  tag: string;
  onSave: (patch: { note?: string; tag?: string }) => void;
  onGenReport: () => Promise<void>;
}) {
  const [gen, setGen] = useState<{ loading: boolean; error: string | null }>({ loading: false, error: null });
  const leaks = Object.entries(p.leaks).sort((a, b) => b[1] - a[1]);
  const stale = !report || p.hands - report.basedOnHandCount >= REPORT_STALE_AFTER;
  const preAcc = p.gradedPre.n > 0 ? Math.round(((p.gradedPre.n - p.gradedPre.mistakes) / p.gradedPre.n) * 100) : null;

  const runGen = async () => {
    setGen({ loading: true, error: null });
    try {
      await onGenReport();
      setGen({ loading: false, error: null });
    } catch (e) {
      setGen({ loading: false, error: String(e instanceof Error ? e.message : e) });
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2">
        <span className="truncate text-base font-bold text-neutral-100">{p.alias}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${archCls(p.archetype)}`}>
          {p.archetype}
        </span>
        <span className="ml-auto text-xs text-neutral-500">{p.hands} 手</span>
        {p.net !== 0 && (
          <span className={`text-sm font-semibold ${p.net > 0 ? "text-emerald-400" : "text-red-400"}`}>
            净 {p.net > 0 ? "+" : ""}
            {Math.round(p.net)}
          </span>
        )}
      </div>

      {/* HUD 统计格 */}
      <div className="mt-3 grid grid-cols-3 gap-1.5 sm:grid-cols-4">
        <Stat label="面对开池·弃" cell={p.vsOpen.fold} tone={p.vsOpen.fold.shrunk != null && p.vsOpen.fold.shrunk > 0.65 ? "warn" : undefined} />
        <Stat label="面对开池·跟" cell={p.vsOpen.call} />
        <Stat label="面对开池·3B" cell={p.vsOpen.threebet} />
        <Stat label="首入池开池" cell={p.pfOpen} />
        <Stat label="翻后激进" cell={p.afPost} tone={p.afPost.shrunk != null && p.afPost.shrunk > 0.6 ? "warn" : undefined} />
        <Stat label="翻牌c-bet" cell={p.cbet} />
        <Stat label="弃vs c-bet" cell={p.foldVsCbet} tone={p.foldVsCbet.shrunk != null && p.foldVsCbet.shrunk > 0.6 ? "warn" : undefined} />
        <Stat label="看摊牌" cell={p.wtsd} />
      </div>

      {(leaks.length > 0 || preAcc != null) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {preAcc != null && (
            <span className="rounded bg-neutral-800/70 px-1.5 py-0.5 text-[10px] text-neutral-300">
              翻前接地合规 {preAcc}%（{p.gradedPre.n} 决策）
            </span>
          )}
          {leaks.map(([k, v]) => (
            <span key={k} className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
              {LEAK_LABEL[k] ?? k}×{v}
            </span>
          ))}
        </div>
      )}

      {/* 剥削报告 */}
      <div className="mt-3 rounded-xl border border-fuchsia-800/30 bg-fuchsia-950/10 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-fuchsia-200">
            <Sparkles className="size-3.5" />
            剥削报告
          </span>
          {report && (
            <span className="text-[10px] text-neutral-500">基于 {report.basedOnHandCount} 手</span>
          )}
          <Button
            onClick={runGen}
            disabled={gen.loading}
            variant="accent"
            size="sm"
            className="ml-auto"
          >
            {gen.loading ? "生成中…" : report ? (stale ? "更新报告" : "重新生成") : "生成报告"}
          </Button>
        </div>
        {report && stale && (
          <p className="mt-1 text-[10px] text-amber-300/80">已新增 {p.hands - report.basedOnHandCount} 手，可更新报告。</p>
        )}
        {gen.error && <p className="mt-1 text-[11px] text-red-300">{gen.error}</p>}
        {report && (
          <div className="mt-2 border-t border-white/[0.06] pt-2 text-sm">
            <Markdown>{report.report}</Markdown>
          </div>
        )}
      </div>

      <NotesEditor note={note} tag={tag} onSave={onSave} />
    </Card>
  );
}

function LocalProfileCard({
  p,
  note,
  tag,
  onSave,
}: {
  p: OpponentProfile;
  note: string;
  tag: string;
  onSave: (patch: { note?: string; tag?: string }) => void;
}) {
  const leaks = Object.entries(p.leaks).sort((a, b) => b[1] - a[1]);
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2">
        <span className="truncate text-base font-bold text-neutral-100">{p.alias}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${archCls(p.archetype)}`}>
          {p.archetype}
        </span>
        {p.net !== 0 && (
          <span className={`ml-auto text-sm font-semibold ${p.net > 0 ? "text-emerald-400" : "text-red-400"}`}>
            净 {p.net > 0 ? "+" : ""}
            {p.net}
          </span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-neutral-400">
        <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">{p.hands} 手</span>
        <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">翻前样本 {p.sample}</span>
        {p.accuracy != null && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">
            合规率 {Math.round(p.accuracy * 100)}%（偏离 {p.mistakes}）
          </span>
        )}
        {p.defend > 0 && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">
            防守 {p.defend}·弃 {p.foldVsOpen}·3bet {p.threebet}
          </span>
        )}
        {p.postflopSample > 0 && (
          <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-300">
            翻后 {p.postflopSample}·偏离 {p.postflopMistakes}
          </span>
        )}
      </div>
      {leaks.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {leaks.map(([k, v]) => (
            <span key={k} className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
              {LEAK_LABEL[k] ?? k}×{v}
            </span>
          ))}
        </div>
      )}
      <NotesEditor note={note} tag={tag} onSave={onSave} />
    </Card>
  );
}

function NotesEditor({
  note,
  tag,
  onSave,
}: {
  note: string;
  tag: string;
  onSave: (patch: { note?: string; tag?: string }) => void;
}) {
  const [draft, setDraft] = useState(note);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    setDraft(note);
    setDirty(false);
  }, [note]);

  return (
    <div className="mt-3 border-t border-white/[0.07] pt-3">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-[11px] text-neutral-500">标签</span>
        <select
          value={tag}
          onChange={(e) => onSave({ tag: e.target.value })}
          className="rounded-md border border-white/10 bg-neutral-950 px-2 py-1 text-xs text-neutral-100 focus:border-fuchsia-600 focus:outline-none"
        >
          {OPP_TAGS.map((t) => (
            <option key={t} value={t}>
              {t || "无"}
            </option>
          ))}
        </select>
      </div>
      <textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(e.target.value !== note);
        }}
        rows={2}
        placeholder="备注：打法、读牌、剥削计划…"
        className="w-full resize-y rounded-md border border-white/10 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-100 focus:border-fuchsia-600 focus:outline-none"
      />
      {dirty && (
        <div className="mt-1.5 flex items-center gap-2">
          <Button onClick={() => { onSave({ note: draft }); setDirty(false); }} variant="accent" size="sm">
            保存备注
          </Button>
          <Button onClick={() => { setDraft(note); setDirty(false); }} variant="secondary" size="sm">
            取消
          </Button>
        </div>
      )}
    </div>
  );
}
