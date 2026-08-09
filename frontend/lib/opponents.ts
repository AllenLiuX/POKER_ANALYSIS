// 对手档案：把「截图导入历史」按对手昵称跨手聚合成持久画像（HUD 风格），
// 并本地持久化每个对手的备注/标签（云端同步见 cloud.ts opponent_notes）。
import type { Deviation, OpponentCounters } from "./api";
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
  if (total < 4) return "样本不足";
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
const BASE = {
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

/** 由 HUD + 漏洞推断画像标签（样本门控 + 收缩后频率）。 */
function classifyCloud(p: CloudProfile): string {
  if (p.hands < 4) return "样本不足";
  const s = p.vsOpen;
  // 优先看强信号（有足够样本）
  if (s.n >= 6 && (s.call.shrunk ?? 0) > 0.45) return "跟注站 · 过松";
  if (afTotalOK(p) && (p.afPost.shrunk ?? 0) > 0.62) return "激进 · 爱诈唬";
  if (s.n >= 6 && (s.fold.shrunk ?? 0) > 0.72) return "过紧 · 怕事";
  if (p.foldVsCbet.n >= 4 && (p.foldVsCbet.shrunk ?? 0) > 0.62) return "翻牌易弃 · 可多偷";
  if (p.wtsd.n >= 6 && (p.wtsd.shrunk ?? 0) > 0.42) return "爱看摊牌 · 黏";
  // 退回接地漏洞
  const top = Object.entries(p.leaks).sort((a, b) => b[1] - a[1])[0];
  const map: Record<string, string> = {
    too_loose: "跟注站 · 过松", too_tight: "过紧 · 怕事",
    too_passive: "被动", too_aggressive: "激进 · 爱诈唬", line_error: "线路混乱",
  };
  if (top && top[1] > 0) return map[top[0]] ?? "有明显漏洞";
  if (p.sample < 6) return "样本不足";
  return "接近均衡（样本内）";
}

function afTotalOK(p: CloudProfile): boolean {
  return p.afPost.n >= 6;
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
