"use client";

import Link from "next/link";
import { ArrowRight, CircleAlert, Clock, Lock, MapPin, Sparkles } from "lucide-react";
import type { Adjustment, Feasibility, Quota } from "@/lib/types";
import { inr, money } from "@/lib/format";

const PARTS: { key: string; label: string; tone: string }[] = [
  { key: "transport", label: "Getting there and back", tone: "bg-spark" },
  { key: "stay", label: "Stay", tone: "bg-signal" },
  { key: "food", label: "Food", tone: "bg-ink-soft" },
  { key: "local", label: "Local travel", tone: "bg-ink-mute/60" },
];

/** Does this change alone close the gap? `saves` is what the engine estimates it takes off the cheapest plan. */
function fixes(a: Adjustment, over: number): boolean {
  return a.key === "higher_budget" || (a.saves ?? 0) >= over;
}

/** The suggestion to lead with: the smallest change that makes the trip work on its own. Raising the budget only when
 * nothing else does, since it asks the most of the traveller; for a time conflict, the engine's own first pick. */
function pickPrimary(adjs: Adjustment[], over: number | null): Adjustment | undefined {
  const usable = adjs.filter((a) => a.apply?.action !== "change_destination");
  if (over === null) return usable[0];
  return usable.find((a) => a.key !== "higher_budget" && fixes(a, over)) ?? usable.find((a) => a.key === "higher_budget") ?? usable[0];
}

/** What it costs at the least against what was budgeted, as one bar: the parts of the cheapest plan side by side,
 * and a line where the budget runs out. The gap reads at a glance before any sentence does. */
function BudgetGap({ min, currency }: { min: Record<string, number>; currency?: string }) {
  const total = min.total || 1;
  const budget = Math.max(0, total - (min.over_by ?? 0));
  const at = Math.min(100, Math.max(0.8, (budget / total) * 100));
  const parts = PARTS.filter((p) => min[p.key] > 0);
  return (
    <div className="mt-6 rounded-2xl border border-line bg-haze/60 p-4 md:p-5">
      <div className="num flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <p className="text-[13px] font-semibold uppercase tracking-wider text-ink-mute">Cheapest workable</p>
          <p className="text-[28px] font-bold leading-tight md:text-[32px]">{money(total, currency)}</p>
        </div>
        <div className="text-right">
          <p className="text-[13px] font-semibold uppercase tracking-wider text-ink-mute">Your budget</p>
          <p className="text-[22px] font-bold leading-tight text-ink-soft md:text-[24px]">{money(budget, currency)}</p>
        </div>
      </div>

      <div className="relative mt-4" aria-hidden>
        <div className="flex h-3.5 w-full overflow-hidden rounded-full bg-haze-2">
          {parts.map((p) => (
            <span key={p.key} className={`${p.tone} h-full border-r-2 border-paper last:border-r-0`} style={{ width: `${(min[p.key] / total) * 100}%` }} />
          ))}
        </div>
        <span className="absolute -top-1.5 h-[26px] w-[3px] -translate-x-1/2 rounded-full bg-caution ring-2 ring-paper" style={{ left: `${at}%` }} />
      </div>
      <p className="num mt-2.5 text-[14px] font-semibold text-caution">
        {money(min.over_by, currency)} over budget
        {budget > 0 && <span className="font-normal text-ink-mute"> · the budget covers about {Math.round((budget / total) * 100)}% of it</span>}
      </p>

      <dl className="num mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5 text-[14px] sm:grid-cols-4">
        {parts.map((p) => (
          <div key={p.key} className="flex items-start gap-2">
            <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${p.tone}`} aria-hidden />
            <div className="min-w-0">
              <dt className="text-ink-mute">{p.label}</dt>
              <dd className="font-semibold">{money(min[p.key], currency)}</dd>
            </div>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Shown when the engine proves the trip cannot work as requested. Each adjustment is re-planned by the engine, not guessed. */
export function InfeasibleView({ feasibility, onApply, busy, readOnly, hasPlan, currency }: {
  feasibility: Feasibility; onApply: (a: Adjustment) => void; busy?: boolean; readOnly?: boolean; hasPlan?: boolean; currency?: string;
}) {
  const min = feasibility.minimum;
  const over = min && typeof min.over_by === "number" ? min.over_by : null;
  const adjs = feasibility.adjustments ?? [];
  const primary = pickPrimary(adjs, over);
  const rest = adjs.filter((a) => a !== primary);
  const Icon = feasibility.kind === "time" ? Clock : CircleAlert;

  return (
    <section className="card relative overflow-hidden p-5 sm:p-6 md:p-8" aria-labelledby="infeasible-title" role="alert">
      <span aria-hidden className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-caution via-caution/70 to-signal" />
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-caution-soft text-caution"><Icon className="h-5 w-5" aria-hidden /></span>
        <div className="min-w-0">
          <h2 id="infeasible-title" className="text-[22px] font-bold leading-tight md:text-2xl">{feasibility.message}</h2>
          {hasPlan && <p className="mt-1 text-[14px] text-ink-mute">Your last working plan is kept below until you choose a change.</p>}
        </div>
      </div>

      {min && min.total > 0 && over !== null && <BudgetGap min={min} currency={currency} />}

      {feasibility.main_conflict && (
        <p className="mt-5 border-l-[3px] border-caution/60 pl-4 text-[16px] leading-relaxed text-ink-soft">{feasibility.main_conflict}</p>
      )}

      {primary && (
        <div className="mt-6 flex flex-col gap-4 rounded-2xl bg-night p-5 text-white sm:flex-row sm:items-center sm:justify-between md:p-6">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-[13px] font-semibold uppercase tracking-wider text-signal">
              <Sparkles className="h-3.5 w-3.5" aria-hidden />Quickest fix
            </p>
            <p className="mt-1 text-[18px] font-bold leading-snug">{primary.label}</p>
            <p className="mt-0.5 text-[14px] text-white/70">{primary.description}</p>
          </div>
          <button className="btn-primary shrink-0 !rounded-full !px-5 !py-3" disabled={busy || readOnly} onClick={() => onApply(primary)}>
            {readOnly ? "Sign in to apply" : busy ? "Re-planning…" : "Apply and re-plan"}<ArrowRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}

      {rest.length > 0 && (
        <>
          <h3 className="mt-7 text-[16px] font-bold text-ink-soft">Or change something else</h3>
          <ul className="mt-3 grid gap-3 md:grid-cols-2">
            {rest.map((a) => {
              const elsewhere = a.apply?.action === "change_destination";
              const enough = over !== null && !elsewhere && fixes(a, over);
              return (
                <li key={a.key + a.label} className="flex items-center justify-between gap-3 rounded-2xl border border-line bg-paper p-4 transition-all hover:-translate-y-px hover:border-signal/40 hover:shadow-[0_10px_24px_-16px_rgb(var(--shadow)/0.3)]">
                  <div className="min-w-0">
                    <p className="font-bold leading-snug">{a.label}</p>
                    <p className="mt-0.5 text-[14px] text-ink-soft">{a.description}</p>
                    {over !== null && !!a.saves && a.saves >= Math.max(1, over * 0.02) && a.key !== "higher_budget" && (
                      <p className={`num mt-1.5 inline-block rounded-full px-2.5 py-0.5 text-[13px] font-semibold ${enough ? "bg-teal-soft text-teal-deep" : "bg-haze-2 text-ink-soft"}`}>
                        {enough ? "Fixes it on its own" : `Saves ${money(a.saves, currency)} · not enough alone`}
                      </p>
                    )}
                  </div>
                  {elsewhere ? (
                    <Link href="/plan" className="btn-ghost shrink-0 !rounded-full !px-4 !py-2 text-[14px]"><MapPin className="h-4 w-4" aria-hidden />Choose</Link>
                  ) : (
                    <button className="btn-primary shrink-0 !rounded-full !px-4 !py-2 text-[14px]" disabled={busy || readOnly} onClick={() => onApply(a)}
                            aria-label={`${readOnly ? "Sign in to apply" : "Apply"}: ${a.label}`}>
                      {readOnly ? "Sign in" : "Apply"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

export function QuotaPanel({ quota, message, next }: { quota: Quota | null; message: string; next?: string }) {
  const tail = next ? `&next=${encodeURIComponent(next)}` : "";
  const plus = quota?.prices.plus_monthly_inr ?? 99;
  const one = quota?.prices.one_trip_inr ?? 49;
  return (
    <section className="mx-auto max-w-xl rounded-ticket bg-night p-7 text-white shadow-2xl md:p-9" aria-labelledby="quota-title">
      <Lock className="h-7 w-7 text-signal" aria-hidden />
      <h2 id="quota-title" className="mt-3 text-2xl font-bold">{message}</h2>
      <p className="mt-2 text-white/75">Your trip is saved. Pick an option and we&apos;ll plan it right away. Changes to trips you&apos;ve already planned stay free.</p>
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <Link href={`/account?upgrade=one_trip${tail}`} className="rounded-2xl border border-white/20 p-4 hover:border-signal">
          <span className="block text-sm text-white/60">One trip</span>
          <span className="num block text-2xl font-bold">{inr(one)}</span>
          <span className="block text-sm text-white/70">Plan this trip</span>
        </Link>
        <Link href={`/account?upgrade=plus${tail}`} className="rounded-2xl border border-signal bg-signal/10 p-4 hover:bg-signal/20">
          <span className="block text-sm text-signal">Plus</span>
          <span className="num block text-2xl font-bold">{inr(plus)}<span className="text-base font-medium text-white/60"> /month</span></span>
          <span className="block text-sm text-white/70">Unlimited trip plans</span>
        </Link>
      </div>
      {quota?.payments_mode === "mock" && <p className="mt-4 text-sm text-white/50">Payments are in test mode; no money is charged.</p>}
    </section>
  );
}
