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
