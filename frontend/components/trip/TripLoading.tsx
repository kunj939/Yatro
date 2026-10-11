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

  return (
    <div className={compact ? "" : "grid min-h-[calc(100dvh-5rem)] place-items-center px-4 py-6"}>
      <div className="relative isolate w-full max-w-xl overflow-hidden rounded-3xl border border-white/10 bg-[#0f1219] p-5 text-white shadow-[0_40px_100px_-40px_rgb(0_0_0/0.9)] sm:p-7">
        <div aria-hidden className="absolute -right-20 -top-24 -z-10 h-56 w-56 rounded-full bg-[rgb(190_255_74/0.18)] blur-3xl" />
        <div aria-hidden className="absolute -bottom-24 -left-16 -z-10 h-48 w-48 rounded-full bg-[rgb(45_212_230/0.14)] blur-3xl" />

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="num truncate text-[13px] text-white/55">{subtitle}</p>
            <h1 className="mt-0.5 text-xl font-bold leading-tight sm:text-2xl">{title}</h1>
          </div>
          <span className="num shrink-0 text-3xl font-extrabold text-[rgb(190_255_74)]" aria-hidden>{pct}%</span>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-label="Planning progress"
             aria-valuemin={0} aria-valuemax={STAGES.length} aria-valuenow={doneCount}>
          <div className="h-full rounded-full bg-gradient-to-r from-[rgb(190_255_74)] to-[rgb(45_212_230)] transition-[width] duration-700 ease-out" style={{ width: `${pct}%` }} />
        </div>

        {/* What is happening now, and the latest finding */}
        <div className="mt-4 rounded-2xl bg-white/[0.06] p-4" aria-live="polite">
          <p className="text-[12px] font-semibold uppercase tracking-[0.14em] text-white/45">
            {inLine ? "In line" : failed ? "Stopped" : finished ? "Done" : "Now"}
          </p>
          <p className="mt-1 flex items-center gap-2.5 text-[18px] font-semibold">
            {failed ? <X className="h-5 w-5 shrink-0 text-[rgb(255_150_175)]" aria-hidden />
              : finished ? <Check className="h-5 w-5 shrink-0 text-[rgb(190_255_74)]" strokeWidth={3} aria-hidden />
              : <Loader2 className="h-5 w-5 shrink-0 animate-spin text-[rgb(190_255_74)] motion-reduce:animate-none" aria-hidden />}
            <span className="min-w-0 truncate">{inLine ? `Busy right now. You're #${queued!.position} in line` : failed ? failed.label : finished ? "Boom. Your plan is ready" : `${current?.label ?? "Starting"}…`}</span>
          </p>
          <p key={latest?.key ?? "none"} className="fade-up mt-1.5 line-clamp-2 min-h-[2.4rem] text-[13px] leading-snug text-white/60 sm:text-[14px]">
            {inLine ? queueEstimate(queued!.eta_seconds, queued!.eta_range)
              : failed ? state(failed.key)?.detail
              : latest ? <><span className="text-white/80">{latest.short}:</span> {state(latest.key)?.detail}</>
              : "Checking real timetables, opening hours and prices. No guessing."}
          </p>
        </div>

        {/* All the steps at a glance */}
        <ol className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {STAGES.map((s) => {
            const status = statusOf(s.key);
            const Icon = s.icon;
            return (
              <li key={s.key} className={`flex items-center gap-2 rounded-xl border px-2.5 py-1.5 text-[13px] font-medium sm:py-2 transition-all duration-500 ${
                status === "done" ? "border-[rgb(190_255_74/0.25)] bg-[rgb(190_255_74/0.07)] text-white"
                : status === "running" ? "border-white/25 bg-white/[0.08] text-white"
                : status === "failed" ? "border-[rgb(244_110_142/0.4)] bg-[rgb(244_110_142/0.1)] text-white"
                : "border-white/10 text-white/40"}`}>
                <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                  status === "done" ? "bg-[rgb(190_255_74)] text-[#0b0d0c]" : status === "failed" ? "bg-[rgb(244_110_142/0.25)] text-[rgb(255_150_175)]" : "bg-white/10"}`}>
                  {status === "done" ? <Check className="h-3.5 w-3.5" strokeWidth={3} aria-label="Done" />
                    : status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-[rgb(190_255_74)] motion-reduce:animate-none" aria-label="In progress" />
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
