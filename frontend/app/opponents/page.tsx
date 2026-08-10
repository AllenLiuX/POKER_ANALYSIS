"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, RefreshCw, Search, Sparkles, Users } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import DeviationFlags from "@/components/DeviationFlags";
import {
  CHART_COLORS,
  ChartCard,
  Donut,
  Legend,
  NetBars,
  type DonutSlice,
} from "@/components/charts/Charts";
import {
  loadImportHistory,
  mergeImportEntries,
  patchImportItems,
  type ImportEntry,
} from "@/lib/importHistory";
import { postOpponentReport } from "@/lib/api";
import {
  cloudReady,
  fetchCloudImports,
  fetchCloudOppNotes,
  fetchOpponentReports,
  syncLocalImportsToCloud,
  syncLocalOppNotesToCloud,
  upsertImportEntry,
  upsertOpponentNote,
  upsertOpponentReport,
  type OpponentReportRow,
} from "@/lib/cloud";
import {
  buildAllLocalProfiles,
  DEV_TAG_LEGEND,
  deviationTags,
  effectiveTag,
  ensureContributions,
  loadLocalReports,
  loadOppNotes,
  mergeOppNotes,
  OPP_TAGS,
  opponentHandNotes,
  saveLocalReport,
  saveOppNote,
  type CloudProfile,
  type DevCat,
  type DevFlag,
  type LocalReport,
  type OppNotes,
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
  "宽开 · 爱进攻": "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  "疑似送分 · 待验证": "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  "赢家 · 谨慎": "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  "接近均衡（样本内）": "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  "样本少 · 待观察": "bg-neutral-700/40 text-neutral-400 ring-neutral-600/40",
};
const archCls = (a: string) => ARCHETYPE_STYLE[a] ?? "bg-neutral-700/40 text-neutral-300 ring-neutral-600/40";

function unionById(list: ImportEntry[]): ImportEntry[] {
  const map = new Map<string, ImportEntry>();
  for (const e of list) if (e && !map.has(e.id)) map.set(e.id, e);
  return [...map.values()];
}

export default function OpponentsPage() {
  const [profiles, setProfiles] = useState<CloudProfile[]>([]);
  const [entries, setEntries] = useState<ImportEntry[]>([]);
  const [notes, setNotes] = useState<OppNotes>({});
  const [query, setQuery] = useState("");
  const [loggedIn, setLoggedIn] = useState(false);
  const [reagg, setReagg] = useState<{ loading: boolean; msg: string | null }>({ loading: false, msg: null });
  // 已生成的剥削报告：云端（key=opponentId）+ 本地缓存（key=alias），用于跳过与显示进度。
  const [cloudReps, setCloudReps] = useState<Record<string, OpponentReportRow>>({});
  const [localReps, setLocalReps] = useState<Record<string, LocalReport>>({});
  const [bulk, setBulk] = useState<{ loading: boolean; done: number; total: number; msg: string | null }>({
    loading: false,
    done: 0,
    total: 0,
    msg: null,
  });

  useEffect(() => {
    const local = loadImportHistory();
    setEntries(local);
    setProfiles(buildAllLocalProfiles(local));
    setNotes(loadOppNotes());
    setLocalReps(loadLocalReports());
    (async () => {
      // 备注：本地 ⇄ 云端合并
      try {
        await syncLocalOppNotesToCloud(loadOppNotes());
        const cloudNotes = await fetchCloudOppNotes();
        if (Object.keys(cloudNotes).length) setNotes(mergeOppNotes(cloudNotes));
      } catch {
        /* ignore */
      }
      try {
        const online = await cloudReady();
        setLoggedIn(online);
        if (online) fetchOpponentReports().then(setCloudReps).catch(() => {});
        // 导入历史：本地→云端补传 + 拉取云端（Supabase 的 import_entries 是唯一权威来源，上限 200）
        const cloudImports = online ? await fetchCloudImports() : [];
        if (online) await syncLocalImportsToCloud(loadImportHistory()).catch(() => {});
        if (cloudImports.length) mergeImportEntries(cloudImports); // 本地缓存（截断）供其它页
        // 用完整并集（不受本地 40 条截断）聚合每个对手，避免漏手。
        const union = unionById([...loadImportHistory(), ...cloudImports]);

        // 回填：补齐缺失的 contributions（每手都有重建即可派生）→ 写回 Supabase import_entries
        const { entries: enriched, patches } = await ensureContributions(union);
        if (Object.keys(patches).length) {
          patchImportItems(patches);
          if (online) {
            for (const id of Object.keys(patches)) {
              const e = enriched.find((x) => x.id === id);
              if (e) upsertImportEntry(e).catch(() => {});
            }
          }
        }
        setEntries(enriched);
        setProfiles(buildAllLocalProfiles(enriched));
      } catch {
        /* ignore */
      }
    })();
  }, []);

  const reaggregate = async () => {
    setReagg({ loading: true, msg: null });
    try {
      const base = entries.length ? entries : loadImportHistory();
      const { entries: enriched, patches } = await ensureContributions(base);
      if (Object.keys(patches).length) {
        patchImportItems(patches);
        for (const id of Object.keys(patches)) {
          const e = enriched.find((x) => x.id === id);
          if (e) upsertImportEntry(e).catch(() => {});
        }
      }
      setEntries(enriched);
      setProfiles(buildAllLocalProfiles(enriched));
      setReagg({ loading: false, msg: `已用 ${enriched.length} 手重新聚合（含 ${Object.keys(patches).length} 手补齐并写回云端）` });
    } catch {
      setReagg({ loading: false, msg: "重新聚合失败：请确认已登录。" });
    }
  };

  const onSaveNote = (alias: string, patch: { note?: string; tag?: string }) => {
    const next = saveOppNote(alias, patch);
    setNotes({ ...next });
    upsertOpponentNote(alias, next[alias]).catch(() => {});
  };

  const hasReport = (p: CloudProfile) => !!(cloudReps[p.opponentId] || localReps[p.alias]);

  const pendingReports = useMemo(
    () => profiles.filter((p) => !(cloudReps[p.opponentId] || localReps[p.alias])),
    [profiles, cloudReps, localReps],
  );

  // 批量生成 AI 剥削报告：只跑还没有报告的对手；结果写本地缓存 + Supabase（避免下次重算）。
  const generateAllReports = async () => {
    const targets = pendingReports;
    if (!targets.length) {
      setBulk({ loading: false, done: 0, total: 0, msg: "所有对手都已生成剥削报告" });
      return;
    }
    setBulk({ loading: true, done: 0, total: targets.length, msg: null });
    const src = entries.length ? entries : loadImportHistory();
    const newLocal: Record<string, LocalReport> = {};
    const newCloud: Record<string, OpponentReportRow> = {};
    let done = 0;
    let failed = 0;
    const runOne = async (p: CloudProfile) => {
      try {
        const res = await postOpponentReport({
          alias: p.alias,
          hands: p.hands,
          net: p.net,
          counters: p.counters,
          tag: effectiveTag(notes[p.alias]?.tag ?? "", p).tag || null,
          note: notes[p.alias]?.note || null,
          hand_notes: opponentHandNotes(src, p.alias),
        });
        const createdAt = new Date().toISOString();
        const lr: LocalReport = {
          report: res.report, model: "gpt-4o", basedOnHandCount: p.hands, createdAt, sources: res.sources,
        };
        saveLocalReport(p.alias, lr);
        newLocal[p.alias] = lr;
        newCloud[p.opponentId] = { opponentId: p.opponentId, ...lr };
        if (loggedIn)
          upsertOpponentReport(p.opponentId, res.report, "gpt-4o", p.hands, p.counters, res.sources).catch(() => {});
      } catch {
        failed += 1;
      } finally {
        done += 1;
        setBulk((b) => ({ ...b, done }));
      }
    };
    // 并发 3：LLM 调用较慢（每份约 10-15s），并发过高易触发限速。
    const queue = [...targets];
    const workers = Array.from({ length: Math.min(3, queue.length) }, async () => {
      while (queue.length) {
        const p = queue.shift();
        if (p) await runOne(p);
      }
    });
    await Promise.all(workers);
    setLocalReps((m) => ({ ...m, ...newLocal }));
    setCloudReps((m) => ({ ...m, ...newCloud }));
    const ok = done - failed;
    setBulk({
      loading: false,
      done,
      total: targets.length,
      msg: failed ? `完成 ${ok}/${targets.length}（${failed} 位失败，可重试）` : `已生成全部 ${ok} 份剥削报告`,
    });
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? profiles.filter((p) => p.alias.toLowerCase().includes(q)) : profiles;
    return list.slice().sort((a, b) => b.hands - a.hands || Math.abs(b.net) - Math.abs(a.net));
  }, [profiles, query]);

  const total = profiles.length;

  const dashProfiles = useMemo<DashProfile[]>(
    () => profiles.map((p) => ({ alias: p.alias, archetype: p.archetype, net: p.net, hands: p.hands })),
    [profiles],
  );

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
            {loggedIn ? (
              <Badge variant="success" size="sm">已登录 · 云端同步</Badge>
            ) : (
              <Badge variant="neutral" size="sm">本地（登录后跨设备同步）</Badge>
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
            按对手昵称跨手聚合导入历史（登录后经 Supabase import_entries 跨设备累计，单一数据源）。频率经小样本
            贝叶斯收缩后展示；样本天然偏向摊牌/关键手，仅供剥削倾向参考。点「深度分析」看图表与 AI 剥削报告。
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
              {shown.length}/{total} 位对手
              {total > 0 && (
                <span className="ml-2 text-neutral-600">
                  · 已画像 {total - pendingReports.length}/{total}
                </span>
              )}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <Button
                onClick={generateAllReports}
                disabled={bulk.loading || pendingReports.length === 0}
                variant="primary"
                size="sm"
                title="为所有还没有 AI 剥削报告的对手批量生成（结果写入 Supabase，避免重复生成）"
              >
                <Sparkles className={bulk.loading ? "animate-pulse" : ""} />
                {bulk.loading
                  ? `生成中 ${bulk.done}/${bulk.total}`
                  : pendingReports.length > 0
                    ? `一键生成剩余画像 (${pendingReports.length})`
                    : "画像已齐"}
              </Button>
              {loggedIn && (
                <Button
                  onClick={reaggregate}
                  disabled={reagg.loading}
                  variant="secondary"
                  size="sm"
                  title="用当前全部上传手牌重新聚合（补齐缺失统计并写回云端）"
                >
                  <RefreshCw className={reagg.loading ? "animate-spin" : ""} />
                  {reagg.loading ? "聚合中…" : "重新聚合"}
                </Button>
              )}
            </div>
            {bulk.msg && <span className="w-full text-xs text-neutral-500">{bulk.msg}</span>}
            {reagg.msg && <span className="w-full text-xs text-neutral-500">{reagg.msg}</span>}
          </div>
        )}

        {total > 0 && <OverviewDashboard profiles={dashProfiles} />}

        {total > 0 && <TagLegend />}

        {total === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-neutral-800 bg-neutral-900/20 px-6 py-12 text-center text-sm text-neutral-500">
            还没有对手档案。先到
            <Link href="/import" className="text-fuchsia-300 underline underline-offset-2">
              截图导入
            </Link>
            里上传几手回放截图，这里就会自动累积每个对手的画像。
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {shown.map((p) => {
              const et = effectiveTag(notes[p.alias]?.tag ?? "", p);
              return (
                <ProfileCard
                  key={p.opponentId}
                  p={p}
                  note={notes[p.alias]?.note ?? ""}
                  tag={et.tag}
                  tagAuto={et.auto}
                  reported={hasReport(p)}
                  onSave={(patch) => onSaveNote(p.alias, patch)}
                />
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}

// ---------- 概览仪表盘：画像分布 + 净额对比 ----------
interface DashProfile {
  alias: string;
  archetype: string;
  net: number;
  hands: number;
}

const ARCHETYPE_HEX: Record<string, string> = {
  "跟注站 · 过松": CHART_COLORS.amber,
  "过紧 · 怕事": CHART_COLORS.sky,
  被动: CHART_COLORS.neutral,
  "激进 · 爱诈唬": CHART_COLORS.red,
  "线路混乱": CHART_COLORS.fuchsia,
  "翻牌易弃 · 可多偷": "#fb923c",
  "爱看摊牌 · 黏": CHART_COLORS.violet,
  "宽开 · 爱进攻": "#fb7185",
  "疑似送分 · 待验证": "#fb923c",
  "赢家 · 谨慎": CHART_COLORS.emerald,
  "接近均衡（样本内）": CHART_COLORS.emerald,
  "样本少 · 待观察": CHART_COLORS.neutral,
};
const PALETTE = [CHART_COLORS.fuchsia, CHART_COLORS.sky, CHART_COLORS.amber, CHART_COLORS.violet, CHART_COLORS.emerald, CHART_COLORS.red];

function OverviewDashboard({ profiles }: { profiles: DashProfile[] }) {
  const totalHands = profiles.reduce((s, p) => s + p.hands, 0);
  const profiled = profiles.filter((p) => p.archetype !== "").length;

  const arch = useMemo<DonutSlice[]>(() => {
    const counts = new Map<string, number>();
    for (const p of profiles) {
      if (!p.archetype) continue; // 无画像（样本不足）不进分布
      counts.set(p.archetype, (counts.get(p.archetype) ?? 0) + 1);
    }
    let pi = 0;
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([name, value]) => ({
        name,
        value,
        color: ARCHETYPE_HEX[name] ?? PALETTE[pi++ % PALETTE.length],
      }));
  }, [profiles]);

  const netBars = useMemo(
    () =>
      profiles
        .filter((p) => Math.round(p.net) !== 0)
        .sort((a, b) => Math.abs(b.net) - Math.abs(a.net))
        .slice(0, 12)
        .map((p) => ({ label: p.alias.length > 6 ? p.alias.slice(0, 6) + "…" : p.alias, value: Math.round(p.net) }))
        .reverse(),
    [profiles],
  );

  return (
    <div className="mb-5 grid grid-cols-1 gap-3 lg:grid-cols-3">
      <ChartCard title="对手画像分布" className="lg:col-span-1">
        <Donut
          data={arch}
          height={168}
          centerTop={String(profiles.length)}
          centerSub="位对手"
          valueFormatter={(v) => `${Math.round(v)} 位`}
        />
        <div className="mt-3">
          <Legend items={arch.slice(0, 6).map((a) => ({ label: `${a.name}·${a.value}`, color: a.color }))} />
        </div>
      </ChartCard>

      <ChartCard
        title="净额对比（对手视角，前 12）"
        className="lg:col-span-2"
        right={
          <div className="flex gap-3 text-[11px]">
            <span className="text-neutral-500">
              观测 <span className="font-semibold text-neutral-300">{totalHands}</span> 手
            </span>
            <span className="text-neutral-500">
              已画像 <span className="font-semibold text-neutral-300">{profiled}</span>/{profiles.length}
            </span>
          </div>
        }
      >
        <NetBars data={netBars} height={188} />
        <p className="mt-1 text-[10px] text-neutral-600">
          正=对手赢（我方在其身上净输），负=对手输。样本偏向摊牌手，仅供参考。
        </p>
      </ChartCard>
    </div>
  );
}

function pctCell(c: StatCell): number | null {
  return c.shrunk != null ? Math.round(c.shrunk * 100) : null;
}

// 偏移标签图例：让用户一眼看清都有哪些标签、各自含义与剥削方向。
const DEVCAT_CLS: Record<DevCat, string> = {
  tight: "bg-sky-500/15 text-sky-300 ring-sky-500/25",
  loose: "bg-amber-500/15 text-amber-300 ring-amber-500/25",
  aggro: "bg-red-500/15 text-red-300 ring-red-500/25",
};
const DEVCAT_DOT: Record<DevCat, string> = { tight: "bg-sky-400", loose: "bg-amber-400", aggro: "bg-red-400" };
const DEVCAT_NAME: Record<DevCat, string> = { tight: "偏紧 / 被动", loose: "偏松 / 黏", aggro: "偏凶 / 激进" };

function TagLegend() {
  const groups: DevCat[] = ["loose", "aggro", "tight"];
  return (
    <details className="mb-5 rounded-2xl border border-white/[0.07] bg-neutral-900/40 p-4">
      <summary className="cursor-pointer select-none text-sm font-medium text-neutral-300">
        标签说明 · 共 {DEV_TAG_LEGEND.length} 种偏移读牌标签
        <span className="ml-2 text-xs font-normal text-neutral-500">
          （颜色区分方向；带「初」为小样本初判，仅供参考）
        </span>
      </summary>
      <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-3">
        {groups.map((cat) => (
          <div key={cat}>
            <div className="mb-2 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-neutral-500">
              <span className={`inline-block size-2 rounded-full ${DEVCAT_DOT[cat]}`} />
              {DEVCAT_NAME[cat]}
            </div>
            <ul className="space-y-1.5">
              {DEV_TAG_LEGEND.filter((t) => t.cat === cat).map((t) => (
                <li key={t.label} className="flex flex-col gap-0.5">
                  <span className={`w-fit rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1 ${DEVCAT_CLS[t.cat]}`}>
                    {t.label}
                  </span>
                  <span className="text-[11px] leading-snug text-neutral-500">{t.hint}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  );
}

function ProfileCard({
  p,
  note,
  tag,
  tagAuto,
  reported,
  onSave,
}: {
  p: CloudProfile;
  note: string;
  tag: string;
  tagAuto: boolean;
  reported?: boolean;
  onSave: (patch: { note?: string; tag?: string }) => void;
}) {
  const flags: DevFlag[] = deviationTags(p);
  const leaks = Object.entries(p.leaks).sort((a, b) => b[1] - a[1]);
  const href = `/opponents/${encodeURIComponent(p.opponentId)}`;
  const preAcc =
    p.gradedPre.n > 0 ? Math.round(((p.gradedPre.n - p.gradedPre.mistakes) / p.gradedPre.n) * 100) : null;

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2">
        <Link href={href} className="truncate text-base font-bold text-neutral-100 hover:text-fuchsia-300">
          {p.alias}
        </Link>
        {p.isHero && (
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300 ring-1 ring-emerald-500/30" title="样本里出现过你自己的座位">
            我
          </span>
        )}
        {p.archetype && (
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${archCls(p.archetype)}`}>
            {p.archetype}
          </span>
        )}
        {tag && (
          <span className="rounded-full bg-neutral-800 px-2 py-0.5 text-[10px] text-neutral-300" title={tagAuto ? "AI 智能标签" : "手动标签"}>
            {tag}
          </span>
        )}
        {p.net !== 0 && (
          <span className={`ml-auto text-sm font-semibold ${p.net > 0 ? "text-emerald-400" : "text-red-400"}`}>
            净 {p.net > 0 ? "+" : ""}
            {Math.round(p.net)}
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-neutral-400">
        <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">{p.hands} 手</span>
        {p.pfOpen.n > 0 && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">开池 {pctCell(p.pfOpen)}%·n{p.pfOpen.n}</span>
        )}
        {p.vsOpen.n > 0 && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">
            防开 弃{pctCell(p.vsOpen.fold)}·跟{pctCell(p.vsOpen.call)}·3b{pctCell(p.vsOpen.threebet)}·n{p.vsOpen.n}
          </span>
        )}
        {p.cbet.n > 0 && (
          <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-300">c-bet {pctCell(p.cbet)}%·n{p.cbet.n}</span>
        )}
        {p.wtsd.n > 0 && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">WTSD {pctCell(p.wtsd)}%·n{p.wtsd.n}</span>
        )}
        {preAcc != null && (
          <span className="rounded bg-neutral-800/70 px-1.5 py-0.5">
            翻前合规 {preAcc}%（偏离 {p.gradedPre.mistakes}）
          </span>
        )}
      </div>

      {flags.length > 0 && (
        <div className="mt-2.5">
          <div className="mb-1 text-[10px] uppercase tracking-wider text-neutral-500">GTO 偏移</div>
          <DeviationFlags flags={flags} />
        </div>
      )}
      {leaks.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {leaks.map(([k, v]) => (
            <span key={k} className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
              {LEAK_LABEL[k] ?? k}×{v}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2">
        <Link
          href={href}
          className="inline-flex items-center gap-0.5 text-xs text-fuchsia-300 hover:text-fuchsia-200"
        >
          深度分析（图表 + AI）
          <ArrowRight className="size-3" />
        </Link>
        {reported && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-emerald-500/12 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300 ring-1 ring-emerald-500/25"
            title="已生成 AI 剥削报告（已缓存，点开即看）"
          >
            <Sparkles className="size-2.5" />
            已画像
          </span>
        )}
      </div>
      <NotesEditor note={note} tag={tag} tagAuto={tagAuto} onSave={onSave} />
    </Card>
  );
}

function NotesEditor({
  note,
  tag,
  tagAuto,
  onSave,
}: {
  note: string;
  tag: string;
  tagAuto?: boolean;
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
        {tagAuto && tag && (
          <span className="rounded bg-fuchsia-500/15 px-1.5 py-0.5 text-[10px] text-fuchsia-300" title="根据聚合统计自动推断，可手动覆盖">
            AI 智能
          </span>
        )}
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
