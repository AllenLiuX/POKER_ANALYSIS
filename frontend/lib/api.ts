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
