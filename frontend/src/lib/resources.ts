"use client";

/**
 * Data-fetching hooks shared by the lab pages.
 *
 * Three deliberate choices:
 *  - reads are aborted on unmount (a fast clicker must not paint a stale response);
 *  - the error state keeps the server's own message and code, so "no such run" and
 *    "you may not see that run" stay distinguishable in the UI;
 *  - background progress is polled from a cursor instead of a long-lived SSE stream, because
 *    a proxy between the browser and the dev server can buffer `text/event-stream` and leave a
 *    running job looking frozen. `/agent/sessions/{id}/events?after=N` gives the same events.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiFailure, api } from "./api";
import type { AgentEvent } from "./types";

export interface Resource<T> {
  data: T | null;
  error: ApiFailure | Error | null;
  loading: boolean;
  refreshing: boolean;
  reload: () => Promise<void>;
  setData: (updater: T | ((current: T | null) => T)) => void;
}

export function useResource<T>(path: string | null, options: { silent?: boolean } = {}): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiFailure | Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [refreshing, setRefreshing] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  const load = useCallback(async () => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    controller.current?.abort();
    const local = new AbortController();
    controller.current = local;
    if (options.silent) setRefreshing(true);
    else setLoading(true);

    try {
      const result = await api.get<T>(path, local.signal);
      if (!mounted.current) return;
      setData(result);
      setError(null);
    } catch (cause) {
      if (!mounted.current || (cause instanceof DOMException && cause.name === "AbortError")) return;
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      if (mounted.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = useCallback((updater: T | ((current: T | null) => T)) => {
    setData((current) => (typeof updater === "function" ? (updater as (value: T | null) => T)(current) : updater));
  }, []);

  return {
    data,
    error,
    loading,
    refreshing,
    reload: load,
    setData: update,
  };
}

/** Poll while a run is queued/running and stop as soon as it reaches a terminal state. */
export function usePolling(callback: () => Promise<void> | void, intervalMs = 1200, enabled = true) {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const id = setInterval(() => {
      if (cancelled) return;
      void saved.current();
    }, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, enabled]);
}

export interface AgentFeed {
  events: AgentEvent[];
  connected: boolean;
  error: string | null;
  latest: AgentEvent | null;
}

/** Cursor-based event feed for an agent session; returns events in order. */
export function useAgentEvents(sessionId: string | null, options: { active: boolean }): AgentFeed {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0);
  const seenSession = useRef<string | null>(null);

  useEffect(() => {
    if (seenSession.current !== sessionId) {
      seenSession.current = sessionId;
      cursor.current = 0;
      setEvents([]);
      setError(null);
    }
  }, [sessionId]);

  const tick = useCallback(async () => {
    if (!sessionId) return;
    try {
      const page = await api.get<{ events: AgentEvent[]; latest_sequence: number }>(
        `/agent/sessions/${sessionId}/events?after=${cursor.current}`,
      );
      const incoming = (page.events ?? []).filter((event) => event.sequence > cursor.current);
      if (incoming.length > 0) {
        cursor.current = Math.max(...incoming.map((event) => event.sequence));
        setEvents((current) => [...current, ...incoming].sort((a, b) => a.sequence - b.sequence).slice(-400));
      }
      setConnected(true);
      setError(null);
    } catch (cause) {
      setConnected(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    void tick();
  }, [sessionId, tick]);

  usePolling(tick, 800, Boolean(sessionId) && options.active);

  return { events, connected, error, latest: events.length > 0 ? events[events.length - 1]! : null };
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiFailure) return error.message;
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : "something went wrong";
}

export function errorCode(error: unknown): string | null {
  return error instanceof ApiFailure ? error.code : null;
}
