"use client";

/**
 * Session + capability context.
 *
 * Authentication is a HttpOnly cookie the backend sets; the client keeps only the public
 * profile it reads back from `GET /auth/me`. There is no token in localStorage anywhere in this
 * app, which is why a successful XSS payload cannot exfiltrate a credential - it can only act
 * as the user while the tab is open, which is also why untrusted markdown is sanitised at render
 * time (src/lib/markdown.ts).
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiFailure, api } from "./api";
import type { AiProvider, Capabilities, Profile } from "./types";

interface SessionValue {
  user: Profile | null;
  capabilities: Capabilities | null;
  provider: AiProvider | null;
  loading: boolean;
  error: string | null;
  /** The run/agent status vocabulary the server actually publishes, for label lookups. */
  runStatuses: string[];
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, displayName: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Profile | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    // Capabilities are public and describe the engine, not the account, so they are fetched
    // unconditionally: the method library and the "mock planner" banner must render even when
    // nobody is signed in.
    const [profile, caps] = await Promise.all([
      api.get<Profile>("/auth/me").catch((cause: unknown) => {
        if (cause instanceof ApiFailure && cause.status === 401) return null;
        setError(cause instanceof Error ? cause.message : String(cause));
        return null;
      }),
      api.get<Capabilities>("/capabilities").catch(() => null),
    ]);
    setUser(profile);
    setCapabilities(caps);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const response = await api.post<{ user: Profile; auth_mode: string }>("/auth/login", { email, password });
      setUser(response.user);
      await load();
    },
    [load],
  );

  const signUp = useCallback(async (email: string, password: string, displayName: string) => {
    await api.post<{ user: Profile }>("/auth/register", { email, password, display_name: displayName });
  }, []);

  const signOut = useCallback(async () => {
    await api.post<{ status: string }>("/auth/logout");
    setUser(null);
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      user,
      capabilities,
      provider: capabilities?.ai ?? null,
      loading,
      error,
      runStatuses: capabilities?.run_statuses ?? [],
      signIn,
      signUp,
      signOut,
      refresh: load,
    }),
    [user, capabilities, loading, error, signIn, signUp, signOut, load],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}

/** A 401 means the session cookie is gone or expired - the UI shows the login screen. */
export function isExpiredSession(error: unknown): boolean {
  return error instanceof ApiFailure && error.status === 401;
}
