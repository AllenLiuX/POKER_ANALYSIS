"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Session, User } from "@supabase/supabase-js";
import { getSupabase, isSupabaseEnabled } from "./supabase";
import {
  syncLocalToCloud,
  syncLocalHandsToCloud,
  syncLocalImportsToCloud,
  syncLocalOppNotesToCloud,
} from "./cloud";
import { loadAttempts } from "./progress";
import { loadHands } from "./battle";
import { loadImportHistory } from "./importHistory";
import { loadOppNotes } from "./opponents";

interface AuthResult {
  error?: string;
  info?: string;
}

interface AuthState {
  enabled: boolean;
  loading: boolean;
  user: User | null;
  session: Session | null;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signUp: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(isSupabaseEnabled);
  const mergedFor = useRef<string | null>(null);

  // 登录后自动把本地（登录前/离线积累的）记录对账补传到云端，一个用户只跑一次。
  const reconcile = useCallback((u: User | null) => {
    if (!u || mergedFor.current === u.id) return;
    mergedFor.current = u.id;
    syncLocalToCloud(loadAttempts()).catch(() => {});
    syncLocalHandsToCloud(loadHands()).catch(() => {});
    syncLocalImportsToCloud(loadImportHistory()).catch(() => {});
    syncLocalOppNotesToCloud(loadOppNotes()).catch(() => {});
  }, []);

  useEffect(() => {
    const sb = getSupabase();
    if (!sb) {
      setLoading(false);
      return;
    }
    sb.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setUser(data.session?.user ?? null);
      setLoading(false);
      reconcile(data.session?.user ?? null);
    });
    const { data: sub } = sb.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      setUser(s?.user ?? null);
      reconcile(s?.user ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, [reconcile]);

  const signIn = useCallback(async (email: string, password: string) => {
    const sb = getSupabase();
    if (!sb) return { error: "未启用云同步（缺少 Supabase 配置）" };
    const { error } = await sb.auth.signInWithPassword({ email, password });
    return error ? { error: error.message } : {};
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    const sb = getSupabase();
    if (!sb) return { error: "未启用云同步（缺少 Supabase 配置）" };
    const { data, error } = await sb.auth.signUp({ email, password });
    if (error) return { error: error.message };
    // 若项目开启了邮箱确认，此时还没有 session
    if (!data.session) return { info: "注册成功，请查收邮件完成确认后再登录。" };
    return {};
  }, []);

  const signOut = useCallback(async () => {
    const sb = getSupabase();
    if (sb) await sb.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      enabled: isSupabaseEnabled,
      loading,
      user,
      session,
      signIn,
      signUp,
      signOut,
    }),
    [loading, user, session, signIn, signUp, signOut],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  return ctx;
}
