"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { endpoints, getToken, setToken, type AuthResult, type SignUp } from "./api";
import type { Quota, User } from "./types";

interface AuthState {
  user: User | null;
  quota: Quota | null;
  ready: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (body: SignUp) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
  setQuota: (q: Quota) => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(null); setQuota(null); setReady(true); return; }
    try {
      const me = await endpoints.me();
      setUser(me.user); setQuota(me.subscription);
    } catch (e: any) {
      if (e?.status === 401) setToken(null);
      setUser(null); setQuota(null);
    } finally { setReady(true); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const accept = (r: AuthResult) => { setToken(r.access_token); setUser(r.user); setQuota(r.subscription); };

  const value: AuthState = {
    user, quota, ready, refresh, setQuota,
    signIn: async (email, password) => accept(await endpoints.login(email, password)),
    signUp: async (body) => accept(await endpoints.register(body)),
    signOut: () => { setToken(null); setUser(null); setQuota(null); },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
