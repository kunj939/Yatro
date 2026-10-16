"use client";

import { Check, Clock, ListChecks, Loader2, MapPin, Repeat, Route, Shuffle, Sparkles, Tags, Wallet, X } from "lucide-react";
import type { QueueInfo, StageEntry } from "@/lib/types";
import { queueEstimate } from "@/lib/format";

export const STAGES: { key: string; label: string; short: string; icon: typeof Check }[] = [
  { key: "understand", label: "Getting what you're after", short: "Preferences", icon: Sparkles },
  { key: "routes", label: "Finding the best routes", short: "Routes", icon: Route },
  { key: "places", label: "Scouting places", short: "Places", icon: MapPin },
  { key: "hours", label: "Checking opening hours", short: "Hours", icon: Clock },
  { key: "prices", label: "Comparing prices", short: "Prices", icon: Tags },
  { key: "optimize", label: "Ordering your stops", short: "Route order", icon: Shuffle },
  { key: "budget", label: "Counting every rupee", short: "Budget", icon: Wallet },
  { key: "alternatives", label: "Lining up swaps", short: "Alternatives", icon: Repeat },
  { key: "itinerary", label: "Writing your itinerary", short: "Itinerary", icon: ListChecks },
];

/** Live planning progress, built to fit any screen: one headline, the step in progress, and a compact grid of all
 *  nine steps. `shown` holds the stage events revealed so far (paced by the parent). */
export function TripLoading({ shown, title, subtitle, compact = false, queued = null }: {
  shown: StageEntry[]; title: string; subtitle?: string; compact?: boolean;
  /** Waiting for room to start (ADR 0008): shown instead of "Now" until the first real step. */
  queued?: QueueInfo | null;
}) {
  const state = (key: string) => {
    const evs = shown.filter((e) => e.key === key);
    return evs.length ? evs[evs.length - 1] : null;
  };
  const statusOf = (key: string) => state(key)?.status ?? "pending";
  const doneCount = STAGES.filter((s) => statusOf(s.key) === "done").length;
  const pct = Math.round((doneCount / STAGES.length) * 100);
  const failed = STAGES.find((s) => statusOf(s.key) === "failed");
  const current = STAGES.find((s) => statusOf(s.key) === "running") ?? STAGES.find((s) => statusOf(s.key) === "pending") ?? null;
  const finished = doneCount === STAGES.length;
  const inLine = !!queued && shown.length === 0;
  // the latest thing the engine found out ("83 options compared (74 train, 9 bus)…")
  const latest = [...STAGES].reverse().find((s) => statusOf(s.key) === "done" && state(s.key)?.detail);

  const R = 26, C = 2 * Math.PI * R;
  return (
    <div className={compact ? "" : "grid min-h-[calc(100dvh-5rem)] place-items-center px-4 py-6"}>
      <div className="relative isolate w-full max-w-xl overflow-hidden rounded-3xl border border-line bg-paper p-5 text-ink shadow-[0_1px_2px_rgb(var(--shadow)/0.05),0_40px_90px_-40px_rgb(var(--shadow)/0.5)] sm:p-7">
        <div aria-hidden className="absolute -right-24 -top-28 -z-10 h-64 w-64 rounded-full bg-signal/12 blur-3xl" />
        <div aria-hidden className="absolute -bottom-28 -left-20 -z-10 h-56 w-56 rounded-full bg-spark/10 blur-3xl" />

        <div className="flex items-center gap-4">
          {/* progress as a ring, the share of the nine checks done */}
          <span className="relative grid h-16 w-16 shrink-0 place-items-center" role="progressbar" aria-label="Planning progress"
                aria-valuemin={0} aria-valuemax={STAGES.length} aria-valuenow={doneCount}>
            <svg viewBox="0 0 64 64" className="absolute inset-0 -rotate-90" aria-hidden>
              <circle cx="32" cy="32" r={R} fill="none" stroke="rgb(var(--haze-2))" strokeWidth="6" />
              <circle cx="32" cy="32" r={R} fill="none" stroke="url(#ring)" strokeWidth="6" strokeLinecap="round"
                      strokeDasharray={C} strokeDashoffset={C * (1 - pct / 100)} className="transition-[stroke-dashoffset] duration-700 ease-out" />
              <defs><linearGradient id="ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="rgb(var(--signal))" /><stop offset="1" stopColor="rgb(var(--spark))" /></linearGradient></defs>
            </svg>
            <span className="num text-[15px] font-extrabold text-ink" aria-hidden>{pct}%</span>
          </span>
          <div className="min-w-0">
            <p className="num truncate text-[13px] text-ink-mute">{subtitle}</p>
            <h1 className="headline-font mt-0.5 text-xl font-extrabold leading-tight sm:text-2xl">{title}</h1>
          </div>
        </div>

        {/* what is happening now, and the latest finding */}
        <div className={`mt-5 rounded-2xl border p-4 ${failed ? "border-danger/30 bg-danger-soft" : "border-signal/25 bg-gradient-to-br from-signal-soft/80 to-paper"}`} aria-live="polite">
          <p className={`text-[11px] font-bold uppercase tracking-[0.14em] ${failed ? "text-danger" : "text-signal-deep"}`}>
            {inLine ? "In line" : failed ? "Stopped" : finished ? "Done" : "Now"}
          </p>
          <p className="mt-1 flex items-center gap-2.5 text-[18px] font-bold text-ink">
            {failed ? <X className="h-5 w-5 shrink-0 text-danger" aria-hidden />
              : finished ? <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-signal text-white"><Check className="h-4 w-4" strokeWidth={3} aria-hidden /></span>
              : <Loader2 className="h-5 w-5 shrink-0 animate-spin text-signal motion-reduce:animate-none" aria-hidden />}
            <span className="min-w-0 truncate">{inLine ? `Busy right now. You're #${queued!.position} in line` : failed ? failed.label : finished ? "Boom. Your plan is ready" : `${current?.label ?? "Starting"}…`}</span>
          </p>
          <p key={latest?.key ?? "none"} className="fade-up mt-1.5 line-clamp-2 min-h-[2.4rem] text-[13px] leading-snug text-ink-soft sm:text-[14px]">
            {inLine ? queueEstimate(queued!.eta_seconds, queued!.eta_range)
              : failed ? state(failed.key)?.detail
              : latest ? <><span className="font-semibold text-ink">{latest.short}:</span> {state(latest.key)?.detail}</>
              : "Checking real timetables, opening hours and prices. No guessing."}
          </p>
          {!failed && !finished && (
            <div className="mt-3 h-1 overflow-hidden rounded-full bg-signal/15" aria-hidden>
              <div className="loading-sweep h-full w-1/3 rounded-full bg-gradient-to-r from-transparent via-signal to-transparent" />
            </div>
          )}
        </div>

        {/* all the steps at a glance */}
        <ol className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {STAGES.map((s) => {
            const status = statusOf(s.key);
            const Icon = s.icon;
            return (
              <li key={s.key} className={`flex items-center gap-2 rounded-xl border px-2.5 py-1.5 text-[13px] font-semibold transition-all duration-500 sm:py-2 ${
                status === "done" ? "border-signal/25 bg-signal-soft/50 text-ink"
                : status === "running" ? "border-signal/50 bg-paper text-ink shadow-[0_6px_16px_-10px_rgb(var(--signal)/0.7)]"
                : status === "failed" ? "border-danger/30 bg-danger-soft text-danger"
                : "border-line bg-paper text-ink-mute"}`}>
                <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                  status === "done" ? "bg-signal text-white" : status === "failed" ? "bg-danger/15 text-danger"
                  : status === "running" ? "bg-signal-soft text-signal-deep" : "bg-haze-2 text-ink-mute"}`}>
                  {status === "done" ? <Check className="h-3.5 w-3.5" strokeWidth={3} aria-label="Done" />
                    : status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-label="In progress" />
                    : status === "failed" ? <X className="h-3.5 w-3.5" aria-label="Stopped" />
                    : <Icon className="h-3.5 w-3.5" aria-hidden />}
                </span>
                <span className="min-w-0 truncate">{s.short}</span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
