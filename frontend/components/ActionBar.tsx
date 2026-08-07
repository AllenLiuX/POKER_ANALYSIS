const ACTION_STYLE: Record<string, string> = {
  fold: "bg-neutral-700 hover:bg-neutral-600",
  call: "bg-sky-600 hover:bg-sky-500",
  raise: "bg-red-600 hover:bg-red-500",
  allin: "bg-amber-600 hover:bg-amber-500",
};

export default function ActionBar({
  actions,
  labels,
  disabled,
  onAct,
}: {
  actions: string[];
  labels: Record<string, string>;
  disabled?: boolean;
  onAct: (action: string) => void;
}) {
  return (
    <div className="flex flex-wrap justify-center gap-3">
      {actions.map((a) => (
        <button
          key={a}
          disabled={disabled}
          onClick={() => onAct(a)}
          className={`min-w-[96px] rounded-xl px-6 py-3 text-base font-semibold text-white shadow transition disabled:cursor-not-allowed disabled:opacity-40 ${
            ACTION_STYLE[a] ?? "bg-neutral-700 hover:bg-neutral-600"
          }`}
        >
          {labels[a] ?? a}
        </button>
      ))}
    </div>
  );
}
