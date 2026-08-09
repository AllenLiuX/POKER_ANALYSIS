// HU 人机对战：API 客户端 + 问题手本地存储。
// 无状态协议：/act 只需回传 {deal_seed, hero_pos, history, action, size}，服务端重放全部状态。
import { API_BASE, streamText } from "./api";

// ---------- 类型（对齐后端 engine.to_public / grade）----------
export interface BattleSizeOption {
  id: string;
  label: string;
  amount_bb: number;
}

/** 每个英雄决策的判分（翻前/翻后字段并集，按需出现）。 */
export interface DecisionGrade {
  street: string;
  spot?: string;
  spot_label: string;
  hand_class?: string;
  action: string;
  size?: string | null;
  optimal_action: string | null;
  grade: "optimal" | "acceptable" | "mistake" | "ungraded";
  correct?: boolean | null;
  severity?: number;
  ev_loss_proxy?: number;
  grounded?: boolean;
  // 翻前特有：该手牌类别在此 spot 的动作频率（GTO 范围表，权威）
  frequencies?: Record<string, number>;
  // 翻后特有
  made_label?: string;
  draw_label?: string;
  tier?: string;
  equity?: number;
  reasons?: string[];
  // 翻后建议详情（accept / 需要胜率 / MDF / 范围·坚果优势标签等）
  recommendation?: {
    recommended?: string;
    accept?: string[];
    required_equity?: number | null;
    mdf?: number | null;
    range_label?: string;
    nut_label?: string;
    size_advice?: string;
  };
}

export interface HistoryEvent {
  actor: "hero" | "villain";
  pos: string;
  street: string;
  action: string;
  size: string | null;
  amount_to: number;
  label: string;
  hero_grade?: DecisionGrade;
}

export interface BattleReview {
  is_problem: boolean;
  is_big: boolean;
  mistakes: number;
  max_severity: number;
  hero_net: number;
}

export interface BattleResult {
  reason: "fold" | "showdown";
  winner: "hero" | "villain" | "split";
  hero_net: number;
  pot_bb: number;
  villain: string[];
  villain_glyphs: string[];
  villain_class: string;
  board: string[];
  review: BattleReview;
}

export interface BattleState {
  deal_seed: number;
  matchup: string;
  matchup_label: string;
  hero_pos: string;
  villain_pos: string;
  opener_pos: string;
  defender_pos: string;
  blinds: { sb: number; bb: number };
  start_stack: number;
  street: "preflop" | "flop" | "turn" | "river";
  board: string[];
  board_glyphs: string[];
  hero: string[];
  hero_glyphs: string[];
  hero_class: string;
  pot_bb: number;
  to_call_bb: number;
  hero_stack_bb: number;
  villain_stack_bb: number;
  to_act: "hero" | null;
  complete: boolean;
  available_actions: string[];
  action_labels: Record<string, string>;
  bet_sizes: BattleSizeOption[];
  raise_sizes: BattleSizeOption[];
  history: HistoryEvent[];
  villain_last: HistoryEvent | null;
  message: string;
  grades: DecisionGrade[];
  result: BattleResult | null;
}

// ---------- API ----------
export interface BattleMatchup {
  matchup: string;
  label: string;
  opener: string;
  defender: string;
  positions: string[];
}

export async function loadBattleMatchups(): Promise<BattleMatchup[]> {
  const res = await fetch(`${API_BASE}/api/battle/matchups`, { cache: "no-store" });
  if (!res.ok) throw new Error(`battle matchups ${res.status}`);
  return (await res.json()).matchups;
}

export async function newBattle(body?: {
  matchup?: string;
  hero_pos?: string;
  seed?: number;
}): Promise<BattleState> {
  const res = await fetch(`${API_BASE}/api/battle/new`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `battle new ${res.status}`);
  }
  return (await res.json()).state;
}

export async function battleAct(body: {
  deal_seed: number;
  matchup: string;
  hero_pos: string;
  history: HistoryEvent[];
  action: string;
  size?: string;
}): Promise<BattleState> {
  const res = await fetch(`${API_BASE}/api/battle/act`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `battle act ${res.status}`);
  }
  return (await res.json()).state;
}

// ---------- 对局记录（本地存储 + AI 复盘）----------
/** 一手完整对局记录（无论是否问题手都保存，便于随时问 AI）。 */
export interface RecordedHand {
  ts: number;
  hero_glyphs: string[];
  hero_pos: string;
  villain_pos: string;
  board_glyphs: string[];
  villain_glyphs: string[]; // 仅摊牌可见
  villain_class: string; // 仅摊牌可见
  hero_net: number;
  reason: string;
  winner: string;
  is_problem: boolean;
  is_big: boolean;
  max_severity: number;
  decisions: DecisionGrade[]; // 本手全部英雄决策（含最优/可接受/偏离）
  history: HistoryEvent[]; // 完整行动线（双方、分街）
}

const ACTOR_CN: Record<string, string> = { hero: "你", villain: "对手" };
const STREET_CN: Record<string, string> = {
  preflop: "翻前",
  flop: "翻牌",
  turn: "转牌",
  river: "河牌",
};

/** 把一手的完整历史整理成「分街行动线」，用于展示与喂给 AI。 */
export function actionLineFromHistory(history: HistoryEvent[]): { street: string; text: string }[] {
  const order = ["preflop", "flop", "turn", "river"];
  const groups: Record<string, string[]> = {};
  for (const e of history ?? []) {
    const who = ACTOR_CN[e.actor] ?? e.actor;
    let act = e.label ?? e.action;
    if ((e.action === "bet" || e.action === "raise") && e.amount_to) {
      act = `${act} ${e.amount_to}`;
    }
    (groups[e.street] ??= []).push(`${who}${act}`);
  }
  return order
    .filter((s) => groups[s]?.length)
    .map((s) => ({ street: STREET_CN[s] ?? s, text: groups[s].join(" · ") }));
}

export function handActionLine(h: RecordedHand): { street: string; text: string }[] {
  return actionLineFromHistory(h.history ?? []);
}

/** 兼容旧命名：分析负载复用同一结构。 */
export type ProblemHand = RecordedHand;

const KEY = "poker_battle_history_v1";
const OLD_KEY = "poker_battle_hands_v1"; // 旧版仅存问题手，加载时迁移
const MAX = 300;

function isBrowser(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

/** 读取全部对局记录（首次会迁移旧「问题手」存储）。 */
export function loadHands(): RecordedHand[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? (arr as RecordedHand[]) : [];
    }
    const old = window.localStorage.getItem(OLD_KEY);
    if (old) {
      const arr = JSON.parse(old);
      if (Array.isArray(arr)) {
        const migrated = (arr as Record<string, unknown>[]).map((h) => ({
          villain_glyphs: [],
          villain_class: "",
          is_problem: true,
          history: [],
          ...h,
        })) as unknown as RecordedHand[];
        window.localStorage.setItem(KEY, JSON.stringify(migrated));
        return migrated;
      }
    }
    return [];
  } catch {
    return [];
  }
}

/** 合并外部（云端）记录到本地存储，按 ts 去重，返回合并后的最新列表。 */
export function mergeHands(incoming: RecordedHand[]): RecordedHand[] {
  const list = loadHands();
  const seen = new Set(list.map((h) => h.ts));
  let changed = false;
  for (const h of incoming) {
    if (h && !seen.has(h.ts)) {
      list.push(h);
      seen.add(h.ts);
      changed = true;
    }
  }
  list.sort((a, b) => a.ts - b.ts);
  const trimmed = list.length > MAX ? list.slice(list.length - MAX) : list;
  if (changed && isBrowser()) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(trimmed));
    } catch {
      /* ignore quota */
    }
  }
  return trimmed;
}

export function clearHands(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(KEY);
    window.localStorage.removeItem(OLD_KEY);
  } catch {
    /* ignore */
  }
}

/** 一手结束后记录（每手都存），返回最新列表。 */
export function recordHand(state: BattleState): RecordedHand[] {
  const r = state.result;
  if (!r) return loadHands();
  const showdown = r.reason === "showdown";
  const hand: RecordedHand = {
    ts: Date.now(),
    hero_glyphs: state.hero_glyphs,
    hero_pos: state.hero_pos,
    villain_pos: state.villain_pos,
    board_glyphs: r.board.map(glyph),
    villain_glyphs: showdown ? r.villain.map(glyph) : [],
    villain_class: showdown ? r.villain_class : "",
    hero_net: r.hero_net,
    reason: r.reason,
    winner: r.winner,
    is_problem: r.review.is_problem,
    is_big: r.review.is_big,
    max_severity: r.review.max_severity,
    decisions: state.grades,
    history: state.history,
  };
  const list = loadHands();
  list.push(hand);
  const trimmed = list.length > MAX ? list.slice(list.length - MAX) : list;
  if (isBrowser()) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(trimmed));
    } catch {
      /* ignore quota */
    }
  }
  return trimmed;
}

const SUIT_GLYPH: Record<string, string> = { s: "♠", h: "♥", d: "♦", c: "♣" };
function glyph(card: string): string {
  return `${card[0]}${SUIT_GLYPH[card[1]?.toLowerCase()] ?? card[1] ?? ""}`;
}

function mapDecision(d: DecisionGrade) {
  return {
    street: d.street,
    spot_label: d.spot_label,
    hand_class: d.hand_class ?? "",
    action: d.action,
    optimal_action: d.optimal_action,
    grade: d.grade,
    made_label: d.made_label,
    draw_label: d.draw_label,
    tier: d.tier,
    equity: d.equity,
    reasons: d.reasons ?? [],
  };
}

function handPayload(h: RecordedHand) {
  return {
    hero_glyphs: h.hero_glyphs,
    hero_pos: h.hero_pos,
    villain_pos: h.villain_pos,
    board_glyphs: h.board_glyphs,
    villain_glyphs: h.villain_glyphs ?? [],
    villain_class: h.villain_class ?? "",
    hero_net: h.hero_net,
    reason: h.reason,
    winner: h.winner,
    action_line: handActionLine(h).map((x) => `${x.street} ${x.text}`),
    decisions: h.decisions.map(mapDecision),
  };
}

/** 批量复盘（问题手）：流式，onChunk 收到累计全文，返回最终全文。 */
export async function streamAnalyzeProblemHands(
  hands: RecordedHand[],
  onChunk: (full: string) => void,
): Promise<string> {
  return streamText(
    `${API_BASE}/api/battle/analyze/stream`,
    { hands: hands.map(handPayload) },
    onChunk,
  );
}

/** 单手复盘：问 AI「这手打得对不对、为什么」。流式返回。 */
export async function streamExplainHand(
  hand: RecordedHand,
  onChunk: (full: string) => void,
): Promise<string> {
  return streamText(
    `${API_BASE}/api/battle/explain/stream`,
    { hand: handPayload(hand) },
    onChunk,
  );
}
