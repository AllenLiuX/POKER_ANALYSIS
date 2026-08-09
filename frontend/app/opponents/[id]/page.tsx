"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Info, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import Markdown from "@/components/Markdown";
import ReportSources from "@/components/ReportSources";
import { CHART_COLORS, ChartCard, TendencyRadar, TrendArea } from "@/components/charts/Charts";
import { postOpponentReport } from "@/lib/api";
import { loadImportHistory, mergeImportEntries, type ImportEntry } from "@/lib/importHistory";
import {
  fetchCloudImports,
  fetchOpponentAggregates,
  fetchOpponentReports,
  upsertOpponentNote,
  upsertOpponentReport,
  type OpponentReportRow,
} from "@/lib/cloud";
import {
  buildLocalCloudProfile,
  deriveCloudProfile,
  effectiveTag,
  freqRows,
  loadOppNotes,
  OPP_TAGS,
  radarStats,
  saveOppNote,
  type CloudProfile,
  type FreqRow,
} from "@/lib/opponents";

const LEAK_LABEL: Record<string, string> = {
  too_tight: "过紧", too_loose: "过松", too_passive: "太被动",
  too_aggressive: "太激进", line_error: "线路偏差",
};
// 重建/偏离里的 street 已是中文街道标签（翻前/翻牌/转牌/河牌）。
const STREET_ORDER = ["翻前", "翻牌", "转牌", "河牌"];

interface HandRow {
  ts: number;
  board: string[];
  net: number | null;
  line: string;
  thumb: string | null;
}

export default function OpponentDetailPage() {
  const params = useParams<{ id: string }>();
  const rawId = decodeURIComponent(String(params?.id ?? ""));
  const aliasHint = rawId.startsWith("alias:") ? rawId.slice(6) : null;
  const [profile, setProfile] = useState<CloudProfile | null>(null);
  const [report, setReport] = useState<OpponentReportRow | undefined>();
  const [hands, setHands] = useState<HandRow[]>([]);
  const [note, setNote] = useState("");
  const [tag, setTag] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "notfound">("loading");
  const [gen, setGen] = useState<{ loading: boolean; error: string | null }>({ loading: false, error: null });

  useEffect(() => {
    (async () => {
      try {
        // 手牌历史（云端+本地合并 → 按手去重）——同时用于本地画像现算与相关手牌清单。
        const hist = dedupByHand(mergeImportEntries(await fetchCloudImports().catch(() => loadImportHistory())));
        const [aggs, reps] = await Promise.all([
          fetchOpponentAggregates().catch(() => []),
          fetchOpponentReports().catch(() => ({}) as Record<string, OpponentReportRow>),
        ]);
        // 优先云端权威聚合；无则用 alias 从导入历史现算本地画像。
        let prof: CloudProfile | null = null;
        if (!aliasHint) {
          const row = aggs.find((a) => a.opponentId === rawId);
          if (row) prof = deriveCloudProfile(row);
        }
        const alias = prof?.alias ?? aliasHint ?? rawId;
        if (!prof) prof = buildLocalCloudProfile(hist, alias);
        if (!prof) {
          setStatus("notfound");
          return;
        }
        setProfile(prof);
        setReport(reps[prof.opponentId]);
        const n = loadOppNotes()[prof.alias];
        setNote(n?.note ?? "");
        setTag(n?.tag ?? "");
        setHands(collectHands(hist, prof.alias));
        setStatus("ready");
      } catch {
        setStatus("notfound");
      }
    })();
  }, [rawId, aliasHint]);

  const onSaveNote = (patch: { note?: string; tag?: string }) => {
    if (!profile) return;
    const next = saveOppNote(profile.alias, patch);
    if (patch.note != null) setNote(patch.note);
    if (patch.tag != null) setTag(patch.tag);
    upsertOpponentNote(profile.alias, next[profile.alias]).catch(() => {});
  };

  const runGen = useCallback(async () => {
    if (!profile) return;
    setGen({ loading: true, error: null });
    try {
      const res = await postOpponentReport({
        alias: profile.alias,
        hands: profile.hands,
        net: profile.net,
        counters: profile.counters,
        tag: tag || null,
        note: note || null,
      });
      const row: OpponentReportRow = {
        opponentId: profile.opponentId,
        report: res.report,
        model: "gpt-4o",
        basedOnHandCount: profile.hands,
        createdAt: new Date().toISOString(),
        sources: res.sources,
      };
      setReport(row);
      upsertOpponentReport(profile.opponentId, res.report, "gpt-4o", profile.hands, profile.counters, res.sources).catch(() => {});
      setGen({ loading: false, error: null });
    } catch (e) {
      setGen({ loading: false, error: String(e instanceof Error ? e.message : e) });
    }
  }, [profile, tag, note]);

  const netTrend = useMemo(() => {
    let cum = 0;
    const asc = [...hands].filter((h) => h.net != null).sort((a, b) => a.ts - b.ts);
    return asc.map((h, i) => {
      cum += h.net ?? 0;
      return { x: String(i + 1), y: Math.round(cum) };
    });
  }, [hands]);

  if (status === "loading")
    return <main className="mx-auto max-w-5xl px-6 py-16 text-center text-sm text-neutral-500">加载对手画像…</main>;

  if (status === "notfound" || !profile)
    return (
      <main className="mx-auto max-w-3xl px-6 py-16 text-center">
        <p className="text-sm text-neutral-400">
          未找到该对手的云端画像。深度分析需要登录并已把导入历史同步到云端聚合。
        </p>
        <Link
          href="/opponents"
          className="mt-4 inline-flex items-center gap-1 text-sm text-fuchsia-300 hover:text-fuchsia-200"
        >
          <ArrowLeft className="size-4" /> 返回对手档案
        </Link>
      </main>
    );

  const et = effectiveTag(tag, profile);
  const radar = radarStats(profile);
  const rows = freqRows(profile);
  const leaks = Object.entries(profile.leaks).sort((a, b) => b[1] - a[1]);
  const preAcc = profile.gradedPre.n > 0 ? Math.round(((profile.gradedPre.n - profile.gradedPre.mistakes) / profile.gradedPre.n) * 100) : null;
  const postAcc = profile.gradedPost.n > 0 ? Math.round(((profile.gradedPost.n - profile.gradedPost.mistakes) / profile.gradedPost.n) * 100) : null;

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[360px] bg-gradient-to-b from-fuchsia-500/10 via-fuchsia-500/[0.02] to-transparent"
      />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Link
          href="/opponents"
          className="mb-4 inline-flex items-center gap-1 text-sm text-neutral-400 hover:text-neutral-200"
        >
          <ArrowLeft className="size-4" /> 对手档案
        </Link>

        {/* 头部 */}
        <header className="mb-5 flex flex-wrap items-center gap-3">
          <h1 className="text-3xl font-black tracking-tight text-neutral-100">{profile.alias}</h1>
          <Badge variant="accent" size="sm">{profile.archetype}</Badge>
          {et.tag && (
            <Badge variant="neutral" size="sm">
              {et.tag}{et.auto ? " · AI" : ""}
            </Badge>
          )}
          <div className="ml-auto flex items-center gap-4 text-sm">
            <span className="text-neutral-500">{profile.hands} 手</span>
            {profile.net !== 0 && (
              <span className={`font-semibold ${profile.net > 0 ? "text-emerald-400" : "text-red-400"}`}>
                净 {profile.net > 0 ? "+" : ""}{Math.round(profile.net)}
              </span>
            )}
          </div>
        </header>

        {/* KPI */}
        <section className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
          <Kpi label="观测手数" value={String(profile.hands)} />
          <Kpi label="翻前样本" value={String(profile.vsOpen.n)} />
          <Kpi label="翻前合规" value={preAcc != null ? `${preAcc}%` : "—"} sub={`${profile.gradedPre.n} 决策`} />
          <Kpi label="翻后合规" value={postAcc != null ? `${postAcc}%` : "—"} sub={`${profile.gradedPost.n} 决策`} />
          <Kpi label="看摊牌" value={cellPct(profile.wtsd.shrunk)} sub={`n${profile.wtsd.n}`} />
          <Kpi label="翻后激进" value={cellPct(profile.afPost.shrunk)} sub={`n${profile.afPost.n}`} />
        </section>

        {/* 雷达 + 频率条 */}
        <div className="mb-4 grid grid-cols-1 gap-3 lg:grid-cols-5">
          <ChartCard title="倾向雷达（相对群体基线）" className="lg:col-span-2">
            <TendencyRadar data={radar} height={264} />
            <p className="mt-1 flex items-center gap-1 text-[10px] text-neutral-600">
              <Info className="size-3" /> 外圈=高于基线；灰色为群体基线（50）。小样本已收缩。
            </p>
          </ChartCard>

          <ChartCard title="行动频率 vs 群体基线" className="lg:col-span-3">
            <div className="space-y-2.5">
              {rows.map((r) => (
                <FreqBar key={r.label} row={r} />
              ))}
            </div>
          </ChartCard>
        </div>

        {/* 净额趋势 + 漏洞 */}
        <div className="mb-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <ChartCard title="对手累计净额趋势" className="lg:col-span-2">
            {netTrend.length >= 2 ? (
              <TrendArea data={netTrend} color={CHART_COLORS.fuchsia} height={180} refY={0} />
            ) : (
              <div className="flex h-[180px] items-center justify-center text-xs text-neutral-600">
                该对手可用净额样本不足（需 ≥2 手）
              </div>
            )}
          </ChartCard>
          <ChartCard title="接地漏洞">
            {leaks.length ? (
              <div className="flex flex-wrap gap-1.5">
                {leaks.map(([k, v]) => (
                  <span key={k} className="rounded-lg bg-red-500/10 px-2 py-1 text-xs text-red-300 ring-1 ring-red-500/20">
                    {LEAK_LABEL[k] ?? k} · {v}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-neutral-600">样本内暂无明显接地漏洞。继续导入更多手牌以提高分辨率。</p>
            )}
          </ChartCard>
        </div>

        {/* 剥削报告 */}
        <Card className="mb-4 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-sm font-semibold text-fuchsia-200">
              <Sparkles className="size-4" /> AI 剥削报告
            </span>
            {report && <span className="text-[11px] text-neutral-500">基于 {report.basedOnHandCount} 手</span>}
            <Button onClick={runGen} disabled={gen.loading} variant="accent" size="sm" className="ml-auto">
              {gen.loading ? "生成中…" : report ? "重新生成" : "生成报告"}
            </Button>
          </div>
          {gen.error && <p className="mt-1 text-[11px] text-red-300">{gen.error}</p>}
          {report ? (
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-sm">
              <Markdown>{report.report}</Markdown>
              <ReportSources sources={report.sources} />
            </div>
          ) : (
            <p className="mt-3 text-xs text-neutral-500">
              基于服务端权威聚合的计数器生成剥削策略；接入知识库后附德州策略参考资料。
            </p>
          )}
        </Card>

        {/* 备注 */}
        <Card className="mb-4 p-4">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-xs font-semibold text-neutral-300">我的备注</span>
            {et.auto && et.tag && (
              <span className="rounded bg-fuchsia-500/15 px-1.5 py-0.5 text-[10px] text-fuchsia-300" title="根据聚合统计自动推断，可手动覆盖">
                AI 智能标签
              </span>
            )}
            <select
              value={et.tag}
              onChange={(e) => onSaveNote({ tag: e.target.value })}
              className="ml-auto rounded-md border border-white/10 bg-neutral-950 px-2 py-1 text-xs text-neutral-100 focus:border-fuchsia-600 focus:outline-none"
            >
              {OPP_TAGS.map((t) => (
                <option key={t} value={t}>{t || "无标签"}</option>
              ))}
            </select>
          </div>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => onSaveNote({ note })}
            rows={3}
            placeholder="打法、读牌、剥削计划…（失焦自动保存）"
            className="w-full resize-y rounded-md border border-white/10 bg-neutral-950 px-2.5 py-2 text-sm text-neutral-100 focus:border-fuchsia-600 focus:outline-none"
          />
        </Card>

        {/* 手牌清单 */}
        <Card className="p-4">
          <h2 className="mb-3 text-sm font-semibold text-neutral-300">
            相关手牌 <span className="font-normal text-neutral-500">{hands.length} 手</span>
          </h2>
          {hands.length === 0 ? (
            <p className="text-xs text-neutral-600">未在本地/云端导入历史里找到该对手的可展开手牌。</p>
          ) : (
            <ul className="divide-y divide-white/[0.05]">
              {hands
                .slice()
                .sort((a, b) => b.ts - a.ts)
                .map((h, i) => (
                  <HandItem key={`${h.ts}-${i}`} h={h} />
                ))}
            </ul>
          )}
        </Card>
      </main>
    </div>
  );
}

function cellPct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-white/[0.07] bg-neutral-900/50 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</div>
      <div className="mt-0.5 text-lg font-bold text-neutral-100">{value}</div>
      {sub && <div className="text-[10px] text-neutral-600">{sub}</div>}
    </div>
  );
}

function FreqBar({ row }: { row: FreqRow }) {
  const { cell, base, label, hint } = row;
  if (cell.n <= 0)
    return (
      <div className="flex items-center gap-2 text-xs text-neutral-600">
        <span className="w-40 shrink-0 truncate">{label}</span>
        <span>无样本</span>
      </div>
    );
  const shrunk = cell.shrunk ?? 0;
  const pct = Math.round(shrunk * 100);
  const basePct = Math.round(base * 100);
  const above = shrunk >= base;
  const fill = above ? CHART_COLORS.fuchsia : CHART_COLORS.sky;
  return (
    <div className="flex items-center gap-2">
      <span className="w-40 shrink-0 truncate text-xs text-neutral-300" title={hint}>{label}</span>
      <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-neutral-800">
        <div className="h-full rounded-full" style={{ width: `${Math.min(100, pct)}%`, backgroundColor: fill }} />
        {/* 群体基线标记 */}
        <div
          className="absolute top-0 h-full w-px bg-white/50"
          style={{ left: `${Math.min(100, basePct)}%` }}
          title={`群体基线 ${basePct}%`}
        />
      </div>
      <span className="w-20 shrink-0 text-right text-xs">
        <span className={above ? "text-fuchsia-300" : "text-sky-300"}>{pct}%</span>
        <span className="ml-1 text-[10px] text-neutral-600">n{cell.n}</span>
      </span>
    </div>
  );
}

function HandItem({ h }: { h: HandRow }) {
  return (
    <li className="flex items-center gap-3 py-2 text-sm">
      {h.thumb ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={h.thumb} alt="" className="h-9 w-14 shrink-0 rounded object-cover ring-1 ring-white/10" />
      ) : (
        <div className="h-9 w-14 shrink-0 rounded bg-neutral-800" />
      )}
      <span className="w-28 shrink-0 font-mono text-xs text-neutral-300">
        {h.board.length ? h.board.join(" ") : "—"}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-neutral-400">{h.line || "（无行动线）"}</span>
      {h.net != null && (
        <span className={`w-16 shrink-0 text-right text-xs font-semibold ${h.net > 0 ? "text-emerald-400" : h.net < 0 ? "text-red-400" : "text-neutral-500"}`}>
          {h.net > 0 ? "+" : ""}{Math.round(h.net)}
        </span>
      )}
    </li>
  );
}

/** 把 {街道, 标签} 列表按街聚合成一行可读行动线（街道已是中文标签）。 */
function streetLine(items: { street: string | null; label: string }[]): string {
  const clean = items.filter((it) => it.label);
  if (!clean.length) return "";
  const byStreet = new Map<string, string[]>();
  for (const it of clean) {
    const st = it.street || "翻前";
    if (!byStreet.has(st)) byStreet.set(st, []);
    byStreet.get(st)!.push(it.label);
  }
  const known = STREET_ORDER.filter((s) => byStreet.has(s));
  const extra = [...byStreet.keys()].filter((s) => !STREET_ORDER.includes(s));
  return [...known, ...extra].map((s) => `${s}: ${byStreet.get(s)!.join("·")}`).join("  |  ");
}

/** 稳定手牌键：优先 hand_id，否则用 board+底池的签名（用于跨云端/本地去重）。 */
function handKey(e: ImportEntry): string {
  const hid = e.item?.facts?.hand_id?.trim();
  if (hid) return `hid:${hid}`;
  const board = (e.item?.reconstruction?.board ?? e.item?.facts?.board ?? []).join("");
  const pot = e.item?.facts?.pot ?? "";
  return `sig:${board}|${pot}`;
}

/** 合并后的历史里，同一手可能同时来自云端与本地（id 不同）。按手去重，保留信息更全的一条。 */
function dedupByHand(history: ImportEntry[]): ImportEntry[] {
  const richness = (e: ImportEntry): number =>
    (e.item?.reconstruction?.players?.some((p) => p.actions?.length) ? 2 : 0) + (e.thumb ? 1 : 0);
  const map = new Map<string, ImportEntry>();
  for (const e of history) {
    const key = handKey(e);
    const prev = map.get(key);
    if (!prev || richness(e) > richness(prev)) map.set(key, e);
  }
  return [...map.values()];
}

/** 从导入历史里抽取某对手参与的手牌（board + 该对手的行动线 + 净额）。 */
function collectHands(history: ImportEntry[], alias: string): HandRow[] {
  const out: HandRow[] = [];
  for (const e of history) {
    const it = e.item;
    if (!it?.ok || !it.analysis?.supported) continue;
    const ap = it.analysis.players.find((p) => !p.is_hero && (p.alias || "").trim() === alias);
    if (!ap) continue;
    const board = it.reconstruction?.board ?? it.facts?.board ?? [];
    // 行动线：优先重建里的逐街动作，缺失则退回偏离标注里的动作。
    const rp = it.reconstruction?.players.find((p) => (p.alias || "").trim() === alias);
    let line = rp
      ? streetLine(rp.actions.map((a) => ({ street: a.street, label: a.label || a.action })))
      : "";
    if (!line && ap.deviations?.length) {
      line = streetLine(
        ap.deviations.map((d) => ({ street: d.street || null, label: d.actual_label || d.actual || "" })),
      );
    }
    out.push({ ts: e.ts, board, net: ap.net, line, thumb: e.thumb });
  }
  return out;
}
