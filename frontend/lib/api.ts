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
  status: "hero" | "folded" | "waiting";
  is_hero: boolean;
  is_blind: boolean;
}

export interface TrainerScenario {
  id: string;
  format: string;
  spot: string;
  position: string;
  hero: string[];
  hero_glyphs: string[];
  hero_class: string;
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
  spot: string;
  score: ScoreResult;
  feedback: Feedback;
  meta: Record<string, unknown>;
}

export async function getTrainerNext(params?: {
  format?: string;
  spot?: string;
  position?: string;
}): Promise<TrainerScenario> {
  const qs = new URLSearchParams();
  if (params?.format) qs.set("format", params.format);
  if (params?.spot) qs.set("spot", params.spot);
  if (params?.position) qs.set("position", params.position);
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

export { API_BASE };
