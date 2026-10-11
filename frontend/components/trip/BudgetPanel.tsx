"use client";

import { ChevronDown } from "lucide-react";
import type { BudgetBreakdown, ExpenseSummary } from "@/lib/types";
import { money } from "@/lib/format";
import { VerificationBadge } from "../VerificationBadge";

const STATUS: Record<string, { label: string; cls: string; note: string }> = {
  within: { label: "Within budget", cls: "bg-teal-soft text-teal-deep", note: "Everything planned fits, with a safety buffer kept aside." },
  tight: { label: "Tight", cls: "bg-signal-soft text-signal-deep", note: "It fits, but there is little room for surprises. Keep some cash spare." },
  over: { label: "Over budget", cls: "bg-danger-soft text-danger", note: "The plan costs more than your budget. Try one of the cheaper options." },
};

// Category colours stay clear of the provenance colours' meanings, and every one is visible in both themes.
const BAR: Record<string, string> = {
  transport: "bg-ink", stay: "bg-spark", food: "bg-signal", activities: "bg-ink-mute", local: "bg-signal/45", buffer: "bg-line",
};

export function BudgetPanel({ budget, expenses, currency }: { budget: BudgetBreakdown; expenses?: ExpenseSummary; currency?: string }) {
  const st = STATUS[budget.status] ?? STATUS.within;
  const cats = budget.categories.filter((c) => c.key !== "buffer" && (c.amount > 0 || c.items.length));
  const buffer = budget.categories.find((c) => c.key === "buffer")?.amount ?? Math.max(0, budget.planned - budget.subtotal);
  const scale = Math.max(budget.budget, budget.planned);
  const spent = expenses?.total ?? 0;
  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-line bg-paper/85 p-5 backdrop-blur md:p-7" aria-labelledby="budget-summary">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="budget-summary" className="text-lg font-bold">Where the money goes</h2>
            <p className="mt-1 max-w-md text-[16px] text-ink-soft">{st.note}</p>
          </div>
          <span className={`rounded-full px-3 py-1 text-sm font-semibold ${st.cls}`}>{st.label}</span>
        </div>

        <dl className="num mt-5 grid grid-cols-2 gap-4 md:grid-cols-4">
          {[
            ["Your budget", money(budget.budget, currency)], ["Planned", money(budget.planned, currency)],
            ["Remaining", money(budget.remaining, currency)], ["Per person", money(Math.round(budget.per_person), currency)],
          ].map(([k, v]) => (
            <div key={k} className="rounded-xl border border-line bg-haze/60 px-4 py-3">
              <dt className="text-sm text-ink-mute">{k}</dt>
              <dd className={`text-xl font-bold ${k === "Remaining" && budget.remaining < 0 ? "text-danger" : ""}`}>{v}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-6 flex h-4 gap-[3px] overflow-hidden rounded-full bg-haze-2" role="img"
             aria-label={`Planned ${money(budget.planned, currency)} of ${money(budget.budget, currency)}`}>
          {cats.map((c) => (
            <div key={c.key} title={`${c.label}: ${money(c.amount, currency)}`} className={`${BAR[c.key] ?? "bg-ink-mute"} rounded-sm transition-opacity hover:opacity-80`} style={{ width: `${(c.amount / scale) * 100}%` }} />
          ))}
          {buffer > 0 && <div title={`Safety buffer: ${money(buffer, currency)}`} className="rounded-sm bg-[repeating-linear-gradient(45deg,rgb(var(--line))_0_4px,rgb(var(--haze))_4px_8px)]" style={{ width: `${(buffer / scale) * 100}%` }} />}
        </div>
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-ink-soft">
          {cats.map((c) => (
            <li key={c.key} className="inline-flex items-center gap-1.5"><span className={`h-2.5 w-2.5 rounded-full ${BAR[c.key] ?? "bg-ink-mute"}`} />{c.label}</li>
          ))}
          {buffer > 0 && <li className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-line" />Safety buffer ({budget.buffer_pct}%)</li>}
        </ul>
      </section>

      <section className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-paper/85 backdrop-blur" aria-label="Budget by category">
        {cats.map((c) => {
          const diff = c.typical ? c.amount - c.typical : 0;
          return (
            <details key={c.key} className="group p-5 transition-colors open:bg-haze/40 hover:bg-haze/40 md:px-6" open={c.key === "transport"}>
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                <span className="flex min-w-0 flex-1 items-center gap-2.5">
                  <span className={`h-3 w-3 shrink-0 rounded-full ${BAR[c.key] ?? "bg-ink-mute"}`} />
                  <span className="font-semibold">{c.label}</span>
                  <span className="num ml-1 hidden text-xs text-ink-mute sm:inline">{Math.round((c.amount / Math.max(1, budget.planned)) * 100)}%</span>
                  <ChevronDown className="h-4 w-4 text-ink-mute transition-transform group-open:rotate-180" aria-hidden />
                </span>
                <span className="num text-right">
                  <span className="font-bold">{money(c.amount, currency)}</span>
                  {c.typical > 0 && (
                    <span className="block text-[13px] text-ink-mute">
                      {Math.abs(diff) < 1 ? "About typical" : diff < 0 ? `${money(-diff, currency)} under typical` : `${money(diff, currency)} over typical`}
                    </span>
                  )}
                </span>
              </summary>
              {c.items.length > 0 && (
                <ul className="mt-3 space-y-2 pl-5">
                  {c.items.map((l, i) => (
                    <li key={i} className="flex items-start justify-between gap-3 text-[14px]">
                      <span className="text-ink-soft">{l.label} <VerificationBadge state={l.state} /></span>
                      <span className="num shrink-0 font-semibold">{money(l.amount, currency)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          );
        })}
        <div className="flex items-center justify-between gap-3 p-5 md:px-6">
          <span>
            <span className="font-semibold">Safety buffer</span>
            <span className="block text-[13px] text-ink-mute">Kept aside for fare changes, tips and small extras</span>
          </span>
          <span className="num font-bold">{money(buffer, currency)}</span>
        </div>
      </section>

      {expenses && (
        <section className="rounded-2xl border border-line bg-paper/85 p-5 backdrop-blur md:p-7" aria-labelledby="actual-vs-plan">
          <h2 id="actual-vs-plan" className="text-lg font-bold">Spent so far</h2>
          <p className="mt-1 text-[16px] text-ink-soft">What the group has actually logged, compared with the plan.</p>
          <div className="num mt-4 flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <p><span className="text-2xl font-bold">{money(spent, currency)}</span> <span className="text-ink-mute">of {money(budget.planned, currency)} planned</span></p>
            <p className="text-ink-soft">{spent <= budget.budget ? `${money(budget.budget - spent, currency)} left in the budget` : `${money(spent - budget.budget, currency)} over budget`}</p>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-haze">
            <div className={`h-full rounded-full ${spent > budget.budget ? "bg-danger" : "bg-teal"}`}
                 style={{ width: `${Math.min(100, (spent / Math.max(1, budget.budget)) * 100)}%` }} />
          </div>
        </section>
      )}
      <p className="text-sm text-ink-mute">Prices come from the sources shown on each line. Estimated and sample prices can change; confirm before you book.</p>
    </div>
  );
}