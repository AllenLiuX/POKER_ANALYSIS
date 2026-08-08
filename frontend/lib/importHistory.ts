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

/** 把新解析的若干条前置到历史（最新在前），返回更新后的列表。 */
export function addImportEntries(entries: ImportEntry[]): ImportEntry[] {
  const next = [...entries, ...loadImportHistory()].slice(0, MAX);
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

/** 生成降采样缩略图 data URL；失败返回 null（不阻塞主流程）。 */
export async function makeThumb(file: File, max = 360): Promise<string | null> {
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
    return canvas.toDataURL("image/jpeg", 0.7);
  } catch {
    return null;
  }
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
