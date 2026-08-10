// 通用手牌展示组件：把「上传导入 / 对战练习 / 对手分析」里的手牌统一成一套视图。
//  - HandView：完整详情（公共牌 + 逐玩家：身份/底牌/成手/投入净额/分街行动线 + 校验 + 附加内容）。
//  - StreetLines：紧凑的「分街行动线」（列表行用），供对战记录 / 对手手牌清单复用。
//  - normalizeCard：把 "A♠" / "10♦" 之类的字形统一成 PlayingCard 认识的 "As" / "Td"。
import type React from "react";
import PlayingCard from "@/components/PlayingCard";
import { ZoomIn } from "lucide-react";
import type {
  IngestItem,
  IngestPlayerObs,
  ReconstructedAction,
} from "@/lib/api";

export const STREET_ORDER = ["翻前", "翻牌", "转牌", "河牌"];
export const STREET_STYLE: Record<string, string> = {
  翻前: "text-sky-300",
  翻牌: "text-emerald-300",
  转牌: "text-amber-300",
  河牌: "text-rose-300",
};

// ---------- 归一化卡牌字符串 ----------
const GLYPH_TO_SUIT: Record<string, string> = { "♠": "s", "♥": "h", "♦": "d", "♣": "c" };

/** 把展示字形（A♠ / 10♦）或原始码（As / Td）统一为 PlayingCard 可渲染的两字符码。 */
export function normalizeCard(card: string): string {
  if (!card) return card;
  let rank = card.slice(0, card.length - 1);
  let suit = card.slice(-1);
  if (rank === "10") rank = "T";
  suit = GLYPH_TO_SUIT[suit] ?? suit.toLowerCase();
  return `${rank}${suit}`;
}

// ---------- 类型 ----------
export interface HandStreet {
  street: string; // 中文街道，"" 表示无街道信息
  actions: string[]; // 已格式化的动作串（可含金额）
}

export interface HandPlayer {
  name: string;
  isHero?: boolean;
  isWinner?: boolean;
  position?: string | null;
  holeCards?: string[];
  madeHand?: string | null;
  net?: number | null;
  invested?: number | null;
  uncertain?: boolean;
  uncertainNote?: string | null;
  streets?: HandStreet[];
  raw?: string | null; // 无分街结构时的原始动作串
}

export type MetaTone = "default" | "good" | "warn" | "bad" | "info";
export interface HandMeta {
  label: string;
  tone?: MetaTone;
  title?: string;
}

export interface HandModel {
  board?: string[];
  players: HandPlayer[];
  meta?: HandMeta[];
  confidence?: number | null; // 0..1 → 进度条
  note?: string | null;
  netUnit?: string; // "" | "bb"
}

const META_TONE: Record<MetaTone, string> = {
  default: "bg-neutral-800 text-neutral-300 ring-neutral-700/40",
  good: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  warn: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  bad: "bg-red-500/15 text-red-300 ring-red-500/30",
  info: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
};

// ---------- 分街行动线（chip 版，用于玩家行）----------
function StreetTimeline({ streets }: { streets: HandStreet[] }) {
  if (!streets.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {streets.map((s, i) =>
        s.street ? (
          <div
            key={`${s.street}-${i}`}
            className="flex items-center gap-1 rounded-md bg-neutral-950/60 px-1.5 py-1 ring-1 ring-neutral-800"
          >
            <span className={`text-[10px] font-semibold ${STREET_STYLE[s.street] ?? "text-neutral-400"}`}>
              {s.street}
            </span>
            {s.actions.map((a, j) => (
              <span key={j} className="rounded bg-neutral-800/80 px-1.5 py-0.5 text-[11px] text-neutral-200">
                {a}
              </span>
            ))}
          </div>
        ) : (
          s.actions.map((a, j) => (
            <span key={`o-${i}-${j}`} className="rounded bg-neutral-800/80 px-1.5 py-0.5 text-[11px] text-neutral-300">
              {a}
            </span>
          ))
        ),
      )}
    </div>
  );
}

// ---------- 分街行动线（文本行版，用于紧凑列表）----------
export function StreetLines({
  lines,
  className = "",
}: {
  lines: { street: string; text: string }[];
  className?: string;
}) {
  if (!lines.length) return null;
  return (
    <div className={`space-y-0.5 ${className}`}>
      {lines.map((ln, i) => (
        <div key={`${ln.street}-${i}`} className="flex gap-2 text-[11px] leading-relaxed">
          <span className={`shrink-0 font-medium ${STREET_STYLE[ln.street] ?? "text-neutral-600"}`}>
            {ln.street}
          </span>
          <span className="text-neutral-400">{ln.text}</span>
        </div>
      ))}
    </div>
  );
}

// ---------- 单玩家行 ----------
function PlayerRow({ p, netUnit = "" }: { p: HandPlayer; netUnit?: string }) {
  const cards = (p.holeCards ?? []).map(normalizeCard);
  const net = p.net;
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-sm ${
        p.isHero ? "border-emerald-600/40 bg-emerald-950/20" : "border-neutral-800 bg-neutral-900/40"
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-neutral-100">{p.name}</span>
        {p.isHero && (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">我</span>
        )}
        {p.isWinner && (
          <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-300">赢家</span>
        )}
        {p.position && (
          <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-300">{p.position}</span>
        )}
        {cards.length > 0 && (
          <span className="ml-0.5 flex gap-1">
            {cards.map((c, i) => (
              <PlayingCard key={`${c}-${i}`} card={c} size="sm" />
            ))}
          </span>
        )}
        {p.madeHand && (
          <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-amber-300">{p.madeHand}</span>
        )}
        {p.uncertain && (
          <span
            className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300 ring-1 ring-amber-500/30"
            title="逐街动作金额之和与净额对不上，可能有动作未被识别"
          >
            动作待复核
          </span>
        )}
        <span className="ml-auto text-xs text-neutral-500">
          {p.invested != null && <>投入 {p.invested}</>}
          {net != null && (
            <span
              className={`ml-2 font-semibold ${
                net > 0 ? "text-emerald-400" : net < 0 ? "text-red-400" : "text-neutral-400"
              }`}
            >
              净 {net > 0 ? "+" : ""}
              {net}
              {netUnit}
            </span>
          )}
        </span>
      </div>
      {p.streets && p.streets.length > 0 ? (
        <StreetTimeline streets={p.streets} />
      ) : (
        p.raw && <p className="mt-1 text-xs text-neutral-400">{p.raw}</p>
      )}
      {p.uncertain && p.uncertainNote && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-amber-300/80">{p.uncertainNote}</p>
      )}
    </div>
  );
}

// ---------- 完整手牌视图 ----------
export function HandView({
  hand,
  checks,
  children,
  className = "",
}: {
  hand: HandModel;
  checks?: React.ReactNode; // 校验行（如净额守恒 / 动作一致）
  children?: React.ReactNode; // 附加内容（如 GTO 偏离标注）
  className?: string;
}) {
  const board = (hand.board ?? []).map(normalizeCard);
  const conf = hand.confidence != null ? Math.round(hand.confidence * 100) : null;
  return (
    <div className={className}>
      {(hand.meta?.length || conf != null) && (
        <div className="flex flex-wrap items-center gap-2">
          {hand.meta?.map((m, i) => (
            <span
              key={`${m.label}-${i}`}
              title={m.title}
              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${META_TONE[m.tone ?? "default"]}`}
            >
              {m.label}
            </span>
          ))}
          {conf != null && (
            <span className="ml-auto flex items-center gap-2 text-xs text-neutral-500">
              置信度
              <span className="inline-block h-1.5 w-16 overflow-hidden rounded-full bg-neutral-800 align-middle">
                <span className="block h-full bg-emerald-500" style={{ width: `${conf}%` }} />
              </span>
              {conf}%
            </span>
          )}
        </div>
      )}

      {checks && <div className="mt-3 flex flex-wrap items-center gap-2">{checks}</div>}

      {board.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-neutral-500">公共牌</div>
          <div className="flex gap-1.5">
            {board.map((c, i) => (
              <PlayingCard key={`${c}-${i}`} card={c} size="sm" />
            ))}
          </div>
        </div>
      )}

      <div className="mt-4">
        <div className="mb-1.5 text-[11px] uppercase tracking-wider text-neutral-500">
          玩家（{hand.players.length}）
        </div>
        <div className="space-y-2">
          {hand.players.map((p, i) => (
            <PlayerRow key={`${p.name}-${i}`} p={p} netUnit={hand.netUnit} />
          ))}
          {hand.players.length === 0 && <p className="text-sm text-neutral-600">未识别到玩家行。</p>}
        </div>
      </div>

      {hand.note && <p className="mt-3 text-[11px] leading-relaxed text-neutral-600">{hand.note}</p>}

      {children}
    </div>
  );
}

// ---------- 紧凑单行（列表用：缩略图 + 公共牌 + 行动线 + 净额）----------
export function HandLine({
  board,
  lines,
  line,
  net,
  netUnit = "",
  thumb,
  onZoom,
  title,
  right,
}: {
  board?: string[];
  lines?: { street: string; text: string }[];
  line?: string;
  net?: number | null;
  netUnit?: string;
  thumb?: string | null;
  onZoom?: (src: string) => void;
  title?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 py-2 text-sm">
      {thumb ? (
        <button
          type="button"
          onClick={() => onZoom?.(thumb)}
          className="group/thumb relative h-9 w-14 shrink-0 overflow-hidden rounded object-cover ring-1 ring-white/10"
          title={onZoom ? "点击放大" : undefined}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={thumb} alt="" className="h-full w-full object-cover" />
          {onZoom && (
            <span className="absolute inset-0 flex items-center justify-center bg-black/0 text-transparent transition group-hover/thumb:bg-black/50 group-hover/thumb:text-white">
              <ZoomIn className="size-3.5" />
            </span>
          )}
        </button>
      ) : (
        <div className="h-9 w-14 shrink-0 rounded bg-neutral-800" />
      )}
      <div className="min-w-0 flex-1">
        {title && <div className="mb-0.5">{title}</div>}
        {board && (
          <div className="font-mono text-xs text-neutral-300">
            {board.length ? board.join(" ") : "—"}
          </div>
        )}
        {lines && lines.length > 0 ? (
          <StreetLines lines={lines} className="mt-0.5" />
        ) : (
          <div className="mt-0.5 truncate text-xs text-neutral-400">{line || "（无行动线）"}</div>
        )}
      </div>
      {net != null && (
        <span
          className={`shrink-0 text-right text-xs font-semibold ${
            net > 0 ? "text-emerald-400" : net < 0 ? "text-red-400" : "text-neutral-500"
          }`}
        >
          {net > 0 ? "+" : ""}
          {netUnit ? net.toFixed(1) : Math.round(net)}
          {netUnit}
        </span>
      )}
      {right}
    </div>
  );
}

// ---------- 适配器：截图导入结果 → HandModel（合并「观测事实」与「重建」）----------
const TYPE_META: Record<string, { label: string; tone: MetaTone }> = {
  hand_replay: { label: "手牌回放", tone: "good" },
  result_summary: { label: "结算画面", tone: "warn" },
  unknown: { label: "未知类型", tone: "default" },
};
const RECON_META: Record<string, { label: string; tone: MetaTone }> = {
  validated: { label: "重建已校验", tone: "good" },
  needs_review: { label: "重建待复核", tone: "warn" },
  needs_user: { label: "需人工确认", tone: "default" },
};
const ZH_TO_STREET_KEY: Record<string, string> = {
  翻前: "preflop",
  翻牌: "flop",
  转牌: "turn",
  河牌: "river",
};

function groupReconStreets(actions: ReconstructedAction[]): HandStreet[] {
  const groups = new Map<string, string[]>();
  const other: string[] = [];
  for (const a of actions) {
    const txt = `${a.label}${a.amount != null ? ` ${a.amount}` : ""}`;
    if (a.street && STREET_ORDER.includes(a.street)) {
      const arr = groups.get(a.street) ?? [];
      arr.push(txt);
      groups.set(a.street, arr);
    } else {
      other.push(txt);
    }
  }
  const out: HandStreet[] = STREET_ORDER.filter((s) => groups.has(s)).map((s) => ({
    street: s,
    actions: groups.get(s)!,
  }));
  if (other.length) out.push({ street: "", actions: other });
  return out;
}

function groupFactsStreets(byStreet?: Record<string, string[]> | null): HandStreet[] {
  if (!byStreet) return [];
  const out: HandStreet[] = [];
  for (const zh of STREET_ORDER) {
    const arr = byStreet[ZH_TO_STREET_KEY[zh]] ?? [];
    if (arr.length) out.push({ street: zh, actions: arr });
  }
  return out;
}

/** 把一条导入结果合并为统一手牌模型：优先用「重建」的分街动作（含金额+校验），
 * 底牌/成手/位置等从「观测事实」补齐；无重建时退回纯观测。 */
export function handFromIngest(item: IngestItem): HandModel {
  const facts = item.facts;
  const recon = item.reconstruction ?? null;
  const board = recon?.board?.length ? recon.board : facts?.board ?? [];

  const factList: IngestPlayerObs[] = facts?.players ?? [];
  const factByAlias = new Map<string, IngestPlayerObs>();
  factList.forEach((p) => {
    const key = (p.alias || "").trim();
    if (key) factByAlias.set(key, p);
  });

  let players: HandPlayer[] = [];
  if (recon?.players?.length) {
    players = recon.players.map((rp, i) => {
      const alias = (rp.alias || "").trim();
      const fp = (alias && factByAlias.get(alias)) || factList[i];
      const holeCards = rp.hole_cards?.length ? rp.hole_cards : fp?.hole_cards ?? [];
      return {
        name: rp.alias ?? fp?.alias ?? "（未知）",
        isHero: rp.is_hero,
        isWinner: rp.is_winner,
        position: rp.position ?? fp?.position ?? null,
        holeCards,
        madeHand: fp?.made_hand ?? null,
        net: rp.net ?? fp?.net ?? null,
        invested: rp.invested,
        uncertain: rp.uncertain,
        uncertainNote: rp.uncertain
          ? `逐街动作之和 ${rp.parsed_invested}，按净额应约 ${rp.invested}——可能有一街动作未被识别，已按净额校正投入。`
          : null,
        streets: groupReconStreets(rp.actions),
      };
    });
  } else {
    players = factList.map((p) => {
      const hasStreets = p.actions_by_street && Object.keys(p.actions_by_street).length > 0;
      return {
        name: p.alias ?? "（未知）",
        isHero: p.is_hero,
        position: p.position ?? null,
        holeCards: p.hole_cards ?? [],
        madeHand: p.made_hand ?? null,
        net: p.net ?? null,
        streets: groupFactsStreets(p.actions_by_street ?? undefined),
        raw: hasStreets ? null : p.actions_raw ?? null,
      };
    });
  }

  const meta: HandMeta[] = [];
  if (facts) {
    const t = TYPE_META[facts.screenshot_type] ?? TYPE_META.unknown;
    meta.push({ label: t.label, tone: t.tone });
    if (facts.blinds) meta.push({ label: `盲注 ${facts.blinds}` });
    if (facts.pot != null) meta.push({ label: `底池 ${facts.pot}` });
    if (facts.hand_id) meta.push({ label: `#${facts.hand_id}` });
  }
  if (recon) {
    const r = RECON_META[recon.status] ?? RECON_META.needs_user;
    meta.push({ label: r.label, tone: r.tone });
    meta.push({ label: `重建置信 ${Math.round(recon.confidence * 100)}%` });
  }

  return {
    board,
    players,
    meta,
    confidence: facts?.extraction_confidence ?? null,
    note: recon?.note ?? null,
  };
}
