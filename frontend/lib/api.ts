const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://127.0.0.1:8000";

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  capabilities: Record<string, boolean>;
}

export interface EquityResponse {
  win: number;
  tie: number;
  lose: number;
  equity: number;
  samples: number;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}

export interface SpotIndexEntry {
  format: string;
  spot: string;
  position: string;
}

export interface RangeCell {
  row: number;
  col: number;
  hand_class: string;
  freqs: Record<string, number>;
}

export interface RangeGrid {
  meta: Record<string, unknown>;
  actions: string[];
  ranks: string[];
  cells: RangeCell[];
}

export async function getRangeIndex(): Promise<SpotIndexEntry[]> {
  const res = await fetch(`${API_BASE}/api/ranges`, { cache: "no-store" });
  if (!res.ok) throw new Error(`ranges ${res.status}`);
  return (await res.json()).spots;
}

export async function getRangeGrid(
  fmt: string,
  spot: string,
  position: string,
): Promise<RangeGrid> {
  const res = await fetch(
    `${API_BASE}/api/ranges/${fmt}/${spot}/${position}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`range grid ${res.status}`);
  return res.json();
}

// ---- Trainer ----
export interface TrainerSeat {
  position: string;
  order: number;
  status: "hero" | "folded" | "waiting" | "raiser";
  is_hero: boolean;
  is_blind: boolean;
}

export interface TrainerScenario {
  id: string;
  format: string;
  spot: string;
  position: string;
  hero_position: string;
  opener_position: string | null;
  facing: { opener_position: string; open_size_bb: number } | null;
  hero: string[];
  hero_glyphs: string[];
  hero_class: string;
  difficulty: string;
  is_critical: boolean;
  effective_stack_bb: number;
  blinds: { sb: number; bb: number };
  pot_bb: number;
  seats: TrainerSeat[];
  available_actions: string[];
  action_labels: Record<string, string>;
  prompt: string;
}

export interface ScoreResult {
  correct: boolean;
  grade: "optimal" | "acceptable" | "mistake";
  chosen: string;
  chosen_freq: number;
  optimal_action: string;
  optimal_freq: number;
  frequencies: Record<string, number>;
  is_mixed: boolean;
  ev_loss_proxy: number;
}

export interface Feedback {
  grade: string;
  headline: string;
  explanation: string;
  tip: string;
}

export interface TrainerAnswer {
  scenario_id: string | null;
  hand_class: string;
  position: string;
  hero_position: string;
  opener_position: string | null;
  spot: string;
  score: ScoreResult;
  feedback: Feedback;
  meta: Record<string, unknown>;
}

export async function getTrainerSpots(
  format?: string,
): Promise<SpotIndexEntry[]> {
  const qs = format ? `?format=${encodeURIComponent(format)}` : "";
  const res = await fetch(`${API_BASE}/api/trainer/spots${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`trainer spots ${res.status}`);
  return (await res.json()).spots;
}

export async function getTrainerNext(params?: {
  format?: string;
  spot?: string;
  position?: string;
  difficulty?: string;
}): Promise<TrainerScenario> {
  const qs = new URLSearchParams();
  if (params?.format) qs.set("format", params.format);
  if (params?.spot) qs.set("spot", params.spot);
  if (params?.position) qs.set("position", params.position);
  if (params?.difficulty) qs.set("difficulty", params.difficulty);
  const url = `${API_BASE}/api/trainer/next${qs.toString() ? `?${qs}` : ""}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `trainer next ${res.status}`);
  }
  return (await res.json()).scenario;
}

export interface TrainerCoach {
  hand_class: string;
  position: string;
  action: string;
  coaching: string;
  action_label: string;
}

export async function postTrainerCoach(body: {
  format: string;
  spot: string;
  position: string;
  hero: string[];
  action: string;
}): Promise<TrainerCoach> {
  const res = await fetch(`${API_BASE}/api/trainer/coach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `trainer coach ${res.status}`);
  }
  return res.json();
}

export async function postTrainerAnswer(body: {
  format: string;
  spot: string;
  position: string;
  hero: string[];
  action: string;
  scenario_id?: string;
}): Promise<TrainerAnswer> {
  const res = await fetch(`${API_BASE}/api/trainer/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `trainer answer ${res.status}`);
  }
  return res.json();
}

export async function postEquity(body: {
  hero: string[];
  villain_range: string;
  board?: string[];
  trials?: number;
}): Promise<EquityResponse> {
  const res = await fetch(`${API_BASE}/api/equity`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `equity ${res.status}`);
  }
  return res.json();
}

// ---------- 翻后训练器 ----------
export interface PostflopTexture {
  board: string[];
  paired: boolean;
  trips: boolean;
  suitedness: string;
  straightiness: number;
  high_card: number;
  high_label: string;
  wetness: number;
  wet_label: string;
  descriptor: string;
}

export interface SizeOption {
  id: string;
  label: string;
  amount_bb: number;
  fraction?: number;
  mult?: number;
}

export interface PostflopScenario {
  id: string;
  mode: string;
  street: string;
  role: "pfr" | "caller";
  format: string;
  config: { pfr: string; caller: string };
  hero_position: string;
  villain_position: string;
  hero: string[];
  hero_glyphs: string[];
  hero_class: string;
  board: string[];
  board_glyphs: string[];
  villain_range: string;
  villain_range_label: string;
  effective_stack_bb: number;
  blinds: { sb: number; bb: number };
  pot_bb: number;
  bet_bb: number | null;
  texture: PostflopTexture;
  available_actions: string[];
  action_labels: Record<string, string>;
  bet_sizes: SizeOption[];
  raise_sizes: SizeOption[];
  prompt: string;
}

export interface PostflopHand {
  made: string;
  made_label: string;
  pair_kind: string | null;
  tier: string;
  draws: string[];
  draw_label: string;
  outs: number;
  combo_draw: boolean;
}

export interface PostflopRecommendation {
  spot: string;
  recommended: string;
  accept: string[];
  mix: boolean;
  equity: number;
  reasons: string[];
  size_advice?: string;
  required_equity?: number;
  mdf?: number;
  wetness?: number;
  pot_bb?: number;
  bet_bb?: number;
}

export interface PostflopScore {
  correct: boolean;
  grade: string;
  chosen: string;
  recommended: string;
  accept: string[];
  mix: boolean;
  size?: string | null;
  recommended_size?: string | null;
  accept_sizes?: string[];
  size_ok?: boolean | null;
  size_label?: string;
  recommended_size_label?: string;
}

export interface PostflopAnswer {
  scenario_id: string | null;
  role: string;
  hero_class: string;
  texture: PostflopTexture;
  hand: PostflopHand;
  equity: number;
  recommendation: PostflopRecommendation;
  score: PostflopScore;
  feedback: { headline: string; explanation: string; tip: string };
  action_label: string;
  approximate: boolean;
}

export async function getPostflopNext(params?: {
  role?: string;
}): Promise<PostflopScenario> {
  const qs = new URLSearchParams();
  if (params?.role) qs.set("role", params.role);
  const url = `${API_BASE}/api/trainer/postflop/next${qs.toString() ? `?${qs}` : ""}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `postflop next ${res.status}`);
  }
  return (await res.json()).scenario;
}

export async function postPostflopAnswer(body: {
  role: string;
  hero: string[];
  board: string[];
  villain_range: string;
  pot_bb: number;
  bet_bb: number | null;
  action: string;
  size?: string;
  scenario_id?: string;
}): Promise<PostflopAnswer> {
  const res = await fetch(`${API_BASE}/api/trainer/postflop/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `postflop answer ${res.status}`);
  }
  return res.json();
}

export interface PostflopCoach {
  role: string;
  action: string;
  coaching: string;
  action_label: string;
}

export async function postPostflopCoach(body: {
  role: string;
  hero: string[];
  board: string[];
  villain_range: string;
  pot_bb: number;
  bet_bb: number | null;
  hero_position: string;
  villain_position: string;
  action: string;
  size?: string;
}): Promise<PostflopCoach> {
  const res = await fetch(`${API_BASE}/api/trainer/postflop/coach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `postflop coach ${res.status}`);
  }
  return res.json();
}

// ---------- 截图导入（Phase 6 · S1：观测提取）----------
export interface IngestPlayerObs {
  seat: number | null;
  alias: string | null;
  position: string | null;
  is_hero: boolean;
  hole_cards: string[];
  stack_end: number | null;
  net: number | null;
  made_hand: string | null;
  actions_raw: string | null;
  visible_actions: string[];
}

export interface ObservationFacts {
  screenshot_type: "hand_replay" | "result_summary" | "unknown";
  hand_id: string | null;
  blinds: string | null;
  board: string[];
  pot: number | null;
  hero_seat: number | null;
  players: IngestPlayerObs[];
  extraction_confidence: number;
  notes: string | null;
}

// ---------- 阶段②：下注序列重建 ----------
export interface ReconstructedAction {
  action: string;
  amount: number | null;
  label: string;
  raw: string;
  street: string | null;
}

export interface ReconstructedPlayer {
  alias: string | null;
  position: string | null;
  is_hero: boolean;
  is_winner: boolean;
  hole_cards: string[];
  net: number | null;
  invested: number;
  parsed_invested: number;
  actions: ReconstructedAction[];
  uncertain: boolean;
}

export interface ReconstructionChecks {
  net_sum: number | null;
  net_ok: boolean;
  invested_sum: number;
  pot: number | null;
  uncertain_count: number;
  rows_consistent: boolean;
}

export interface Reconstruction {
  status: "validated" | "needs_review" | "needs_user";
  confidence: number;
  pot: number | null;
  board: string[];
  players: ReconstructedPlayer[];
  checks: ReconstructionChecks;
  note: string;
}

// ---------- 阶段③：GTO 偏离标注 ----------
export type DeviationGrade = "optimal" | "acceptable" | "mistake";

export interface Deviation {
  alias: string | null;
  is_hero: boolean;
  street: string;
  spot: string; // "RFI" | "vs_RFI" | "postflop"
  spot_label: string;
  // 翻前接地字段
  position?: string | null;
  opener?: string | null;
  hand_class?: string;
  actual?: string;
  actual_label?: string;
  grade?: DeviationGrade;
  grade_label?: string;
  optimal_action?: string;
  optimal_label?: string;
  chosen_freq?: number;
  optimal_freq?: number;
  frequencies?: Record<string, number>;
  is_mixed?: boolean;
  deviation_type?: string | null;
  deviation_label?: string | null;
  ev_loss_proxy?: number;
  off_tree?: boolean;
  // 翻后启发式字段
  made_label?: string;
  draw_label?: string;
  tier?: string;
  grounded: boolean;
  approximate: boolean;
  confidence: number;
  note?: string | null;
}

export interface AnalysisPlayer {
  alias: string | null;
  is_hero: boolean;
  position: string | null;
  hole_cards: string[];
  net: number | null;
  deviations: Deviation[];
}

export interface Analysis {
  supported: boolean;
  format: string;
  players: AnalysisPlayer[];
  counts: { graded: number; grounded: number; mistakes: number };
  note: string;
}

/** 单张截图的处理结果（批量数组里的一项）。 */
export interface IngestItem {
  filename: string;
  ok: boolean;
  error?: string;
  stage?: string;
  recognized?: boolean;
  facts?: ObservationFacts;
  reconstruction?: Reconstruction | null;
  analysis?: Analysis | null;
  raw_model_output?: string;
  note?: string;
}

export interface IngestBatchResult {
  count: number;
  results: IngestItem[];
}

/** 批量上传截图，逐图返回观测事实 + 重建。非图片/解析失败为单图错误，不影响其它图。 */
export async function postIngestExtract(files: File[]): Promise<IngestBatchResult> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  // 后端并发解析（每张走线程池），但视觉模型仍可能较慢；给一个随张数放宽的墙钟上限，
  // 超时给出可操作提示而不是无限转圈。
  const timeoutMs = Math.min(300_000, 30_000 + files.length * 45_000);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}/api/ingest/extract`, {
      method: "POST",
      body: form,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `ingest extract ${res.status}`);
    }
    return res.json();
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(
        `解析超时（>${Math.round(timeoutMs / 1000)}s）。可能是图片较多或模型较慢，请减少单次张数后重试。`,
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/** 阶段③：为已有的观测事实 + 重建结果标注 GTO 偏离（确定性，无 LLM）。用于回填历史。 */
export async function postIngestAnalyze(
  facts: ObservationFacts | Record<string, unknown> | undefined,
  reconstruction: Reconstruction | null | undefined,
): Promise<{ analysis: Analysis }> {
  const res = await fetch(`${API_BASE}/api/ingest/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ facts: facts ?? {}, reconstruction: reconstruction ?? null }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `ingest analyze ${res.status}`);
  }
  return res.json();
}

// ---------- 阶段④：逐对手剥削分析 ----------
export interface ExploitDecision {
  street?: string;
  spot: string;
  spot_label?: string;
  hand_class?: string;
  actual: string;
  optimal_action?: string;
  grade: string;
  deviation_type?: string | null;
  grounded: boolean;
}

export interface ExploitProfileInput {
  alias: string;
  hands: number;
  net?: number | null;
  decisions: ExploitDecision[];
}

export interface ExploitRequestBody {
  opponents: ExploitProfileInput[];
  hero?: ExploitProfileInput | null;
}

export interface ExploitProfileSummary {
  alias: string;
  hands: number;
  net: number | null;
  sample: number;
  mistakes: number;
  accuracy: number | null;
  leaks: Record<string, number>;
  dominant_leak: string | null;
  rfi_count: number;
  defend_count: number;
  fold_vs_open: number;
  threebet_count: number;
}

export interface ExploitResult {
  report: string;
  profiles: ExploitProfileSummary[];
  hero: ExploitProfileSummary | null;
  opponents_analyzed: number;
  note: string;
}

export async function postExploit(body: ExploitRequestBody): Promise<ExploitResult> {
  const res = await fetch(`${API_BASE}/api/ingest/exploit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `ingest exploit ${res.status}`);
  }
  return res.json();
}

// ---------- AI 复盘报告 ----------
export interface ReviewMistake {
  spot: string;
  position: string;
  hero_position?: string | null;
  opener?: string | null;
  hand_class: string;
  action: string;
  optimal_action: string;
}

export interface ReviewRequestBody {
  total: number;
  accuracy: number;
  current_streak: number;
  best_streak: number;
  by_grade: Record<string, number>;
  by_spot: { key: string; total: number; correct: number }[];
  by_position: { key: string; total: number; correct: number }[];
  mistakes: ReviewMistake[];
}

export interface ReviewResult {
  report: string;
  analyzed: number;
  mistakes_considered: number;
}

export async function postTrainerReview(body: ReviewRequestBody): Promise<ReviewResult> {
  const res = await fetch(`${API_BASE}/api/trainer/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `trainer review ${res.status}`);
  }
  return res.json();
}

export { API_BASE };
