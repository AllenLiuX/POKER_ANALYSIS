"use client";

// 统一深色主题的图表封装（基于 recharts）。所有图都在挂载后再渲染，避免 SSR 水合抖动。
import { useEffect, useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export const CHART_COLORS = {
  emerald: "#10b981",
  amber: "#f59e0b",
  red: "#ef4444",
  sky: "#38bdf8",
  fuchsia: "#e879f9",
  violet: "#a78bfa",
  neutral: "#525252",
};

const TOOLTIP_STYLE = {
  background: "rgba(10,10,10,0.95)",
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 10,
  fontSize: 12,
  color: "#e5e5e5",
  padding: "6px 10px",
} as const;

function useMounted(): boolean {
  const [m, setM] = useState(false);
  useEffect(() => setM(true), []);
  return m;
}

function Placeholder({ height }: { height: number }) {
  return <div style={{ height }} className="animate-pulse rounded-xl bg-white/[0.03]" />;
}

export interface DonutSlice {
  name: string;
  value: number;
  color: string;
}

export function Donut({
  data,
  height = 180,
  centerTop,
  centerSub,
}: {
  data: DonutSlice[];
  height?: number;
  centerTop?: string;
  centerSub?: string;
}) {
  const mounted = useMounted();
  const total = data.reduce((s, d) => s + d.value, 0);
  if (!mounted) return <Placeholder height={height} />;
  if (total <= 0)
    return (
      <div style={{ height }} className="flex items-center justify-center text-xs text-neutral-600">
        暂无数据
      </div>
    );
  return (
    <div className="relative" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="62%"
            outerRadius="92%"
            paddingAngle={2}
            stroke="none"
          >
            {data.map((d, i) => (
              <Cell key={i} fill={d.color} />
            ))}
          </Pie>
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number, n: string) => [v, n]} />
        </PieChart>
      </ResponsiveContainer>
      {(centerTop || centerSub) && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          {centerTop && <span className="text-2xl font-bold text-neutral-100">{centerTop}</span>}
          {centerSub && (
            <span className="mt-0.5 text-[10px] uppercase tracking-wider text-neutral-500">{centerSub}</span>
          )}
        </div>
      )}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; hint?: string }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: it.color }} />
          <span className="text-neutral-300">{it.label}</span>
          {it.hint && <span className="text-neutral-600">{it.hint}</span>}
        </span>
      ))}
    </div>
  );
}

export function TrendArea({
  data,
  xKey = "x",
  yKey = "y",
  color = CHART_COLORS.emerald,
  height = 160,
  yDomain,
  yTickFormatter,
  refY,
}: {
  data: Record<string, number | string>[];
  xKey?: string;
  yKey?: string;
  color?: string;
  height?: number;
  yDomain?: [number, number];
  yTickFormatter?: (v: number) => string;
  refY?: number;
}) {
  const mounted = useMounted();
  if (!mounted) return <Placeholder height={height} />;
  const gid = `grad-${yKey}-${color.replace("#", "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
        <XAxis dataKey={xKey} tick={{ fontSize: 10, fill: "#737373" }} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis
          domain={yDomain}
          tick={{ fontSize: 10, fill: "#737373" }}
          axisLine={false}
          tickLine={false}
          tickFormatter={yTickFormatter}
          width={40}
        />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        {refY != null && <ReferenceLine y={refY} stroke="rgba(255,255,255,0.2)" strokeDasharray="3 3" />}
        <Area type="monotone" dataKey={yKey} stroke={color} strokeWidth={2} fill={`url(#${gid})`} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function NetBars({
  data,
  height = 200,
}: {
  data: { label: string; value: number }[];
  height?: number;
}) {
  const mounted = useMounted();
  if (!mounted) return <Placeholder height={height} />;
  if (data.length === 0)
    return (
      <div style={{ height }} className="flex items-center justify-center text-xs text-neutral-600">
        暂无数据
      </div>
    );
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 6, right: 8, bottom: 4, left: -16 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fill: "#737373" }}
          axisLine={false}
          tickLine={false}
          interval={0}
          angle={-28}
          textAnchor="end"
          height={48}
        />
        <YAxis tick={{ fontSize: 10, fill: "#737373" }} axisLine={false} tickLine={false} width={40} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
        <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
        <Bar dataKey="value" radius={[3, 3, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.value >= 0 ? CHART_COLORS.emerald : CHART_COLORS.red} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export interface RadarPoint {
  stat: string;
  value: number;
  baseline: number;
}

export function TendencyRadar({ data, height = 260 }: { data: RadarPoint[]; height?: number }) {
  const mounted = useMounted();
  if (!mounted) return <Placeholder height={height} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={data} outerRadius="72%">
        <PolarGrid stroke="rgba(255,255,255,0.1)" />
        <PolarAngleAxis dataKey="stat" tick={{ fontSize: 11, fill: "#a3a3a3" }} />
        <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
        <Radar name="基线" dataKey="baseline" stroke={CHART_COLORS.neutral} fill={CHART_COLORS.neutral} fillOpacity={0.14} />
        <Radar name="该对手" dataKey="value" stroke={CHART_COLORS.fuchsia} fill={CHART_COLORS.fuchsia} fillOpacity={0.4} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

/** 通用小节容器：给图表配统一标题。 */
export function ChartCard({
  title,
  right,
  children,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-2xl border border-white/[0.07] bg-neutral-900/50 p-4 ${className}`}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-neutral-500">{title}</h3>
        {right}
      </div>
      {children}
    </div>
  );
}
