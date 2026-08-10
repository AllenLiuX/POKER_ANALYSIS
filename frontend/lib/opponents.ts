// 对手档案：把「截图导入历史」按对手昵称跨手聚合成持久画像（HUD 风格），
// 并本地持久化每个对手的备注/标签（云端同步见 cloud.ts opponent_notes）。
import type { Deviation, IngestItem, KbSource, OpponentCounters } from "./api";
import { postIngestContributions } from "./api";
import {
  applyHandContributions,
  cloudReady,
  retractHand,
  type OpponentAggregateRow,
} from "./cloud";
import type { ImportEntry } from "./importHistory";

export interface OpponentProfile {
  alias: string;
  hands: number; // 出现过的手数
  net: number; // 累计净额（对手视角）
  sample: number; // 可接地的翻前决策数
  mistakes: number; // 翻前偏离数
  accuracy: number | null; // 合规率
  rfi: number;
  defend: number;
  foldVsOpen: number;
  threebet: number;
  postflopSample: number; // 接地翻后决策数
  postflopMistakes: number;
  leaks: Record<string, number>; // 倾向计数（too_loose / too_tight ...）
  archetype: string; // 画像标签（由统计推断）
  firstSeen: number;
  lastSeen: number;
}

const PRE_SPOTS = new Set(["RFI", "vs_RFI"]);

function classify(p: OpponentProfile): string {
  const total = p.sample + p.postflopSample;
  if (total < 4) return "";
  const entries = Object.entries(p.leaks).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0 || (entries[0]?.[1] ?? 0) === 0) return "接近均衡（样本内）";
  const [top] = entries[0];
  const map: Record<string, string> = {
    too_loose: "跟注站 · 过松",
    too_tight: "过紧 · 怕事",
    too_passive: "被动",
    too_aggressive: "激进 · 爱诈唬",
    line_error: "线路混乱",
  };
  return map[top] ?? "有明显漏洞";
}

/** 把全部导入历史按对手 alias 聚合成档案（排除英雄本人）。 */
export function buildOpponentProfiles(history: ImportEntry[]): OpponentProfile[] {
  const map = new Map<string, OpponentProfile>();
  for (const e of history) {
    const it = e.item;
    if (!it?.ok || !it.analysis?.supported) continue;
    for (const pl of it.analysis.players) {
      if (pl.is_hero) continue;
      const alias = (pl.alias || "").trim();
      if (!alias) continue;
      let prof = map.get(alias);
      if (!prof) {
        prof = {
          alias, hands: 0, net: 0, sample: 0, mistakes: 0, accuracy: null,
          rfi: 0, defend: 0, foldVsOpen: 0, threebet: 0,
          postflopSample: 0, postflopMistakes: 0, leaks: {}, archetype: "",
          firstSeen: e.ts, lastSeen: e.ts,
        };
        map.set(alias, prof);
      }
      prof.hands += 1;
      if (typeof pl.net === "number") prof.net += pl.net;
      prof.firstSeen = Math.min(prof.firstSeen, e.ts);
      prof.lastSeen = Math.max(prof.lastSeen, e.ts);
      for (const d of pl.deviations as Deviation[]) {
        const isPre = d.grounded && PRE_SPOTS.has(d.spot);
        const isPost = d.grounded && d.spot.startsWith("postflop");
        if (isPre) {
          prof.sample += 1;
          if (d.spot === "RFI") prof.rfi += 1;
          if (d.spot === "vs_RFI") {
            prof.defend += 1;
            if (d.actual === "fold") prof.foldVsOpen += 1;
            if (d.actual === "raise") prof.threebet += 1;
          }
          if (d.grade === "mistake") {
            prof.mistakes += 1;
            if (d.deviation_type) prof.leaks[d.deviation_type] = (prof.leaks[d.deviation_type] ?? 0) + 1;
          }
        } else if (isPost) {
          prof.postflopSample += 1;
          if (d.grade === "mistake") {
            prof.postflopMistakes += 1;
            if (d.deviation_type) prof.leaks[d.deviation_type] = (prof.leaks[d.deviation_type] ?? 0) + 1;
          }
        }
      }
    }
  }
  const list = Array.from(map.values());
  for (const p of list) {
    p.accuracy = p.sample > 0 ? (p.sample - p.mistakes) / p.sample : null;
    p.archetype = classify(p);
  }
  // 默认按样本量（可接地决策）降序，其次按手数
  list.sort((a, b) => b.sample + b.postflopSample - (a.sample + a.postflopSample) || b.hands - a.hands);
  return list;
}

// ============ Phase A：服务端权威聚合的派生（HUD + 贝叶斯收缩 + 画像）============

/** 聚合单元 key：优先 hand_id（可跨设备去重），否则退回条目 id（每条一算）。 */
export function aggKey(entry: ImportEntry): string {
  const hid = entry.item?.facts?.hand_id?.trim();
  return hid && hid.length ? hid : entry.id;
}

/** 把一条导入并入云端对手聚合（幂等）。prevKey 用于编辑后撤销旧聚合。 */
export async function syncEntryToProfiles(entry: ImportEntry, prevKey?: string): Promise<void> {
  if (!(await cloudReady())) return;
  const item = entry.item;
  if (!item?.ok || item.recognized === false) return;
  let players = item.contributions?.players;
  if (!players) {
    try {
      const r = await postIngestContributions(
        item.facts ?? {}, item.reconstruction ?? null, item.analysis ?? null,
      );
      players = r.contributions.players;
    } catch {
      return; // 派生失败不阻塞导入
    }
  }
  const key = aggKey(entry);
  try {
    if (prevKey && prevKey !== key) await retractHand(prevKey);
    await retractHand(key);                       // 幂等：无既有贡献则无操作
    await applyHandContributions(key, players ?? []);
  } catch {
    /* 网络/权限问题静默忽略 */
  }
}

/** 从云端聚合中移除某条导入（删除条目时调用）。 */
export async function removeEntryFromProfiles(entry: ImportEntry): Promise<void> {
  if (!(await cloudReady())) return;
  try {
    await retractHand(aggKey(entry));
  } catch {
    /* 忽略 */
  }
}

// ---- 统计派生（HUD） ----
export interface StatCell {
  k: number;
  n: number;
  pct: number | null;   // 原始频率 k/n
  shrunk: number | null; // 贝叶斯收缩后的频率
}

// 群体基线先验（用于小样本收缩，非精确 GTO，仅防止早期估计乱跳）
export const BASE = {
  foldVsOpen: 0.5, callVsOpen: 0.32, threebet: 0.1,
  pfOpen: 0.4, afPost: 0.45, cbet: 0.55, foldVsCbet: 0.45, wtsd: 0.28, wonSd: 0.5,
};
const PRIOR_M = 6; // 先验强度（相当于 6 手观测）

function cell(k: number, n: number, p0: number): StatCell {
  return {
    k, n,
    pct: n > 0 ? k / n : null,
    shrunk: n > 0 ? (k + PRIOR_M * p0) / (n + PRIOR_M) : null,
  };
}

export interface CloudProfile {
  opponentId: string;
  alias: string;
  hands: number;
  net: number;
  updatedAt: number;
  vsOpen: { n: number; fold: StatCell; call: StatCell; threebet: StatCell };
  pfOpen: StatCell;
  afPost: StatCell;
  cbet: StatCell;
  foldVsCbet: StatCell;
  wtsd: StatCell;
  wonSd: StatCell;
  leaks: Record<string, number>;
  gradedPre: { n: number; mistakes: number };
  gradedPost: { n: number; mistakes: number };
  sample: number;
  archetype: string;
  counters: OpponentCounters;
  isHero?: boolean; // 该昵称在样本里出现过英雄座位（即「我」）——用于标记，不影响统计
}

function num(v: unknown): number {
  return typeof v === "number" && isFinite(v) ? v : 0;
}

/** 由聚合计数器派生 HUD 展示 + 画像。 */
export function deriveCloudProfile(row: OpponentAggregateRow): CloudProfile {
  const c = row.counters || ({} as OpponentCounters);
  const vs = c.pf_vs_open ?? { n: 0, fold: 0, call: 0, raise: 0 };
  const vsN = num(vs.n);
  const af = c.af_post ?? { aggr: 0, passive: 0 };
  const afTotal = num(af.aggr) + num(af.passive);
  const leaks: Record<string, number> = {};
  for (const bucket of [c.leaks_pre, c.leaks_post]) {
    if (bucket) for (const [k, v] of Object.entries(bucket)) leaks[k] = (leaks[k] ?? 0) + num(v);
  }
  const profile: CloudProfile = {
    opponentId: row.opponentId,
    alias: row.alias,
    hands: row.handCount,
    net: row.net,
    updatedAt: row.updatedAt,
    vsOpen: {
      n: vsN,
      fold: cell(num(vs.fold), vsN, BASE.foldVsOpen),
      call: cell(num(vs.call), vsN, BASE.callVsOpen),
      threebet: cell(num(vs.raise), vsN, BASE.threebet),
    },
    pfOpen: cell(num(c.pf_open?.k), num(c.pf_open?.n), BASE.pfOpen),
    afPost: cell(num(af.aggr), afTotal, BASE.afPost),
    cbet: cell(num(c.cbet_flop?.k), num(c.cbet_flop?.n), BASE.cbet),
    foldVsCbet: cell(num(c.fold_vs_cbet_flop?.k), num(c.fold_vs_cbet_flop?.n), BASE.foldVsCbet),
    wtsd: cell(num(c.wtsd?.k), num(c.wtsd?.n), BASE.wtsd),
    wonSd: cell(num(c.won_sd?.k), num(c.won_sd?.n), BASE.wonSd),
    leaks,
    gradedPre: { n: num(c.graded_pre?.n), mistakes: num(c.graded_pre?.mistakes) },
    gradedPost: { n: num(c.graded_post?.n), mistakes: num(c.graded_post?.mistakes) },
    sample: vsN + num(c.pf_open?.n) + afTotal + num(c.wtsd?.n),
    archetype: "",
    counters: c,
  };
  profile.archetype = classifyCloud(profile);
  return profile;
}

/** 由 HUD + 漏洞推断画像标签。样本足→用收缩值（高置信）；样本少→用原始频率（初判）；
 *  再退回接地漏洞与净额结果，保证每个对手都有一个可读画像（不再返回空）。 */
function classifyCloud(p: CloudProfile): string {
  const s = p.vsOpen;
  const raw = (c: StatCell): number | null => (c.n > 0 ? c.pct : null);
  const sh = (c: StatCell): number => c.shrunk ?? 0;
  // 1) 强信号（样本达标，用收缩值）
  if (s.n >= 5 && sh(s.call) > 0.45) return "跟注站 · 过松";
  if (p.afPost.n >= 5 && sh(p.afPost) > 0.62) return "激进 · 爱诈唬";
  if (s.n >= 5 && sh(s.fold) > 0.72) return "过紧 · 怕事";
  if (p.foldVsCbet.n >= 4 && sh(p.foldVsCbet) > 0.62) return "翻牌易弃 · 可多偷";
  if (p.wtsd.n >= 5 && sh(p.wtsd) > 0.42) return "爱看摊牌 · 黏";
  if (p.pfOpen.n >= 5 && sh(p.pfOpen) > 0.55) return "宽开 · 爱进攻";
  // 2) 初判（小样本，用原始频率；标签同类，容忍一定噪音）
  if (s.n >= 2 && (raw(s.call) ?? 0) >= 0.6) return "跟注站 · 过松";
  if (s.n >= 2 && (raw(s.fold) ?? 0) >= 0.75) return "过紧 · 怕事";
  if (p.foldVsCbet.n >= 2 && (raw(p.foldVsCbet) ?? 0) >= 0.6) return "翻牌易弃 · 可多偷";
  if (p.cbet.n >= 2 && (raw(p.cbet) ?? 0) >= 0.75) return "激进 · 爱诈唬";
  if (p.wtsd.n >= 2 && (raw(p.wtsd) ?? 0) >= 0.5) return "爱看摊牌 · 黏";
  if (p.pfOpen.n >= 2 && (raw(p.pfOpen) ?? 0) >= 0.6) return "宽开 · 爱进攻";
  // 3) 退回接地漏洞
  const top = Object.entries(p.leaks).sort((a, b) => b[1] - a[1])[0];
  const map: Record<string, string> = {
    too_loose: "跟注站 · 过松", too_tight: "过紧 · 怕事",
    too_passive: "被动", too_aggressive: "激进 · 爱诈唬", line_error: "线路混乱",
  };
  if (top && top[1] > 0) return map[top[0]] ?? "有明显漏洞";
  // 4) 净额结果兜底（对手视角：正=对手赢我方）
  if (p.hands >= 2 && p.net <= -800) return "疑似送分 · 待验证";
  if (p.hands >= 2 && p.net >= 800) return "赢家 · 谨慎";
  if (p.sample >= 6) return "接近均衡（样本内）";
  return "样本少 · 待观察";
}

// ---- GTO 偏移标签：由收缩后频率 vs 群体基线派生，带样本门控，一眼看出偏离方向 ----

export type DevCat = "tight" | "loose" | "aggro"; // 偏紧/被动 · 偏松/黏 · 偏凶
export type DevConf = "high" | "low"; // high=样本达标（收缩值）；low=样本偏少的初判（原始频率）
export interface DevFlag {
  key: string;
  label: string;
  cat: DevCat;
  severity: number; // 相对基线的偏离幅度（排序用）
  hint: string; // 剥削建议（tooltip）
  conf: DevConf;
}

interface DevRule {
  key: string;
  get: (p: CloudProfile) => StatCell;
  base: number;
  gate: number; // 触发所需的最小原始样本 n
  high?: { t: number; label: string; cat: DevCat; hint: string };
  low?: { t: number; label: string; cat: DevCat; hint: string };
}

// 门控 gate = 触发标签所需的最小原始样本 n。截图样本天然偏小，门控偏低以保证可见，
// 但配合贝叶斯收缩（PRIOR_M=6）防止早期估计乱跳。
const DEV_RULES: DevRule[] = [
  {
    key: "pfOpen", get: (p) => p.pfOpen, base: BASE.pfOpen, gate: 4,
    high: { t: 0.55, label: "开池过松", cat: "loose", hint: "他开池太宽——收紧跟注、放心 3bet 施压。" },
    low: { t: 0.25, label: "开池过紧", cat: "tight", hint: "他只开强牌——尊重其加注，别轻易反抗。" },
  },
  {
    key: "foldVsOpen", get: (p) => p.vsOpen.fold, base: BASE.foldVsOpen, gate: 4,
    high: { t: 0.66, label: "面对开池过度弃牌", cat: "tight", hint: "他面对开池弃太多——扩大偷盲/开池范围。" },
    low: { t: 0.34, label: "面对开池防守过宽", cat: "loose", hint: "他很少弃牌——减少诈唬，价值下注更薄更多。" },
  },
  {
    key: "callVsOpen", get: (p) => p.vsOpen.call, base: BASE.callVsOpen, gate: 4,
    high: { t: 0.46, label: "冷跟过多", cat: "loose", hint: "他跟注过宽——加大价值下注、少诈唬。" },
  },
  {
    key: "threebet", get: (p) => p.vsOpen.threebet, base: BASE.threebet, gate: 5,
    high: { t: 0.17, label: "3bet 过频", cat: "aggro", hint: "他 3bet 太多——用更强范围 4bet/跟注反击。" },
    low: { t: 0.035, label: "几乎不 3bet", cat: "tight", hint: "他几乎不 3bet——放心开池偷盲。" },
  },
  {
    key: "cbet", get: (p) => p.cbet, base: BASE.cbet, gate: 3,
    high: { t: 0.74, label: "c-bet 过多", cat: "aggro", hint: "他翻牌乱开火——多 check-raise/浮动惩罚。" },
    low: { t: 0.38, label: "c-bet 过少", cat: "tight", hint: "他常放弃主动权——他过牌时多偷。" },
  },
  {
    key: "foldVsCbet", get: (p) => p.foldVsCbet, base: BASE.foldVsCbet, gate: 3,
    high: { t: 0.62, label: "面对 c-bet 过度弃牌", cat: "tight", hint: "他面对 c-bet 弃太多——高频小注诈唬。" },
    low: { t: 0.28, label: "面对 c-bet 很少弃(黏)", cat: "loose", hint: "他很黏——减少诈唬、纯价值加注。" },
  },
  {
    key: "afPost", get: (p) => p.afPost, base: BASE.afPost, gate: 4,
    high: { t: 0.62, label: "翻后过度激进", cat: "aggro", hint: "他翻后过凶——多抓诈/轻跟。" },
    low: { t: 0.30, label: "翻后过于被动", cat: "tight", hint: "他翻后被动——多价值下注，遇加注多让路。" },
  },
  {
    key: "wtsd", get: (p) => p.wtsd, base: BASE.wtsd, gate: 4,
    high: { t: 0.40, label: "看摊牌过多(黏)", cat: "loose", hint: "他爱看摊牌——价值下注更薄、少诈唬。" },
    low: { t: 0.17, label: "看摊牌过少(易弃)", cat: "tight", hint: "他常弃到河牌——多桶诈唬。" },
  },
  {
    key: "wonSd", get: (p) => p.wonSd, base: BASE.wonSd, gate: 4,
    low: { t: 0.42, label: "摊牌胜率偏低(跟太宽)", cat: "loose", hint: "他到摊牌常输——加大价值下注的频率与厚度。" },
  },
];

const _CN_STREETS = ["翻前", "翻牌", "转牌", "河牌"];

/** 为某对手抽取逐手紧凑观测（board/位置/行动线/净额/摊牌），供 LLM 少样本剥削读牌。 */
export function opponentHandNotes(history: ImportEntry[], alias: string, max = 12): string[] {
  const notes: string[] = [];
  for (const e of dedupByHand(history)) {
    const it = e.item;
    if (!it?.ok || it.recognized === false) continue;
    const rp = it.reconstruction?.players?.find((p) => (p.alias || "").trim() === alias);
    const ap = it.analysis?.players?.find((p) => (p.alias || "").trim() === alias);
    const cp = it.contributions?.players?.find((p) => (p.alias || "").trim() === alias);
    if (!rp && !ap && !cp) continue;
    const board = it.reconstruction?.board ?? it.facts?.board ?? [];
    const acts = rp?.actions ?? [];
    let line = "";
    if (acts.length) {
      const byS: Record<string, string[]> = {};
      for (const a of acts) {
        const st = a.street || "翻前";
        (byS[st] ||= []).push(a.label || a.action);
      }
      line = _CN_STREETS.filter((s) => byS[s]).map((s) => `${s}:${byS[s].join("·")}`).join(" ");
    }
    const cards = (rp?.hole_cards?.length ? rp.hole_cards : ap?.hole_cards) || [];
    const pos = ap?.position ?? rp?.position ?? null;
    const net = typeof ap?.net === "number" ? ap.net : typeof cp?.net === "number" ? cp.net : rp?.net;
    const parts: string[] = [`板[${board.join(" ") || "—"}]`];
    if (pos) parts.push(String(pos));
    parts.push(line || "无可见动作");
    if (typeof net === "number") parts.push(`净${net > 0 ? "+" : ""}${Math.round(net)}`);
    if (cards.length) parts.push(`摊牌${cards.join("")}`);
    notes.push(`#${notes.length + 1} ` + parts.join("｜"));
    if (notes.length >= max) break;
  }
  return notes;
}

/** 由频率派生 GTO 偏移标签。
 *  - 样本达标（n≥gate）：用贝叶斯收缩值 → 高置信标签；
 *  - 样本偏少（2≤n<gate）：用原始频率 → 低置信「初判」标签（截图样本天然偏小，保证每个对手都能看出倾向）。
 *  再补充基于净额/摊牌的「读牌」标签，最后按 高置信优先→偏离幅度 排序。 */
export function deviationTags(p: CloudProfile): DevFlag[] {
  const out: DevFlag[] = [];
  for (const r of DEV_RULES) {
    const cell = r.get(p);
    if (cell.n < 1) continue;
    const high = cell.n >= r.gate && cell.shrunk != null;
    // 低置信用原始频率（单手噪音太大，要求 n≥2）
    const v = high ? (cell.shrunk as number) : cell.n >= 2 ? cell.pct : null;
    if (v == null) continue;
    const conf: DevConf = high ? "high" : "low";
    const denom = Math.max(0.05, r.base);
    if (r.high && v >= r.high.t) {
      out.push({ key: r.key, label: r.high.label, cat: r.high.cat, hint: r.high.hint, severity: (v - r.base) / denom, conf });
    } else if (r.low && v <= r.low.t) {
      out.push({ key: r.key + "_lo", label: r.low.label, cat: r.low.cat, hint: r.low.hint, severity: (r.base - v) / denom, conf });
    }
  }
  // 结果类「读牌」标签（净额为对手视角：正=对手赢我方）
  if (p.hands >= 2) {
    if (p.net <= -1200)
      out.push({ key: "netLoser", label: "此前给我方送分", cat: "loose", hint: "历史净额大幅为负——放心扩大价值下注，别怕被诈唬。", severity: 1.2, conf: "low" });
    else if (p.net >= 1200)
      out.push({ key: "netWinner", label: "此前赢我方较多", cat: "aggro", hint: "历史净额为正——保持谨慎，少送薄价值，多让路给其强行动。", severity: 1.0, conf: "low" });
  }
  // 去重（同 label 只留最强/最高置信的一个）
  const seen = new Map<string, DevFlag>();
  for (const f of out) {
    const prev = seen.get(f.label);
    if (!prev || (prev.conf === "low" && f.conf === "high") || f.severity > prev.severity) seen.set(f.label, f);
  }
  return [...seen.values()]
    .sort((a, b) => (a.conf === b.conf ? b.severity - a.severity : a.conf === "high" ? -1 : 1))
    .slice(0, 8);
}

/** 所有可能出现的偏移标签目录（供前端「标签说明」图例）。 */
export const DEV_TAG_LEGEND: { cat: DevCat; label: string; hint: string }[] = (() => {
  const seen = new Set<string>();
  const rows: { cat: DevCat; label: string; hint: string }[] = [];
  const add = (t?: { label: string; cat: DevCat; hint: string }) => {
    if (t && !seen.has(t.label)) {
      seen.add(t.label);
      rows.push({ cat: t.cat, label: t.label, hint: t.hint });
    }
  };
  for (const r of DEV_RULES) {
    add(r.high);
    add(r.low);
  }
  add({ label: "此前给我方送分", cat: "loose", hint: "历史净额大幅为负——放心扩大价值下注，别怕被诈唬。" });
  add({ label: "此前赢我方较多", cat: "aggro", hint: "历史净额为正——保持谨慎，少送薄价值。" });
  return rows;
})();

// ---- 雷达图 / 频率条派生（对手详情页用） ----

/** 雷达图数据：每个维度以「基线=50」归一，>50 表示高于群体基线。 */
export interface RadarStat {
  stat: string;
  value: number; // 0..100（相对基线，50=基线）
  baseline: number; // 恒为 50
  raw: number | null; // 收缩后原始频率
  base: number; // 群体基线频率
  n: number;
}

export function radarStats(p: CloudProfile): RadarStat[] {
  const norm = (shrunk: number | null, base: number): number =>
    shrunk == null ? 50 : Math.max(4, Math.min(100, (shrunk / base) * 50));
  const mk = (stat: string, cell: StatCell, base: number): RadarStat => ({
    stat,
    value: norm(cell.shrunk, base),
    baseline: 50,
    raw: cell.shrunk,
    base,
    n: cell.n,
  });
  return [
    mk("弃vs开池", p.vsOpen.fold, BASE.foldVsOpen),
    mk("3bet", p.vsOpen.threebet, BASE.threebet),
    mk("c-bet", p.cbet, BASE.cbet),
    mk("弃vsC-bet", p.foldVsCbet, BASE.foldVsCbet),
    mk("翻后激进", p.afPost, BASE.afPost),
    mk("看摊牌", p.wtsd, BASE.wtsd),
  ];
}

/** 频率条数据：展示收缩后频率、群体基线、样本量与偏离方向。 */
export interface FreqRow {
  label: string;
  cell: StatCell;
  base: number;
  hint?: string;
}

export function freqRows(p: CloudProfile): FreqRow[] {
  return [
    { label: "首入池开池 (open)", cell: p.pfOpen, base: BASE.pfOpen, hint: "越高越松" },
    { label: "面对开池 · 弃牌", cell: p.vsOpen.fold, base: BASE.foldVsOpen, hint: "越高越可偷" },
    { label: "面对开池 · 跟注", cell: p.vsOpen.call, base: BASE.callVsOpen, hint: "越高越黏" },
    { label: "面对开池 · 3bet", cell: p.vsOpen.threebet, base: BASE.threebet, hint: "越高越激进" },
    { label: "翻牌 c-bet", cell: p.cbet, base: BASE.cbet },
    { label: "面对 c-bet 弃牌", cell: p.foldVsCbet, base: BASE.foldVsCbet, hint: "越高越可诈唬" },
    { label: "翻后激进度 (AF)", cell: p.afPost, base: BASE.afPost },
    { label: "看到摊牌 (WTSD)", cell: p.wtsd, base: BASE.wtsd, hint: "越高越少弃牌" },
  ];
}

// ---------- 智能标签（根据聚合信息自动推断）----------

/** 由画像 + 净额 + 手数自动推断一个粗粒度标签（对应 OPP_TAGS 之一）。样本不足则返回空。 */
export function autoTag(p: {
  archetype: string;
  net?: number;
  hands?: number;
  leaks?: Record<string, number>;
}): string {
  const hands = p.hands ?? 0;
  const net = p.net ?? 0;
  const a = p.archetype || "";
  if (hands < 2 || !a) return "";
  if (a.includes("过松") || a.includes("跟注站") || a.includes("黏") || a.includes("送分"))
    return net <= -1500 ? "鱼/娱乐" : "跟注站";
  if (a.includes("激进") || a.includes("进攻") || a.includes("宽开")) return "激进/疯";
  if (a.includes("过紧") || a.includes("被动") || a.includes("易弃")) return "紧弱";
  if (a.includes("线路混乱")) return "鱼/娱乐";
  if (a.includes("赢家")) return "危险/强";
  if (a.includes("均衡")) return net >= 1500 ? "危险/强" : "常规";
  return "常规";
}

/** 用户手设标签优先；否则用智能标签（auto=true 表示是自动推断的）。 */
export function effectiveTag(
  userTag: string,
  p: { archetype: string; net?: number; hands?: number; leaks?: Record<string, number> },
): { tag: string; auto: boolean } {
  if (userTag) return { tag: userTag, auto: false };
  const t = autoTag(p);
  return { tag: t, auto: !!t };
}

// ---------- 本地画像（无云端聚合时，用导入历史里内嵌的贡献计数器现算）----------

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function deepAdd(acc: Record<string, unknown>, add: Record<string, unknown>): void {
  for (const [k, v] of Object.entries(add)) {
    if (typeof v === "number") {
      acc[k] = (typeof acc[k] === "number" ? (acc[k] as number) : 0) + v;
    } else if (isPlainObject(v)) {
      if (!isPlainObject(acc[k])) acc[k] = {};
      deepAdd(acc[k] as Record<string, unknown>, v);
    }
  }
}

const _norm = (a: unknown): string => (typeof a === "string" ? a.trim() : "");

/** 稳定手牌键：优先 hand_id（跨云端/本地去重），否则退回条目 id（每条一算）。 */
export function handSig(e: ImportEntry): string {
  const hid = e.item?.facts?.hand_id?.trim();
  return hid && hid.length ? `hid:${hid}` : `id:${e.id}`;
}

/** 按「手」去重：同一手可能被多次导入（同 hand_id）或同时来自云端/本地。
 * 保留识别最完整的一条（重建里带动作的玩家更多、非英雄玩家更多、有缩略图）——
 * 避免因为保留了某个漏识别对手的副本，导致该对手在这手里"消失"。 */
export function dedupByHand(history: ImportEntry[]): ImportEntry[] {
  const richness = (e: ImportEntry): number => {
    const recon = e.item?.reconstruction?.players ?? [];
    const withActions = recon.filter((p) => p.actions?.length).length;
    const nonHero = recon.filter((p) => !p.is_hero).length;
    const contrib = e.item?.contributions?.players?.length ?? 0;
    return withActions * 4 + nonHero + contrib + (e.thumb ? 1 : 0);
  };
  const map = new Map<string, ImportEntry>();
  for (const e of history) {
    const key = handSig(e);
    const prev = map.get(key);
    if (!prev || richness(e) > richness(prev)) map.set(key, e);
  }
  return [...map.values()];
}

/** 某手里该玩家（含英雄本人）的记录：优先内嵌 contributions（不受 analysis.supported / 重建状态限制），
 * 退回 analysis / reconstruction。返回 { net, counters, isHero } 供聚合；找不到返回 null。
 * 注意：只要该玩家出现在这手（任何座位、含未摊牌/待复核），就算一手——一手可进多个玩家档案。 */
function opponentInHand(
  it: ImportEntry["item"],
  alias: string,
): { net: number | null; counters: OpponentCounters | null; isHero: boolean } | null {
  const cp = it?.contributions?.players?.find((p) => _norm(p.alias) === alias);
  if (cp) return { net: typeof cp.net === "number" ? cp.net : null, counters: cp.counters, isHero: !!cp.is_hero };
  const ap = it?.analysis?.players?.find((p) => _norm(p.alias) === alias);
  if (ap) return { net: typeof ap.net === "number" ? ap.net : null, counters: null, isHero: !!ap.is_hero };
  const rp = it?.reconstruction?.players?.find((p) => _norm(p.alias) === alias);
  if (rp) return { net: typeof rp.net === "number" ? rp.net : null, counters: null, isHero: !!rp.is_hero };
  return null;
}

/** 从导入历史里为某 alias 现算一个 CloudProfile（求和内嵌 contributions）。无匹配手返回 null。
 * 按 hand_id 去重后再统计——同一手多次导入只算一手，云端/本地口径一致。
 * 不再排除英雄：只要该玩家在这手出现（任何座位、含待复核/未摊牌）就计入其档案。 */
export function buildLocalCloudProfile(history: ImportEntry[], alias: string): CloudProfile | null {
  const counters: Record<string, unknown> = {};
  let net = 0;
  let hands = 0;
  let ts = 0;
  let heroSeen = false;
  for (const e of dedupByHand(history)) {
    const it = e.item;
    if (!it?.ok || it.recognized === false) continue;
    const found = opponentInHand(it, alias);
    if (!found) continue;
    hands += 1;
    ts = Math.max(ts, e.ts);
    if (found.isHero) heroSeen = true;
    if (typeof found.net === "number") net += found.net;
    if (found.counters) deepAdd(counters, found.counters as unknown as Record<string, unknown>);
  }
  if (hands === 0) return null;
  const prof = deriveCloudProfile({
    opponentId: `alias:${alias}`,
    alias,
    handCount: hands,
    net,
    counters: counters as unknown as OpponentCounters,
    updatedAt: ts || Date.now(),
  });
  prof.isHero = heroSeen;
  return prof;
}

/** 导入历史里出现过的全部玩家昵称（含英雄本人——一手可进多个档案）。以 contributions 为准。 */
export function opponentAliases(history: ImportEntry[]): string[] {
  const set = new Set<string>();
  for (const e of dedupByHand(history)) {
    const it = e.item;
    if (!it?.ok || it.recognized === false) continue;
    const src = it.contributions?.players ?? it.analysis?.players ?? it.reconstruction?.players ?? [];
    for (const p of src) {
      const a = _norm(p.alias);
      if (a) set.add(a);
    }
  }
  return [...set];
}

/** 用导入历史里内嵌的 contributions 现算全部对手的 CloudProfile（未登录/离线兜底）。 */
export function buildAllLocalProfiles(history: ImportEntry[]): CloudProfile[] {
  const dedup = dedupByHand(history);
  return opponentAliases(dedup)
    .map((a) => buildLocalCloudProfile(dedup, a))
    .filter((p): p is CloudProfile => p != null);
}

// ---------- 回填：把上传的手牌重新聚合进 Supabase（单一数据源）----------

/** 给 Promise 加超时，避免单个挂起的网络请求把并发 worker 永久卡住。 */
function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

async function mapLimit<T>(items: T[], limit: number, fn: (item: T) => Promise<void>): Promise<void> {
  let i = 0;
  const n = Math.max(1, Math.min(limit, items.length));
  const workers = Array.from({ length: n }, async () => {
    while (i < items.length) {
      const idx = i++;
      await fn(items[idx]);
    }
  });
  await Promise.all(workers);
}

/** 确保每条导入都带 contributions（缺失且有重建时向后端确定性派生）。
 * 返回补全后的历史（新对象）与需要持久化的 {id: item} 补丁。 */
export async function ensureContributions(
  history: ImportEntry[],
): Promise<{ entries: ImportEntry[]; patches: Record<string, IngestItem> }> {
  const patches: Record<string, IngestItem> = {};
  const need = history.filter(
    (e) =>
      e.item?.ok &&
      e.item.recognized !== false &&
      e.item.reconstruction &&
      !e.item.contributions?.players,
  );
  await mapLimit(need, 5, async (e) => {
    try {
      const r = await withTimeout(
        postIngestContributions(
          e.item.facts ?? {},
          e.item.reconstruction ?? null,
          e.item.analysis ?? null,
        ),
        15000,
      );
      patches[e.id] = { ...e.item, contributions: r.contributions };
    } catch {
      /* 单条派生失败/超时不阻塞整体 */
    }
  });
  const entries = history.map((e) => (patches[e.id] ? { ...e, item: patches[e.id] } : e));
  return { entries, patches };
}

/** 回填：把（带 contributions 的）历史整体重新聚合进云端（幂等：按聚合键先撤后加）。
 * 返回实际应用的手数。未登录/未启用返回 0。 */
export async function backfillCloudAggregation(entries: ImportEntry[]): Promise<number> {
  if (!(await cloudReady())) return 0;
  const byKey = new Map<string, ImportEntry>();
  for (const e of entries) {
    const players = e.item?.contributions?.players;
    if (!players || players.length === 0) continue;
    byKey.set(aggKey(e), e); // 同一聚合键去重（同手同贡献，取其一即可）
  }
  let applied = 0;
  const items = [...byKey.entries()];
  await mapLimit(items, 4, async ([key, e]) => {
    try {
      await retractHand(key); // 幂等：无既有贡献则空操作
      await applyHandContributions(key, e.item.contributions!.players);
      applied += 1;
    } catch {
      /* 网络/权限问题静默忽略 */
    }
  });
  return applied;
}

// ---------- 备注 / 标签（本地持久化）----------
export interface OppNote {
  note: string;
  tag: string;
  updated: number;
}
export type OppNotes = Record<string, OppNote>;

export const OPP_TAGS = ["", "鱼/娱乐", "跟注站", "紧弱", "激进/疯", "常规", "危险/强"] as const;

const NOTES_KEY = "poker_opponent_notes_v1";

function isBrowser(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function loadOppNotes(): OppNotes {
  if (!isBrowser()) return {};
  try {
    const raw = window.localStorage.getItem(NOTES_KEY);
    return raw ? (JSON.parse(raw) as OppNotes) : {};
  } catch {
    return {};
  }
}

function persistNotes(notes: OppNotes): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(NOTES_KEY, JSON.stringify(notes));
  } catch {
    /* 忽略配额 */
  }
}

export function saveOppNote(alias: string, patch: { note?: string; tag?: string }): OppNotes {
  const notes = loadOppNotes();
  const prev = notes[alias] ?? { note: "", tag: "", updated: 0 };
  notes[alias] = {
    note: patch.note ?? prev.note,
    tag: patch.tag ?? prev.tag,
    updated: Date.now(),
  };
  persistNotes(notes);
  return notes;
}

// ---------- 剥削报告缓存（本地兜底：离线/未登录也不必每次重算；登录后以 Supabase 为权威）----------
export interface LocalReport {
  report: string;
  model: string | null;
  basedOnHandCount: number;
  createdAt: string;
  sources?: KbSource[];
}
type LocalReports = Record<string, LocalReport>; // key = alias（稳定）
const REPORTS_KEY = "poker_opp_reports_v1";

export function loadLocalReports(): LocalReports {
  if (!isBrowser()) return {};
  try {
    const raw = window.localStorage.getItem(REPORTS_KEY);
    return raw ? (JSON.parse(raw) as LocalReports) : {};
  } catch {
    return {};
  }
}

export function loadLocalReport(alias: string): LocalReport | undefined {
  return loadLocalReports()[alias];
}

export function saveLocalReport(alias: string, row: LocalReport): void {
  if (!isBrowser() || !alias) return;
  const all = loadLocalReports();
  all[alias] = row;
  try {
    window.localStorage.setItem(REPORTS_KEY, JSON.stringify(all));
  } catch {
    /* 忽略配额 */
  }
}

/** 合并云端备注到本地（以 updated 较新者为准），返回合并后的备注表。 */
export function mergeOppNotes(incoming: OppNotes): OppNotes {
  const local = loadOppNotes();
  let changed = false;
  for (const [alias, note] of Object.entries(incoming)) {
    const cur = local[alias];
    if (!cur || (note.updated ?? 0) > (cur.updated ?? 0)) {
      local[alias] = note;
      changed = true;
    }
  }
  if (changed) persistNotes(local);
  return local;
}
