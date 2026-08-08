"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getHealth, type HealthResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const LINKS: { href: string; label: string; badge?: string }[] = [
  { href: "/trainer", label: "翻前训练" },
  { href: "/trainer/postflop", label: "翻后训练" },
  { href: "/battle", label: "模拟对战", badge: "New" },
  { href: "/ranges", label: "范围表" },
  { href: "/import", label: "截图导入", badge: "Beta" },
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
          <span className="text-xl text-emerald-400">♠</span>
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
                className={`relative shrink-0 rounded-lg px-2.5 py-1.5 text-sm font-medium transition sm:px-3 ${
                  active
                    ? "bg-neutral-800 text-neutral-100"
                    : "text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200"
                }`}
              >
                {l.label}
                {l.badge && (
                  <span className="ml-1 rounded-full bg-emerald-900/70 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-300 align-middle">
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
      className="max-w-[9rem] truncate rounded-full border border-neutral-700 bg-neutral-900/60 px-3 py-1 text-xs text-neutral-300 transition hover:bg-neutral-800"
      title={user?.email ?? "登录"}
    >
      {user ? user.email : "登录"}
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
      <span className="hidden rounded-full bg-red-900/60 px-2.5 py-1 text-xs text-red-300 sm:inline">
        后端离线
      </span>
    );
  }
  return (
    <span
      className={`hidden rounded-full px-2.5 py-1 text-xs sm:inline ${
        health
          ? "bg-emerald-900/60 text-emerald-300"
          : "bg-neutral-800 text-neutral-400"
      }`}
    >
      {health ? "在线" : "连接中"}
    </span>
  );
}
