// attempts 云同步（Supabase）。全部函数在未启用/未登录时安全返回空，不抛错，
// 因此调用方无需关心是否配置了 Supabase。表结构见 supabase/migrations/0001_init.sql。
import { getSupabase } from "./supabase";
import type { Attempt } from "./progress";
import type { RecordedHand } from "./battle";
import type { ContribPlayer, IngestItem, KbSource, OpponentCounters } from "./api";
import type { ImportEntry } from "./importHistory";
import type { OppNote, OppNotes } from "./opponents";

const TABLE = "attempts";
const HANDS_TABLE = "battle_hands";
const IMPORTS_TABLE = "import_entries";
const NOTES_TABLE = "opponent_notes";

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

/** 对账补传：把本地有、云端还没有的手牌一次性写上去（按 ts 去重，幂等）。
 *
 * 用于登录时合并登录前/离线积累的本地记录，或补上曾经写失败的手牌。
 * 因为每手 ts=Date.now() 实际唯一，用 ts 去重即可避免重复插入。返回新写入条数。
 */
export async function syncLocalToCloud(attempts: Attempt[]): Promise<number> {
  const sb = getSupabase();
  if (!sb || attempts.length === 0) return 0;
  const userId = await currentUserId();
  if (!userId) return 0;
  const { data, error: selErr } = await sb
    .from(TABLE)
    .select("ts")
    .eq("user_id", userId)
    .limit(5000);
  if (selErr) return 0;
  const existing = new Set((data ?? []).map((r) => Number((r as { ts: number }).ts)));
  const missing = attempts.filter((a) => !existing.has(a.ts));
  if (missing.length === 0) return 0;
  const { error } = await sb.from(TABLE).insert(missing.map((a) => toRow(userId, a)));
  return error ? 0 : missing.length;
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

// ---------- 对战「对局记录」云同步（battle_hands，表结构见 0002_battle_hands.sql）----------
interface HandRow {
  user_id: string;
  ts: number;
  hero_pos: string;
  hero_net: number;
  is_problem: boolean;
  is_big: boolean;
  data: RecordedHand;
}

function toHandRow(userId: string, h: RecordedHand): HandRow {
  return {
    user_id: userId,
    ts: h.ts,
    hero_pos: h.hero_pos,
    hero_net: h.hero_net,
    is_problem: h.is_problem,
    is_big: h.is_big,
    data: h,
  };
}

/** 单手写入云端（fire-and-forget；未登录/未启用则静默跳过）。 */
export async function pushHand(h: RecordedHand): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(HANDS_TABLE).insert(toHandRow(userId, h));
}

/** 对账补传：把本地有、云端还没有的对局记录一次性写上去（按 ts 去重，幂等）。返回新写入条数。 */
export async function syncLocalHandsToCloud(hands: RecordedHand[]): Promise<number> {
  const sb = getSupabase();
  if (!sb || hands.length === 0) return 0;
  const userId = await currentUserId();
  if (!userId) return 0;
  const { data, error: selErr } = await sb
    .from(HANDS_TABLE)
    .select("ts")
    .eq("user_id", userId)
    .limit(5000);
  if (selErr) return 0;
  const existing = new Set((data ?? []).map((r) => Number((r as { ts: number }).ts)));
  const missing = hands.filter((h) => !existing.has(h.ts));
  if (missing.length === 0) return 0;
  const { error } = await sb.from(HANDS_TABLE).insert(missing.map((h) => toHandRow(userId, h)));
  return error ? 0 : missing.length;
}

/** 拉取当前用户的云端对局记录（按时间升序，上限 2000）。 */
export async function fetchCloudHands(): Promise<RecordedHand[]> {
  const sb = getSupabase();
  if (!sb) return [];
  const userId = await currentUserId();
  if (!userId) return [];
  const { data, error } = await sb
    .from(HANDS_TABLE)
    .select("data")
    .eq("user_id", userId)
    .order("ts", { ascending: true })
    .limit(2000);
  if (error || !data) return [];
  return (data as { data: RecordedHand }[]).map((r) => r.data).filter(Boolean);
}

/** 清空当前用户云端对局记录。 */
export async function clearCloudHands(): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(HANDS_TABLE).delete().eq("user_id", userId);
}

// ---------- 截图导入历史云同步（import_entries，表结构见 0003_import_entries.sql）----------
interface ImportRow {
  user_id: string;
  entry_id: string;
  ts: number;
  filename: string;
  thumb: string | null;
  data: IngestItem;
}

function toImportRow(userId: string, e: ImportEntry): ImportRow {
  return {
    user_id: userId,
    entry_id: e.id,
    ts: e.ts,
    filename: e.filename,
    thumb: e.thumb,
    data: e.item,
  };
}

function fromImportRow(r: ImportRow): ImportEntry {
  return {
    id: r.entry_id,
    ts: Number(r.ts),
    filename: r.filename,
    thumb: r.thumb ?? null,
    item: r.data,
  };
}

/** 写入/更新单条导入记录（upsert；未登录/未启用则静默跳过）。 */
export async function upsertImportEntry(e: ImportEntry): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(IMPORTS_TABLE).upsert(toImportRow(userId, e), { onConflict: "user_id,entry_id" });
}

/** 对账补传：把本地导入记录整体 upsert 到云端（幂等，条数≤本地上限）。返回写入条数。 */
export async function syncLocalImportsToCloud(entries: ImportEntry[]): Promise<number> {
  const sb = getSupabase();
  if (!sb || entries.length === 0) return 0;
  const userId = await currentUserId();
  if (!userId) return 0;
  const { error } = await sb
    .from(IMPORTS_TABLE)
    .upsert(entries.map((e) => toImportRow(userId, e)), { onConflict: "user_id,entry_id" });
  return error ? 0 : entries.length;
}

/** 拉取当前用户的云端导入记录（按时间降序，上限 200）。 */
export async function fetchCloudImports(): Promise<ImportEntry[]> {
  const sb = getSupabase();
  if (!sb) return [];
  const userId = await currentUserId();
  if (!userId) return [];
  const { data, error } = await sb
    .from(IMPORTS_TABLE)
    .select("*")
    .eq("user_id", userId)
    .order("ts", { ascending: false })
    .limit(200);
  if (error || !data) return [];
  return (data as ImportRow[]).map(fromImportRow).filter((e) => Boolean(e.item));
}

/** 删除云端单条导入记录。 */
export async function deleteCloudImport(entryId: string): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(IMPORTS_TABLE).delete().eq("user_id", userId).eq("entry_id", entryId);
}

/** 清空当前用户云端导入记录。 */
export async function clearCloudImports(): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(IMPORTS_TABLE).delete().eq("user_id", userId);
}

// ---------- 对手备注/标签云同步（opponent_notes，表结构见 0004_opponent_notes.sql）----------
interface NoteRow {
  user_id: string;
  alias: string;
  note: string;
  tag: string;
  updated_at: number;
}

/** 写入/更新单个对手备注（upsert；未登录/未启用则静默跳过）。 */
export async function upsertOpponentNote(alias: string, n: OppNote): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb
    .from(NOTES_TABLE)
    .upsert(
      { user_id: userId, alias, note: n.note, tag: n.tag, updated_at: n.updated },
      { onConflict: "user_id,alias" },
    );
}

/** 对账补传：把本地备注整体 upsert 到云端（幂等）。返回写入条数。 */
export async function syncLocalOppNotesToCloud(notes: OppNotes): Promise<number> {
  const sb = getSupabase();
  const entries = Object.entries(notes);
  if (!sb || entries.length === 0) return 0;
  const userId = await currentUserId();
  if (!userId) return 0;
  const rows: NoteRow[] = entries.map(([alias, n]) => ({
    user_id: userId, alias, note: n.note, tag: n.tag, updated_at: n.updated,
  }));
  const { error } = await sb.from(NOTES_TABLE).upsert(rows, { onConflict: "user_id,alias" });
  return error ? 0 : rows.length;
}

/** 拉取当前用户的云端对手备注（返回 alias→note 映射）。 */
export async function fetchCloudOppNotes(): Promise<OppNotes> {
  const sb = getSupabase();
  if (!sb) return {};
  const userId = await currentUserId();
  if (!userId) return {};
  const { data, error } = await sb.from(NOTES_TABLE).select("*").eq("user_id", userId).limit(2000);
  if (error || !data) return {};
  const out: OppNotes = {};
  for (const r of data as NoteRow[]) {
    out[r.alias] = { note: r.note ?? "", tag: r.tag ?? "", updated: Number(r.updated_at) || 0 };
  }
  return out;
}

// ---------- 逐对手权威聚合画像（Phase A，表/RPC 见 0005_opponent_profiles.sql）----------
const OPP_TABLE = "opponents";
const OPP_STATS_TABLE = "opponent_stats";
const OPP_REPORTS_TABLE = "opponent_reports";

/** 云端是否可用且已登录（对手画像走服务端权威聚合，需登录）。 */
export async function cloudReady(): Promise<boolean> {
  return Boolean(getSupabase()) && Boolean(await currentUserId());
}

/** 幂等增量：把一手的逐对手贡献并入云端聚合（同手同对手只计一次）。 */
export async function applyHandContributions(handId: string, players: ContribPlayer[]): Promise<void> {
  const sb = getSupabase();
  if (!sb || !handId || players.length === 0) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.rpc("apply_hand_contributions", { p_hand_id: handId, p_players: players });
}

/** 回滚一手（编辑/删除前调用）：从聚合中扣除该手贡献。 */
export async function retractHand(handId: string): Promise<void> {
  const sb = getSupabase();
  if (!sb || !handId) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.rpc("retract_hand", { p_hand_id: handId });
}

export interface OpponentAggregateRow {
  opponentId: string;
  alias: string;
  handCount: number;
  net: number;
  counters: OpponentCounters;
  updatedAt: number;
}

/** 拉取当前用户的对手聚合（opponents ⨝ opponent_stats）。 */
export async function fetchOpponentAggregates(): Promise<OpponentAggregateRow[]> {
  const sb = getSupabase();
  if (!sb) return [];
  const userId = await currentUserId();
  if (!userId) return [];
  const [{ data: opps }, { data: stats }] = await Promise.all([
    sb.from(OPP_TABLE).select("opponent_id,primary_alias").eq("user_id", userId).limit(5000),
    sb.from(OPP_STATS_TABLE).select("*").eq("user_id", userId).limit(5000),
  ]);
  if (!opps || !stats) return [];
  const aliasById = new Map<string, string>();
  for (const o of opps as { opponent_id: string; primary_alias: string }[]) {
    aliasById.set(o.opponent_id, o.primary_alias);
  }
  const out: OpponentAggregateRow[] = [];
  for (const s of stats as {
    opponent_id: string; hand_count: number; net: number; counters: OpponentCounters; updated_at: number;
  }[]) {
    out.push({
      opponentId: s.opponent_id,
      alias: aliasById.get(s.opponent_id) ?? "(未知)",
      handCount: Number(s.hand_count) || 0,
      net: Number(s.net) || 0,
      counters: s.counters,
      updatedAt: Number(s.updated_at) || 0,
    });
  }
  return out;
}

export interface OpponentReportRow {
  opponentId: string;
  report: string;
  model: string | null;
  basedOnHandCount: number;
  createdAt: string;
  sources?: KbSource[]; // 报告接地的知识库来源（存于 stats_snapshot.sources）
}

/** 拉取当前用户的所有对手报告（opponent_id → report）。 */
export async function fetchOpponentReports(): Promise<Record<string, OpponentReportRow>> {
  const sb = getSupabase();
  if (!sb) return {};
  const userId = await currentUserId();
  if (!userId) return {};
  const { data, error } = await sb.from(OPP_REPORTS_TABLE).select("*").eq("user_id", userId).limit(5000);
  if (error || !data) return {};
  const out: Record<string, OpponentReportRow> = {};
  for (const r of data as {
    opponent_id: string; report: string; model: string | null; based_on_hand_count: number;
    created_at: string; stats_snapshot: { sources?: KbSource[] } | null;
  }[]) {
    const snap = r.stats_snapshot;
    out[r.opponent_id] = {
      opponentId: r.opponent_id,
      report: r.report,
      model: r.model,
      basedOnHandCount: Number(r.based_on_hand_count) || 0,
      createdAt: r.created_at,
      sources: snap && Array.isArray(snap.sources) ? snap.sources : undefined,
    };
  }
  return out;
}

/** 写入/更新单个对手的剥削报告（按样本量记录，供显著性门控）。sources 一并落库供复现。 */
export async function upsertOpponentReport(
  opponentId: string,
  report: string,
  model: string,
  basedOnHandCount: number,
  counters?: unknown,
  sources?: KbSource[],
): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await sb.from(OPP_REPORTS_TABLE).upsert(
    {
      user_id: userId,
      opponent_id: opponentId,
      report,
      model,
      based_on_hand_count: basedOnHandCount,
      stats_snapshot: { counters: counters ?? null, sources: sources ?? [] },
    },
    { onConflict: "user_id,opponent_id" },
  );
}

/** 清空当前用户的对手画像（聚合 + 索引 + 报告 + 身份）。 */
export async function clearOpponentProfiles(): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;
  const userId = await currentUserId();
  if (!userId) return;
  await Promise.all([
    sb.from(OPP_STATS_TABLE).delete().eq("user_id", userId),
    sb.from("opponent_hand_index").delete().eq("user_id", userId),
    sb.from(OPP_REPORTS_TABLE).delete().eq("user_id", userId),
  ]);
  await sb.from(OPP_TABLE).delete().eq("user_id", userId);
}
