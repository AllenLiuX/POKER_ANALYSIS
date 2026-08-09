// 截图导入历史的本地持久化（localStorage）。
// 存已解析的结果（观测事实 + 重建）+ 一张缩略图，刷新后仍在。
// 缩略图做过降采样（JPEG），单条约 15~30KB；上限 MAX 条以约束占用。

import type { IngestItem } from "./api";

export interface ImportEntry {
  id: string;
  ts: number;
  filename: string;
  thumb: string | null; // data URL（降采样 JPEG）
  item: IngestItem;
}

const KEY = "poker_import_history_v1";
const MAX = 40;

function isBrowser(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function loadImportHistory(): ImportEntry[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? (arr as ImportEntry[]) : [];
  } catch {
    return [];
  }
}

function persist(list: ImportEntry[]): void {
  if (!isBrowser()) return;
  const capped = list.slice(0, MAX);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(capped));
  } catch {
    // 配额超限：逐步丢弃最旧条目的缩略图 / 再截断后重试
    try {
      const lite = capped.map((e, i) => (i < 8 ? e : { ...e, thumb: null }));
      window.localStorage.setItem(KEY, JSON.stringify(lite));
    } catch {
      try {
        window.localStorage.setItem(KEY, JSON.stringify(capped.slice(0, 10)));
      } catch {
        // 放弃写入
      }
    }
  }
}

/** 手牌去重签名：优先用 hand_id（可靠）；无 hand_id 则不去重（返回 null）。 */
export function handSignature(e: ImportEntry): string | null {
  const hid = e.item?.facts?.hand_id;
  return hid && hid.trim() ? `hid:${hid.trim()}` : null;
}

/** 把新解析的若干条前置到历史（最新在前），返回更新后的列表。
 * 若新导入与历史中某条 hand_id 相同（重复导入同一手），用新的替换旧的，避免统计重复计数。 */
export function addImportEntries(entries: ImportEntry[]): ImportEntry[] {
  const existing = loadImportHistory();
  const incomingSigs = new Set<string>();
  for (const e of entries) {
    const s = handSignature(e);
    if (s) incomingSigs.add(s);
  }
  const kept = existing.filter((e) => {
    const s = handSignature(e);
    return !(s && incomingSigs.has(s));
  });
  const next = [...entries, ...kept].slice(0, MAX);
  persist(next);
  return next;
}

export function removeImportEntry(id: string): ImportEntry[] {
  const next = loadImportHistory().filter((e) => e.id !== id);
  persist(next);
  return next;
}

/** 用 id→IngestItem 的补丁批量更新历史条目的 item（如回填阶段③偏离标注），并持久化。 */
export function patchImportItems(patches: Record<string, IngestItem>): ImportEntry[] {
  const next = loadImportHistory().map((e) => (patches[e.id] ? { ...e, item: patches[e.id] } : e));
  persist(next);
  return next;
}

/** 合并外部（云端）导入记录到本地，按 id 去重，返回合并后的最新列表（最新在前）。 */
export function mergeImportEntries(incoming: ImportEntry[]): ImportEntry[] {
  const list = loadImportHistory();
  const seen = new Set(list.map((e) => e.id));
  let changed = false;
  for (const e of incoming) {
    if (e && !seen.has(e.id)) {
      list.push(e);
      seen.add(e.id);
      changed = true;
    }
  }
  list.sort((a, b) => b.ts - a.ts);
  const capped = list.slice(0, MAX);
  if (changed) persist(capped);
  return capped;
}

export function clearImportHistory(): ImportEntry[] {
  if (isBrowser()) {
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      // 忽略
    }
  }
  return [];
}

/** 生成降采样缩略图 data URL；失败返回 null（不阻塞主流程）。
 * 默认 720px 长边：列表里仍显示很小，但点开放大后手牌回放的文字/动作可读。 */
export async function makeThumb(file: File, max = 720): Promise<string | null> {
  if (typeof window === "undefined" || typeof createImageBitmap !== "function") return null;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close?.();
    return canvas.toDataURL("image/jpeg", 0.72);
  } catch {
    return null;
  }
}

// ---------- 导出 ----------
function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 完整历史（观测事实 + 重建 + 偏离）导出为 JSON 字符串。 */
export function exportHistoryJSON(list: ImportEntry[]): string {
  return JSON.stringify(
    list.map((e) => ({
      id: e.id,
      ts: e.ts,
      filename: e.filename,
      facts: e.item?.facts ?? null,
      reconstruction: e.item?.reconstruction ?? null,
      analysis: e.item?.analysis ?? null,
    })),
    null,
    2,
  );
}

/** 把每手里的每个「接地/近似」决策拍平成一行 CSV（便于导入表格/透视分析）。 */
export function exportDecisionsCSV(list: ImportEntry[]): string {
  const header = [
    "time", "filename", "hand_id", "type", "board", "pot",
    "player", "is_hero", "position", "street", "spot", "hand_class",
    "actual", "grade", "optimal", "deviation", "equity", "net",
  ];
  const rows: string[][] = [header];
  for (const e of list) {
    const it = e.item;
    if (!it?.ok || !it.analysis?.supported) continue;
    const f = it.facts;
    const time = new Date(e.ts).toISOString();
    const board = (f?.board ?? []).join(" ");
    for (const pl of it.analysis.players) {
      for (const d of pl.deviations) {
        rows.push([
          time,
          e.filename,
          f?.hand_id ?? "",
          f?.screenshot_type ?? "",
          board,
          f?.pot != null ? String(f.pot) : "",
          pl.alias ?? "",
          pl.is_hero ? "1" : "0",
          pl.position ?? d.position ?? "",
          d.street ?? "",
          d.spot ?? "",
          d.hand_class ?? d.made_label ?? "",
          d.actual_label ?? d.actual ?? "",
          d.grade_label ?? d.grade ?? "",
          d.optimal_label ?? d.optimal_action ?? "",
          d.deviation_label ?? "",
          typeof d.equity === "number" ? String(Math.round(d.equity * 100) / 100) : "",
          pl.net != null ? String(pl.net) : "",
        ].map(csvCell));
      }
    }
  }
  return rows.map((r) => r.join(",")).join("\n");
}

/** 触发浏览器下载。 */
export function downloadText(filename: string, content: string, mime = "text/plain"): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function genId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** 由后端结果 + 原文件构造历史条目（含缩略图）。 */
export async function buildEntry(item: IngestItem, file: File | null): Promise<ImportEntry> {
  return {
    id: genId(),
    ts: Date.now(),
    filename: item.filename,
    thumb: file ? await makeThumb(file) : null,
    item,
  };
}
