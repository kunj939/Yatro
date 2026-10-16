"use client";

import { useMemo, useState } from "react";
import { ChevronDown, PiggyBank, ShieldCheck, Users, Wallet } from "lucide-react";
import type { BudgetBreakdown, ExpenseSummary, Itinerary } from "@/lib/types";
import { money, niceDate } from "@/lib/format";
import { BUFFER, CATS, compact, dailySpend, rings, slicesFromBudget, ticks, type Slice } from "@/lib/budgetViz";
import { VerificationBadge } from "../VerificationBadge";

const STATUS: Record<string, { label: string; cls: string; note: string; bar: string }> = {
  within: { label: "Within budget", cls: "bg-teal-soft text-teal-deep", note: "Everything planned fits, with a safety buffer kept aside.", bar: "bg-signal" },
  tight: { label: "Tight", cls: "bg-signal-soft text-signal-deep", note: "It fits, but there's little room for surprises. Keep some cash spare.", bar: "bg-signal" },
  over: { label: "Over budget", cls: "bg-danger-soft text-danger", note: "The plan costs more than your budget. Try one of the cheaper options.", bar: "bg-danger" },
};
const HATCH = "repeating-linear-gradient(45deg,#c9cdc6 0 3px,#eceee9 3px 6px)";
const swatch = (s: { key: string; light: string }) => (s.key === "buffer" ? { background: HATCH } : { background: s.light });

export function BudgetPanel({ budget, days, expenses, currency }:
  { budget: BudgetBreakdown; days: Itinerary["days"]; expenses?: ExpenseSummary; currency?: string }) {
  const st = STATUS[budget.status] ?? STATUS.within;
  const slices = useMemo(() => slicesFromBudget(budget), [budget]);
  const daily = useMemo(() => dailySpend(days), [days]);
  const buffer = slices.find((s) => s.key === "buffer")?.amount ?? 0;
  const used = Math.min(100, (budget.planned / Math.max(1, budget.budget)) * 100);
  const spent = expenses?.total ?? 0;
  const m = (v: number) => money(Math.round(v), currency);

  return (
    <div className="space-y-5">
      {/* the one number this tab leads with, and how it sits against the budget */}
      <section className="card overflow-hidden" aria-labelledby="budget-summary">
        <div className="relative p-5 md:p-7">
          <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-signal/10 blur-3xl" />
          <div className="relative flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 id="budget-summary" className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-mute">Planned for this trip</h2>
              <p className="mt-1.5 flex flex-wrap items-baseline gap-x-2">
                <span className="headline-font text-[44px] font-extrabold leading-none text-ink md:text-[52px]">{m(budget.planned)}</span>
                <span className="text-[15px] text-ink-mute">of {m(budget.budget)} budget</span>
              </p>
              <p className="mt-2 max-w-md text-[15px] text-ink-soft">{st.note}</p>
            </div>
            <span className={`rounded-full px-3 py-1 text-sm font-semibold ${st.cls}`}>{st.label}</span>
          </div>
          <div className="relative mt-5 h-2.5 overflow-hidden rounded-full bg-signal-soft" role="meter" aria-valuemin={0} aria-valuemax={100}
               aria-valuenow={Math.round(used)} aria-label={`${Math.round(used)}% of the budget planned`}>
            <div className={`h-full rounded-full ${st.bar} transition-[width] duration-700`} style={{ width: `${used}%` }} />
          </div>
          <dl className="num relative mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile icon={Wallet} label="Remaining" value={m(budget.remaining)} tone={budget.remaining < 0 ? "text-danger" : ""} />
            <Tile icon={Users} label={budget.travellers > 1 ? `Per person (${budget.travellers})` : "Per person"} value={m(budget.per_person)} />
            <Tile icon={ShieldCheck} label={`Safety buffer (${budget.buffer_pct}%)`} value={m(buffer)} />
            <Tile icon={PiggyBank} label="Logged so far" value={m(spent)} />
          </dl>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Donut slices={slices} total={budget.planned} m={m} />
        <DailyColumns daily={daily} m={m} currency={currency} />
      </div>

      <LineByLine budget={budget} m={m} />

      {expenses && expenses.total > 0 && <SpentVsPlan budget={budget} expenses={expenses} m={m} />}

      <p className="text-sm text-ink-mute">Prices come from the sources shown on each line. Estimated and sample prices can change; confirm before you book.</p>
    </div>
  );
}

function Tile({ icon: Icon, label, value, tone = "" }: { icon: any; label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-2xl border border-line bg-paper/80 px-4 py-3">
      <dt className="flex items-center gap-1.5 text-[13px] text-ink-mute"><Icon className="h-3.5 w-3.5" aria-hidden />{label}</dt>
      <dd className={`mt-0.5 text-[19px] font-bold ${tone || "text-ink"}`}>{value}</dd>
    </div>
  );
}

/** Part-to-whole at a glance (six slices at most). The list beside it carries every value, so nothing rests on colour. */
function Donut({ slices, total, m }: { slices: Slice[]; total: number; m: (v: number) => string }) {
  const [hover, setHover] = useState<string | null>(null);
  const segs = rings(slices, 100, 100, 64, 92, 3);
  const on = segs.find((s) => s.key === hover);
  return (
    <section className="card p-5 md:p-6" aria-labelledby="donut-title">
      <h3 id="donut-title" className="font-bold">Where the money goes</h3>
      <p className="text-[13px] text-ink-mute">Share of everything planned</p>
      <div className="mt-4 flex flex-col items-center gap-5 sm:flex-row sm:items-center">
        <svg viewBox="0 0 200 200" className="h-44 w-44 shrink-0" role="img" aria-label="Budget split by category">
          <defs>
            <pattern id="buffer-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="#eceee9" /><rect width="3" height="6" fill="#c9cdc6" />
            </pattern>
          </defs>
          {segs.map((s) => (
            <path key={s.key} d={s.d} fill={s.key === "buffer" ? "url(#buffer-hatch)" : s.light}
                  opacity={hover && hover !== s.key ? 0.35 : 1} className="cursor-pointer transition-opacity duration-200"
                  onMouseEnter={() => setHover(s.key)} onMouseLeave={() => setHover(null)}>
              <title>{`${s.label}: ${m(s.amount)} (${Math.round(s.share * 100)}%)`}</title>
            </path>
          ))}
          <text x="100" y="94" textAnchor="middle" className="fill-ink-mute text-[11px] font-medium">{on ? on.label : "Planned"}</text>
          <text x="100" y="116" textAnchor="middle" className="num fill-ink text-[20px] font-extrabold">{on ? m(on.amount) : m(total)}</text>
          {on && <text x="100" y="134" textAnchor="middle" className="num fill-ink-mute text-[11px]">{Math.round(on.share * 100)}%</text>}
        </svg>
        <ul className="num w-full min-w-[12rem] flex-1 space-y-1">
          {segs.map((s) => (
            <li key={s.key} onMouseEnter={() => setHover(s.key)} onMouseLeave={() => setHover(null)}
                className={`flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[14px] transition-colors ${hover === s.key ? "bg-haze" : ""}`}>
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={swatch(s)} aria-hidden />
              <span className="min-w-0 flex-1 truncate text-ink-soft">{s.label}</span>
              <span className="w-9 shrink-0 text-right text-[12px] text-ink-mute">{Math.round(s.share * 100)}%</span>
              <span className="shrink-0 text-right font-semibold text-ink">{m(s.amount)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/** What each day costs, stacked by category in the fixed order, with a hover breakdown and the total on each cap. */
function DailyColumns({ daily, m, currency }: { daily: ReturnType<typeof dailySpend>; m: (v: number) => string; currency?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...daily.map((d) => d.total));
  const axis = ticks(max), top = axis[axis.length - 1] || 1;
  const H = 180;
  const many = daily.length > 10;
  const peak = daily.reduce((a, d) => (d.total > a.total ? d : a), daily[0]);
  return (
    <section className="card p-5 md:p-6" aria-labelledby="daily-title">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id="daily-title" className="font-bold">Spend by day</h3>
          <p className="text-[13px] text-ink-mute">The hotel is spread over the nights it covers</p>
        </div>
        {peak && daily.length > 1 && <p className="num text-[13px] text-ink-mute">Busiest: <span className="font-semibold text-ink">{peak.label}, {m(peak.total)}</span></p>}
      </div>
      <div className="relative mt-5 flex gap-2">
        <div className="num relative w-9 shrink-0 text-right text-[11px] text-ink-mute" style={{ height: H }} aria-hidden>
          {axis.map((t) => <span key={t} className="absolute right-0 -translate-y-1/2" style={{ top: H - (t / top) * H }}>{compact(t, currency)}</span>)}
        </div>
        <div className="relative min-w-0 flex-1">
          <div className="pointer-events-none absolute inset-x-0 top-0" style={{ height: H }} aria-hidden>
            {axis.map((t) => <span key={t} className="absolute inset-x-0 border-t border-line" style={{ top: H - (t / top) * H }} />)}
          </div>
          <ol className="relative flex items-end justify-around gap-1" style={{ height: H }} aria-label="Cost per day">
            {daily.map((d, n) => (
              <li key={d.index} className="relative flex h-full flex-1 cursor-default flex-col items-center justify-end"
                  onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(null)} tabIndex={0} onFocus={() => setHover(n)} onBlur={() => setHover(null)}
                  aria-label={`${d.label}: ${m(d.total)}`}>
                {!many && <span className="num mb-1 text-[11px] font-semibold text-ink-soft">{compact(d.total, currency)}</span>}
                <span className={`flex w-full max-w-[24px] flex-col-reverse gap-[2px] overflow-hidden rounded-t-[4px] transition-opacity ${hover !== null && hover !== n ? "opacity-40" : ""}`}
                      style={{ height: (d.total / top) * H }}>
                  {CATS.map((c) => d.parts[c.key] > 0 && (
                    <span key={c.key} style={{ height: `${(d.parts[c.key] / d.total) * 100}%`, background: c.light, minHeight: 2 }} />
                  ))}
                </span>
                {hover === n && (
                  <span className={`num absolute bottom-full z-10 mb-2 w-48 rounded-xl border border-line bg-paper p-3 text-left text-[12px] shadow-lift ${
                    n > daily.length / 2 ? "right-0" : "left-0"}`} role="tooltip">
                    <span className="block font-semibold text-ink">{d.label} · {niceDate(d.date, { weekday: "short", day: "numeric", month: "short" })}</span>
                    {CATS.filter((c) => d.parts[c.key] > 0).map((c) => (
                      <span key={c.key} className="mt-1 flex items-center gap-1.5 text-ink-soft">
                        <span className="h-2 w-2 rounded-full" style={{ background: c.light }} />{c.label}<span className="ml-auto font-semibold text-ink">{m(d.parts[c.key])}</span>
                      </span>
                    ))}
                    <span className="mt-1.5 flex border-t border-line pt-1.5 font-semibold text-ink">Total<span className="ml-auto">{m(d.total)}</span></span>
                  </span>
                )}
              </li>
            ))}
          </ol>
          <ol className="mt-2 flex justify-around gap-1 text-center text-[11px] text-ink-mute" aria-hidden>
            {daily.map((d, n) => <li key={d.index} className="flex-1 truncate">{many ? (n % 2 ? "" : n + 1) : d.label.replace("Day ", "D")}</li>)}
          </ol>
        </div>
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-soft">
        {CATS.filter((c) => daily.some((d) => d.parts[c.key] > 0)).map((c) => (
          <li key={c.key} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: c.light }} />{c.label}</li>
        ))}
      </ul>
      <table className="sr-only">
        <caption>Cost per day</caption>
        <thead><tr><th>Day</th>{CATS.map((c) => <th key={c.key}>{c.label}</th>)}<th>Total</th></tr></thead>
        <tbody>{daily.map((d) => <tr key={d.index}><td>{d.label}</td>{CATS.map((c) => <td key={c.key}>{m(d.parts[c.key])}</td>)}<td>{m(d.total)}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

/** Every line of the plan's money, one category at a time (biggest open first), instead of a wall of cards. */
function LineByLine({ budget, m }: { budget: BudgetBreakdown; m: (v: number) => string }) {
  const cats = budget.categories.filter((c) => c.key !== "buffer" && (c.amount > 0 || c.items.length))
    .sort((a, b) => b.amount - a.amount).map((c) => ({ ...c, items: [...c.items].sort((a, b) => b.amount - a.amount) }));
  const [open, setOpen] = useState<string | null>(cats[0]?.key ?? null);
  const color = (k: string) => CATS.find((c) => c.key === k)?.light ?? BUFFER.light;
  return (
    <section className="card overflow-hidden" aria-labelledby="lines-title">
      <div className="px-5 pt-5 md:px-6">
        <h3 id="lines-title" className="font-bold">Line by line</h3>
        <p className="text-[13px] text-ink-mute">Tap a category to see every cost in it</p>
      </div>
      <ul className="mt-3 divide-y divide-line border-t border-line">
        {cats.map((c) => {
          const share = (c.amount / Math.max(1, budget.planned)) * 100;
          const diff = c.typical ? c.amount - c.typical : 0;
          const isOpen = open === c.key;
          return (
            <li key={c.key}>
              <button type="button" onClick={() => setOpen(isOpen ? null : c.key)} aria-expanded={isOpen}
                      className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition-colors hover:bg-haze/60 md:px-6">
                <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: color(c.key) }} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-semibold text-ink">{c.label}</span>
                    <span className="num text-[12px] text-ink-mute">{Math.round(share)}% · {c.items.length} {c.items.length === 1 ? "line" : "lines"}</span>
                  </span>
                  <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-haze-2" aria-hidden>
                    <span className="block h-full rounded-full" style={{ width: `${Math.min(100, share)}%`, background: color(c.key) }} />
                  </span>
                </span>
                <span className="num shrink-0 text-right">
                  <span className="block font-bold text-ink">{m(c.amount)}</span>
                  {/* "typical" is the usual share of this budget; only a clear overspend is worth a mention */}
                  {c.typical > 0 && diff > c.typical * 0.1 && <span className="block text-[12px] text-caution">{m(diff)} more than usual</span>}
                </span>
                <ChevronDown className={`h-4 w-4 shrink-0 text-ink-mute transition-transform ${isOpen ? "rotate-180" : ""}`} aria-hidden />
              </button>
              {isOpen && c.items.length > 0 && (
                <ul className="fade-up divide-y divide-line/60 bg-haze/40 px-5 md:px-6">
                  {c.items.map((l, i) => (
                    <li key={i} className="flex items-start justify-between gap-3 py-2.5 pl-6 text-[14px]">
                      <span className="min-w-0 text-ink-soft">{l.label} <VerificationBadge state={l.state} /></span>
                      <span className="num shrink-0 font-semibold text-ink">{m(l.amount)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** What the group has logged, against what was planned, per category. */
function SpentVsPlan({ budget, expenses, m }: { budget: BudgetBreakdown; expenses: ExpenseSummary; m: (v: number) => string }) {
  const planned = Object.fromEntries(budget.categories.map((c) => [c.key, c.amount]));
  const rows = [...CATS.map((c) => ({ key: c.key, label: c.label, color: c.light })),
    { key: "shopping", label: "Shopping", color: BUFFER.light }, { key: "other", label: "Other", color: BUFFER.light }]
    .map((r) => ({ ...r, plan: planned[r.key] ?? 0, spent: (expenses.by_category as Record<string, number>)[r.key] ?? 0 }))
    .filter((r) => r.plan > 0 || r.spent > 0);
  const over = expenses.total > budget.budget;
  return (
    <section className="card p-5 md:p-6" aria-labelledby="actual-vs-plan">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="actual-vs-plan" className="font-bold">Spent so far vs the plan</h3>
          <p className="text-[13px] text-ink-mute">What the group has logged in Hisaab</p>
        </div>
        <p className="num text-right">
          <span className="text-[22px] font-extrabold text-ink">{m(expenses.total)}</span>
          <span className="block text-[13px] text-ink-mute">{over ? `${m(expenses.total - budget.budget)} over budget` : `${m(budget.budget - expenses.total)} left in the budget`}</span>
        </p>
      </div>
      <ul className="num mt-4 space-y-3">
        {rows.map((r) => {
          const pct = r.plan > 0 ? Math.min(100, (r.spent / r.plan) * 100) : 100;
          return (
            <li key={r.key}>
              <div className="flex items-baseline justify-between gap-3 text-[14px]">
                <span className="flex items-center gap-2 text-ink-soft"><span className="h-2.5 w-2.5 rounded-full" style={{ background: r.color }} aria-hidden />{r.label}</span>
                <span><span className="font-semibold text-ink">{m(r.spent)}</span><span className="text-ink-mute"> of {r.plan ? m(r.plan) : "nothing planned"}</span></span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-haze-2" aria-hidden>
                <div className={`h-full rounded-full ${r.spent > r.plan ? "bg-caution" : ""}`} style={{ width: `${pct}%`, background: r.spent > r.plan ? undefined : r.color }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
