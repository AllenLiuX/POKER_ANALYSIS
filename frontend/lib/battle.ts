// HU 人机对战：API 客户端 + 问题手本地存储。
// 无状态协议：/act 只需回传 {deal_seed, hero_pos, history, action, size}，服务端重放全部状态。
import { API_BASE } from "./api";

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
  // 翻后特有
  made_label?: string;
  draw_label?: string;
  tier?: string;
  equity?: number;
  reasons?: string[];
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
  hero_pos: "BTN" | "BB";
  villain_pos: "BTN" | "BB";
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
export async function newBattle(body?: {
  hero_pos?: "BTN" | "BB";
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
  hero_pos: "BTN" | "BB";
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

// ---------- 问题手（本地存储 + AI 复盘）----------
export interface ProblemHand {
  ts: number;
  hero_glyphs: string[];
  hero_pos: string;
  villain_pos: string;
  board_glyphs: string[];
  hero_net: number;
  reason: string;
  winner: string;
  is_big: boolean;
  max_severity: number;
  decisions: DecisionGrade[];
}

const KEY = "poker_battle_hands_v1";
const MAX = 200;

function isBrowser(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function loadProblemHands(): ProblemHand[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? (arr as ProblemHand[]) : [];
  } catch {
    return [];
  }
}

export function clearProblemHands(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** 从一手结束的对战状态里抽出"问题手"记录（若确为问题手），存入本地并返回最新列表。 */
export function maybeRecordProblemHand(state: BattleState): ProblemHand[] {
  const r = state.result;
  if (!r || !r.review.is_problem) return loadProblemHands();
  const decisions = state.grades.filter(
    (g) => g.grade === "mistake" || g.grade === "acceptable",
  );
  const hand: ProblemHand = {
    ts: Date.now(),
    hero_glyphs: state.hero_glyphs,
    hero_pos: state.hero_pos,
    villain_pos: state.villain_pos,
    board_glyphs: r.board.map(glyph),
    hero_net: r.hero_net,
    reason: r.reason,
    winner: r.winner,
    is_big: r.review.is_big,
    max_severity: r.review.max_severity,
    decisions,
  };
  const list = loadProblemHands();
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

export interface BattleAnalyzeResult {
  report: string;
  analyzed: number;
}

export async function analyzeProblemHands(
  hands: ProblemHand[],
): Promise<BattleAnalyzeResult> {
  const payload = {
    hands: hands.map((h) => ({
      hero_glyphs: h.hero_glyphs,
      hero_pos: h.hero_pos,
      villain_pos: h.villain_pos,
      board_glyphs: h.board_glyphs,
      hero_net: h.hero_net,
      reason: h.reason,
      winner: h.winner,
      decisions: h.decisions.map((d) => ({
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
      })),
    })),
  };
  const res = await fetch(`${API_BASE}/api/battle/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || `battle analyze ${res.status}`);
  }
  return res.json();
}
