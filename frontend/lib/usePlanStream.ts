"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, streamEvents } from "./api";
import type { Feasibility, PlanEvent, QueueInfo, StageEntry, Trip } from "./types";

const STEP_MS = 180; // readable while the engine works
const FLUSH_MS = 60; // once the result is in, show the remaining steps quickly

/** "no_change": a request that turned out to need no change to the plan — `message` says why. */
export interface StreamOutcome {
  status: "ready" | "infeasible" | "no_change" | "unavailable"; trip: Trip; feasibility?: Feasibility; message?: string;
  /** "unavailable": why, when new plans reopen (an ISO time), and that no free plan was used. */
  reason?: "provider_busy" | "daily_cap" | "high_demand" | "paused"; retry_at?: string; quota_used?: boolean;
}

/** One key per tap of a Plan button (ADR 0008): the same tap sent twice is the same plan on the server. */
function tapKey(): string {
  try { if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID(); } catch { /* fall through */ }
  return `tap-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Runs a streamed plan/replan and reveals stage events at a readable pace. */
export function usePlanStream() {
  const [shown, setShown] = useState<StageEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [queued, setQueued] = useState<QueueInfo | null>(null); // "High demand — you're #N": waiting for room to start
  const queue = useRef<StageEntry[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => () => clearInterval(timer.current), []);

  const run = useCallback(async (path: string, body: unknown): Promise<StreamOutcome> => {
    queue.current = []; setShown([]); setQueued(null); setRunning(true);
    const key = tapKey();
    clearInterval(timer.current);
    timer.current = setInterval(() => {
      const next = queue.current.shift();
      if (next) setShown((s) => [...s, next]);
    }, STEP_MS);
    let outcome: StreamOutcome | null = null;
    let failure: ApiError | null = null;
    const onEvent = (e: PlanEvent) => {
      if (e.type === "queue") setQueued({ position: e.position ?? 1, waiting: e.waiting ?? 1, eta_seconds: e.eta_seconds ?? null, eta_range: e.eta_range ?? null });
      else if (e.type === "stage") { setQueued(null); queue.current.push({ key: e.key!, label: e.label!, status: e.status!, detail: e.detail }); }
      else if (e.type === "result") outcome = { status: e.status as StreamOutcome["status"], trip: e.trip!, feasibility: e.feasibility, message: e.message,
        reason: e.reason, retry_at: e.retry_at, quota_used: e.quota_used };
      else if (e.type === "error") failure = new ApiError(400, e.error!.code, e.error!.message, e.error!.details);
    };
    // A dropped connection is retried once with the SAME key: the server knows it as the same plan and hands back that plan's
    // events or its result, so a flaky network never starts a second plan or spends a second free plan (ADR 0008).
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await streamEvents(path, body, onEvent, key);
        break;
      } catch (err) {
        if (outcome || failure) break;
        const http = err instanceof ApiError && err.status !== 0;  // the server answered with an error: not a dropped connection
        if (http || attempt === 1) { failure = err instanceof ApiError ? err : new ApiError(0, "network", "Planning was interrupted. Please try again."); break; }
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
    // the result is ready: play the remaining steps fast instead of making people wait for the animation
    clearInterval(timer.current);
    timer.current = setInterval(() => {
      const next = queue.current.shift();
      if (next) setShown((s) => [...s, next]);
    }, FLUSH_MS);
    await new Promise<void>((resolve) => {
      const wait = setInterval(() => { if (!queue.current.length) { clearInterval(wait); resolve(); } }, 80);
    });
    await new Promise((r) => setTimeout(r, failure ? 150 : 250));
    clearInterval(timer.current);
    setRunning(false);
    setQueued(null);
    if (failure) throw failure;
    if (!outcome) throw new ApiError(0, "incomplete", "Planning stopped before finishing. Please try again.");
    return outcome;
  }, []);

  return { shown, running, queued, run };
}