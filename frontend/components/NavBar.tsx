"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Spade, UserRound } from "lucide-react";
import { getHealth, type HealthResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";

const LINKS: { href: string; label: string; badge?: string }[] = [
  { href: "/trainer", label: "翻前训练" },
  { href: "/trainer/postflop", label: "翻后训练" },
  { href: "/battle", label: "模拟对战", badge: "New" },
  { href: "/ranges", label: "范围表" },
  { href: "/import", label: "截图导入", badge: "Beta" },
  { href: "/opponents", label: "对手档案", badge: "Beta" },
  { href: "/progress", label: "我的进度" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/trainer") return pathname === "/trainer"; // 不把翻后误判为翻前
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function NavBar() {
  const pathname = usePathname() || "/";

  return (
    <header className="sticky top-0 z-40 border-b border-neutral-900/80 bg-neutral-950/80 backdrop-blur">
      <nav className="mx-auto flex h-14 max-w-6xl items-center gap-1 px-4 sm:px-6">
        <Link href="/" className="mr-2 flex shrink-0 items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/25">
            <Spade className="size-4" fill="currentColor" strokeWidth={0} />
          </span>
          <span className="hidden text-sm font-bold tracking-tight text-neutral-100 sm:inline">
            Poker Analysis
          </span>
        </Link>

        <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
          {LINKS.map((l) => {
            const active = isActive(pathname, l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={cn(
                  "relative shrink-0 rounded-lg px-2.5 py-1.5 text-sm font-medium transition sm:px-3",
                  active
                    ? "bg-white/[0.08] text-neutral-100"
                    : "text-neutral-400 hover:bg-white/5 hover:text-neutral-200",
                )}
              >
                {l.label}
                {l.badge && (
                  <span className="ml-1 align-middle rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-300 ring-1 ring-emerald-500/20">
                    {l.badge}
                  </span>
                )}
              </Link>
            );
          })}
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-2">
          <HealthBadge />
          <AuthNav />
        </div>
      </nav>
    </header>
  );
}

function AuthNav() {
  const { enabled, user } = useAuth();
  if (!enabled) return null;
  return (
    <Link
      href={user ? "/progress" : "/login"}
      className="flex max-w-[9rem] items-center gap-1.5 truncate rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-neutral-300 transition hover:bg-white/[0.07]"
      title={user?.email ?? "登录"}
    >
      <UserRound className="size-3.5 shrink-0" />
      <span className="truncate">{user ? user.email : "登录"}</span>
    </Link>
  );
}

function HealthBadge() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setErr(true));
  }, []);

  if (err) {
    return (
      <span className="hidden items-center gap-1.5 rounded-full bg-red-500/12 px-2.5 py-1 text-xs text-red-300 ring-1 ring-red-500/25 sm:inline-flex">
        <span className="size-1.5 rounded-full bg-red-400" />
        后端离线
      </span>
    );
  }
  return (
    <span
      className={cn(
        "hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs ring-1 sm:inline-flex",
        health
          ? "bg-emerald-500/12 text-emerald-300 ring-emerald-500/25"
          : "bg-white/[0.04] text-neutral-400 ring-white/10",
      )}
    >
      <span className={cn("size-1.5 rounded-full", health ? "bg-emerald-400" : "bg-neutral-500")} />
      {health ? "在线" : "连接中"}
    </span>
  );
}
