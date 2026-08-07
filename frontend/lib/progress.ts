// 本地进度持久化（localStorage）。数据结构与将来 Supabase 的 attempts 表对齐，
// 之后接云同步时可直接把这些 Attempt 记录 upsert 上去。

export type Grade = "optimal" | "acceptable" | "mistake";

export interface Attempt {
  ts: number;
  spot: string; // RFI / vs_RFI
  position: string; // 评分键（vs_RFI 为复合键，如 BB_vs_BTN）
  heroPosition: string;
  opener: string | null;
  handClass: string;
  action: string; // 玩家所选
  optimalAction: string;
  grade: Grade;
  correct: boolean;
}

const KEY = "poker_progress_v1";
const MAX = 2000; // 只保留最近 N 手，避免无限增长

function isBrowser(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function loadAttempts(): Attempt[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? (arr as Attempt[]) : [];
  } catch {
    return [];
  }
}

export function recordAttempt(a: Attempt): Attempt[] {
  if (!isBrowser()) return [];
  const list = loadAttempts();
  list.push(a);
  const trimmed = list.length > MAX ? list.slice(list.length - MAX) : list;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(trimmed));
  } catch {
    // 忽略写入失败（隐私模式/配额）
  }
  return trimmed;
}

export function clearProgress(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}

export interface Bucket {
  total: number;
  correct: number;
}

export interface Summary {
  total: number;
  correct: number;
  accuracy: number; // 0..1
  byGrade: Record<Grade, number>;
  bySpot: Record<string, Bucket>;
  byPosition: Record<string, Bucket>; // 用 hero 视角的可读键
  currentStreak: number;
  bestStreak: number;
  recentMistakes: Attempt[]; // 最近的错题（grade=mistake），新→旧
}

function bump(map: Record<string, Bucket>, key: string, correct: boolean) {
  const b = map[key] ?? { total: 0, correct: 0 };
  b.total += 1;
  if (correct) b.correct += 1;
  map[key] = b;
}

export function positionKey(a: Attempt): string {
  return a.opener ? `${a.heroPosition} vs ${a.opener}` : a.heroPosition;
}

export interface Target {
  spot: string;
  position: string;
}

/**
 * 自适应选题：在可选 target 里按"弱项 + 练得少"加权随机挑一个。
 * - 错误率高（且样本≥3）→ 权重高
 * - 练得少（样本<8）→ 探索加成，避免一直重复同一个薄弱点
 * 没有历史时退化为近似均匀随机。rnd 可注入以便测试。
 */
export function pickAdaptiveTarget(
  attempts: Attempt[],
  targets: Target[],
  rnd: () => number = Math.random,
): Target | null {
  if (!targets.length) return null;
  const buckets: Record<string, Bucket> = {};
  for (const a of attempts) bump(buckets, `${a.spot}\u0000${a.position}`, a.correct);

  const weighted = targets.map((t) => {
    const b = buckets[`${t.spot}\u0000${t.position}`];
    const total = b?.total ?? 0;
    const acc = total ? b!.correct / total : 0;
    const explore = total < 8 ? (8 - total) / 8 : 0; // 0..1
    const errRate = total >= 3 ? 1 - acc : 0.4; // 样本足够才信任错误率
    return { t, w: 0.2 + 1.5 * errRate + 1.0 * explore };
  });

  const totalW = weighted.reduce((s, x) => s + x.w, 0);
  let r = rnd() * totalW;
  for (const x of weighted) {
    r -= x.w;
    if (r <= 0) return x.t;
  }
  return weighted[weighted.length - 1].t;
}

export function summarize(attempts: Attempt[]): Summary {
  const byGrade: Record<Grade, number> = {
    optimal: 0,
    acceptable: 0,
    mistake: 0,
  };
  const bySpot: Record<string, Bucket> = {};
  const byPosition: Record<string, Bucket> = {};
  let correct = 0;
  let best = 0;
  let run = 0;

  for (const a of attempts) {
    if (a.correct) {
      correct += 1;
      run += 1;
      best = Math.max(best, run);
    } else {
      run = 0;
    }
    byGrade[a.grade] = (byGrade[a.grade] ?? 0) + 1;
    bump(bySpot, a.spot, a.correct);
    bump(byPosition, positionKey(a), a.correct);
  }

  const recentMistakes = attempts
    .filter((a) => a.grade === "mistake")
    .slice(-30)
    .reverse();

  const total = attempts.length;
  return {
    total,
    correct,
    accuracy: total ? correct / total : 0,
    byGrade,
    bySpot,
    byPosition,
    currentStreak: run,
    bestStreak: best,
    recentMistakes,
  };
}
