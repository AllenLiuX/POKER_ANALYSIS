// Supabase 客户端工厂。凭证缺失时 isSupabaseEnabled=false，全站自动降级为
// 纯本地模式（无登录、进度只存 localStorage）。填好 env 后无需改代码即可点亮。
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
// 兼容两种客户端密钥命名：新版 publishable key（sb_publishable_...）或旧版 anon key。
const anon =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ||
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const isSupabaseEnabled = Boolean(url && anon);

let _client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (!isSupabaseEnabled) return null;
  if (!_client) {
    _client = createClient(url as string, anon as string, {
      auth: { persistSession: true, autoRefreshToken: true },
    });
  }
  return _client;
}
