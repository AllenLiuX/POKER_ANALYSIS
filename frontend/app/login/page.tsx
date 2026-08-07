"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { enabled, user, signIn, signUp, signOut } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setInfo(null);
    const res = mode === "signin"
      ? await signIn(email, password)
      : await signUp(email, password);
    setBusy(false);
    if (res.error) {
      setErr(res.error);
    } else if (res.info) {
      setInfo(res.info);
    } else {
      router.push("/progress");
    }
  }

  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← 返回
        </Link>
        <h1 className="text-2xl font-bold">
          {mode === "signin" ? "登录" : "注册"}
        </h1>
      </div>

      {!enabled ? (
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-6 text-sm text-neutral-300">
          <p className="font-medium text-amber-300">云同步尚未启用</p>
          <p className="mt-2 text-neutral-400">
            当前为本地模式，进度保存在本机浏览器即可正常训练。要开启账户与多设备同步，
            需在 <code className="text-neutral-500">frontend/.env.local</code> 填入
            Supabase 的 URL 与 anon key（见 <code className="text-neutral-500">docs/SUPABASE.md</code>）。
          </p>
          <Link
            href="/progress"
            className="mt-4 inline-block rounded-lg bg-neutral-800 px-4 py-2 text-neutral-200 hover:bg-neutral-700"
          >
            查看本地进度 →
          </Link>
        </div>
      ) : user ? (
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-6 text-sm">
          <p className="text-neutral-300">
            已登录：<span className="font-medium text-emerald-300">{user.email}</span>
          </p>
          <div className="mt-4 flex gap-3">
            <Link
              href="/progress"
              className="rounded-lg bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-500"
            >
              我的进度
            </Link>
            <button
              onClick={() => signOut()}
              className="rounded-lg bg-neutral-800 px-4 py-2 text-neutral-300 hover:bg-neutral-700"
            >
              退出登录
            </button>
          </div>
        </div>
      ) : (
        <form
          onSubmit={submit}
          className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-6"
        >
          <label className="block">
            <span className="text-sm text-neutral-400">邮箱</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-neutral-100 outline-none focus:border-emerald-500"
              placeholder="you@example.com"
            />
          </label>
          <label className="mt-4 block">
            <span className="text-sm text-neutral-400">密码</span>
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-neutral-100 outline-none focus:border-emerald-500"
              placeholder="至少 6 位"
            />
          </label>

          {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
          {info && <p className="mt-3 text-sm text-emerald-400">{info}</p>}

          <button
            type="submit"
            disabled={busy}
            className="mt-5 w-full rounded-xl bg-emerald-600 py-2.5 font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy ? "处理中…" : mode === "signin" ? "登录" : "注册"}
          </button>

          <button
            type="button"
            onClick={() => {
              setMode(mode === "signin" ? "signup" : "signin");
              setErr(null);
              setInfo(null);
            }}
            className="mt-3 w-full text-center text-sm text-neutral-400 hover:text-neutral-200"
          >
            {mode === "signin" ? "还没有账号？去注册" : "已有账号？去登录"}
          </button>
        </form>
      )}
    </main>
  );
}
