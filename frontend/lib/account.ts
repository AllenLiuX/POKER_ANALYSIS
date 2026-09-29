import { getSupabase } from "./supabase";

export interface WpkMe {
  user_id: string;
  email: string | null;
  is_admin: boolean;
  hands: number;
}

export interface OwnedHand {
  hand_id: string;
  hand_number: number | null;
  played_at: string | null;
  game_mode: string;
  status: string;
  pot: number | null;
  board_json: string;
  quality_status: string;
}

export interface OwnedOpponent {
  user_id: string;
  alias: string | null;
  hands: number;
}

export interface AdminOverview {
  hands: number;
  profiles: {
    id: string;
    email: string | null;
    is_admin: boolean;
    created_at: string;
    hands: number;
  }[];
  by_owner: { owner_email: string; hands: number }[];
}

async function rpc<T>(fn: string, args?: Record<string, unknown>): Promise<T> {
  const client = getSupabase();
  if (!client) throw new Error("Supabase 未配置");
  const { data, error } = await client.rpc(fn, args);
  if (error) throw new Error(error.message);
  return data as T;
}

export function fetchWpkMe(): Promise<WpkMe> {
  return rpc<WpkMe>("wpk_me");
}

export function fetchMyHands(limit = 50): Promise<OwnedHand[]> {
  return rpc<OwnedHand[]>("my_hands", { lim: limit });
}

export function fetchMyOpponents(limit = 50): Promise<OwnedOpponent[]> {
  return rpc<OwnedOpponent[]>("my_opponents", { lim: limit });
}

export function fetchAdminOverview(): Promise<AdminOverview> {
  return rpc<AdminOverview>("admin_overview");
}
