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

export { API_BASE };
