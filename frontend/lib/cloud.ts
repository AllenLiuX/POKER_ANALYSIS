// attempts 云同步（Supabase）。全部函数在未启用/未登录时安全返回空，不抛错，
// 因此调用方无需关心是否配置了 Supabase。表结构见 supabase/migrations/0001_init.sql。
import { getSupabase } from "./supabase";
import type { Attempt } from "./progress";

const TABLE = "attempts";

interface Row {
  user_id: string;
  ts: number;
  spot: string;
  position: string;
  hero_position: string;
  opener: string | null;
  hand_class: string;
  action: string;
  optimal_action: string;
  grade: string;
  correct: boolean;
}

function toRow(userId: string, a: Attempt): Row {
  return {
    user_id: userId,
    ts: a.ts,
    spot: a.spot,
    position: a.position,
    hero_position: a.heroPosition,
    opener: a.opener,
    hand_class: a.handClass,
    action: a.action,
    optimal_action: a.optimalAction,
    grade: a.grade,
    correct: a.correct,
  };
}

function fromRow(r: Row): Attempt {
  return {
    ts: Number(r.ts),
    spot: r.spot,
    position: r.position,
    heroPosition: r.hero_position,
    opener: r.opener,
    handClass: r.hand_class,
    action: r.action,
    optimalAction: r.optimal_action,
    grade: r.grade as Attempt["grade"],
    correct: r.correct,
  };
}

async function currentUserId(): Promise<string | null> {
  const sb = getSupabase();
  if (!sb) return null;
  const { data } = await sb.auth.getUser();
  return data.user?.id ?? null;
}

/** 单手写入云端（fire-and-forget 调用即可；未登录/未启用则静默跳过）。 */
export async function pushAttempt(a: Attempt): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(TABLE).insert(toRow(userId, a));
}

/** 批量上传（用于把本地历史一次性同步到云端）。返回写入条数。 */
export async function pushAttempts(attempts: Attempt[]): Promise<number> {
  const sb = getSupabase();
  if (!sb || attempts.length === 0) return 0;
  const userId = await currentUserId();
  if (!userId) return 0;
  const rows = attempts.map((a) => toRow(userId, a));
  const { error } = await sb.from(TABLE).insert(rows);
  return error ? 0 : rows.length;
}

/** 拉取当前用户的云端记录（按时间升序，上限 2000）。 */
export async function fetchCloudAttempts(): Promise<Attempt[]> {
  const sb = getSupabase();
  if (!sb) return [];
  const userId = await currentUserId();
  if (!userId) return [];
  const { data, error } = await sb
    .from(TABLE)
    .select("*")
    .eq("user_id", userId)
    .order("ts", { ascending: true })
    .limit(2000);
  if (error || !data) return [];
  return (data as Row[]).map(fromRow);
}

/** 清空当前用户云端记录。 */
export async function clearCloudAttempts(): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(TABLE).delete().eq("user_id", userId);
}
