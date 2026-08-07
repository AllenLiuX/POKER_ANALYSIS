const SUIT_GLYPH: Record<string, string> = {
  s: "\u2660",
  h: "\u2665",
  d: "\u2666",
  c: "\u2663",
};

const RED_SUITS = new Set(["h", "d"]);

const SIZE = {
  sm: { box: "h-9 w-7 text-sm", suit: "text-xs" },
  md: { box: "h-14 w-10 text-xl", suit: "text-lg" },
  lg: { box: "h-20 w-14 text-3xl", suit: "text-2xl" },
} as const;

export default function PlayingCard({
  card,
  size = "md",
}: {
  card: string;
  size?: keyof typeof SIZE;
}) {
  const rank = card[0];
  const suit = card[1]?.toLowerCase() ?? "s";
  const red = RED_SUITS.has(suit);
  const s = SIZE[size];
  return (
    <div
      className={`inline-flex flex-col items-center justify-center rounded-md bg-white font-bold shadow-md ${s.box} ${
        red ? "text-red-600" : "text-neutral-900"
      }`}
    >
      <span className="leading-none">{rank}</span>
      <span className={`leading-none ${s.suit}`}>{SUIT_GLYPH[suit] ?? suit}</span>
    </div>
  );
}
