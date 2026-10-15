"use client";

import { useMemo, useState } from "react";
import { Car, Check, Minus, TrainFront, Zap } from "lucide-react";
import { eur } from "@/lib/format";
import { brand } from "@/lib/brand";
import { Reveal, useCountUp } from "../Reveal";

// Illustrative sample data (a couple, Rome -> Florence and back in a day). The real planner uses live, verified data.
type Style = "budget" | "balanced" | "premium";
const TRANSPORT = [
  { key: "reg", label: "Regionale Veloce, both ways", cost: 50, icon: TrainFront },
  { key: "fr", label: "Frecciarossa high-speed, both ways", cost: 118, icon: Zap },
  { key: "car", label: "Private driver, both ways", cost: 420, icon: Car },
];
const STOPS = [
  { name: "Duomo and Baptistery pass", cost: 60, kind: "Sight", priority: 9 },
  { name: "Uffizi Gallery", cost: 58, kind: "Sight", priority: 8 },
  { name: "Lunch at a trattoria", cost: 60, cheap: 30, kind: "Meal", priority: 10 },
  { name: "Accademia, Michelangelo's David", cost: 32, kind: "Sight", priority: 7 },
  { name: "Ponte Vecchio gelato stop", cost: 10, kind: "Treat", priority: 5 },
  { name: "Sunset at Piazzale Michelangelo", cost: 4, kind: "Sunset", priority: 6 },
  { name: "Dinner in Oltrarno", cost: 90, cheap: 45, kind: "Meal", priority: 10 },
  { name: "Palazzo Pitti and Boboli", cost: 32, kind: "Sight", priority: 4 },
];
const MIN = 100, MAX = 700, ESSENTIAL_MEALS = STOPS.filter((s) => s.kind === "Meal").reduce((a, s) => a + (s.cheap ?? s.cost), 0);
// The cheapest honest day: slowest train, basic meals, a small float, then the 7% buffer on top.
const FLOOR = Math.ceil(((TRANSPORT[0].cost + ESSENTIAL_MEALS + 20) * 1.07) / 10) * 10;

function plan(budget: number, style: Style) {
  const room = budget / 1.07;
  const order = style === "budget" ? [0, 1, 2] : style === "premium" ? [2, 1, 0] : [1, 0, 2];
  let transport = TRANSPORT[0];
  for (const i of order) {
    const t = TRANSPORT[i];
    if (t.cost + ESSENTIAL_MEALS + 20 <= room) { transport = t; break; }
  }
  let spent = transport.cost;
  const kept: { name: string; cost: number; kind: string }[] = [];
  for (const s of [...STOPS].sort((a, b) => b.priority - a.priority)) {
    const cost = style === "budget" && s.cheap ? s.cheap : s.cost;
    if (spent + cost <= room) { spent += cost; kept.push({ name: s.name, cost, kind: s.kind }); }
  }
  const feasible = budget >= FLOOR && kept.some((k) => k.kind === "Meal");
  const buffer = Math.max(0, Math.min(Math.round(spent * 0.07), budget - spent));
  const stops = spent - transport.cost;
  return { transport, kept, stops, buffer, planned: spent + buffer, remaining: budget - spent - buffer, feasible };
}

const PRESETS = [{ label: "Shoestring", v: 200 }, { label: "Comfortable", v: 400 }, { label: "Splurge", v: 650 }];

export function InteractivePlanner() {
  const [budget, setBudget] = useState(400);
  const [style, setStyle] = useState<Style>("balanced");
  const p = useMemo(() => plan(budget, style), [budget, style]);
  const Icon = p.transport.icon;
  const planned = useCountUp(p.planned, 500);
  const remaining = useCountUp(p.remaining, 500);
  const fill = ((budget - MIN) / (MAX - MIN)) * 100;
  const seg = (v: number) => `${Math.max(0, (v / budget) * 100)}%`;

  return (
    <section id="try" className="relative py-16 md:py-20" aria-labelledby="try-heading">
      <div className="container-x grid gap-8 lg:grid-cols-[0.9fr_1.1fr] lg:items-center lg:gap-12">
        <Reveal className="min-w-0">
          <span className="eyebrow">Live demo</span>
          <h2 id="try-heading" className="mt-4 text-title font-bold">Move the budget. <span className="accent highlight">Watch the day change.</span></h2>
          <p className="mt-3 max-w-md leading-relaxed text-ink-soft">
            A sample couple&apos;s day trip, Rome to Florence. Every euro is counted before anything gets added. No pretending.
          </p>
          <div className="spotlight mt-6 card rounded-2xl p-4 md:p-5">
            <label htmlFor="budget-range" className="flex items-baseline justify-between font-semibold">
              <span>Total budget for two</span>
              <span className="num text-xl">{eur(budget)}</span>
            </label>
            <input id="budget-range" type="range" min={MIN} max={MAX} step={10} value={budget}
                   onChange={(e) => setBudget(Number(e.target.value))} aria-valuetext={`${eur(budget)}, between ${eur(MIN)} and ${eur(MAX)}`}
                   className="range mt-4 w-full" style={{ ["--fill" as string]: `${fill}%` }} />
            <div className="mt-3 flex flex-wrap gap-1.5">
              {PRESETS.map((pr) => (
                <button key={pr.v} type="button" onClick={() => setBudget(pr.v)}
                        className={`rounded-full border px-2.5 py-1 text-[12px] font-medium transition-all duration-200 ${
                          budget === pr.v ? "border-signal bg-signal-soft text-ink" : "border-line text-ink-soft hover:border-ink/30 hover:text-ink"}`}>
                  {pr.label}<span className="hidden sm:inline"> · {eur(pr.v)}</span>
                </button>
              ))}
            </div>
            <fieldset className="mt-4 flex items-center justify-between gap-3 border-t border-line pt-4">
              <legend className="sr-only">Style</legend>
              <span aria-hidden className="font-semibold">Style</span>
              <div className="flex rounded-full bg-haze p-1">
                {(["budget", "balanced", "premium"] as Style[]).map((st) => (
                  <button key={st} type="button" onClick={() => setStyle(st)} aria-pressed={style === st}
                          className={`rounded-full px-3 py-1 text-[13px] font-medium transition-all duration-200 ${
                            style === st ? "bg-signal text-[rgb(var(--on-signal))] shadow-[0_4px_12px_-6px_rgb(var(--signal)/0.8)]" : "text-ink-soft hover:text-ink"}`}>
                    {st[0].toUpperCase() + st.slice(1)}
                  </button>
                ))}
              </div>
            </fieldset>
          </div>
        </Reveal>

        <Reveal delay={120} className="min-w-0">
          <div className="spotlight overflow-hidden card rounded-2xl" aria-live="polite">
            {p.feasible ? (
              <>
                <div className="flex items-center gap-3 border-b border-line px-4 py-3.5 md:px-6">
                  <span key={p.transport.key} className="fade-up grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-night text-signal ring-1 ring-signal/20">
                    <Icon className="h-4 w-4" aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[15px] font-semibold leading-snug">{p.transport.label}</p>
                    <p className="truncate text-[13px] text-ink-mute">Roma Termini ⇄ Firenze S.M.N., for two</p>
                  </div>
                  <span className="num font-semibold">{eur(p.transport.cost)}</span>
                </div>
                <ul className="divide-y divide-line px-4 md:px-6">
                  {STOPS.map((s) => {
                    const k = p.kept.find((x) => x.name === s.name);
                    return (
                      <li key={s.name} className={`flex items-center gap-3 py-2 text-[14px] transition-all duration-300 ${k ? "" : "opacity-40"}`}>
                        <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full transition-colors duration-300 ${k ? "bg-teal-soft text-teal" : "bg-haze-2 text-ink-mute"}`}>
                          {k ? <Check className="h-3 w-3" aria-label="Included" /> : <Minus className="h-3 w-3" aria-label="Left out" />}
                        </span>
                        <span className={`min-w-0 flex-1 truncate ${k ? "" : "line-through"}`}>{s.name}</span>
                        <span className="hidden text-xs text-ink-mute sm:inline">{s.kind}</span>
                        <span className="num w-12 text-right font-medium">{k ? eur(k.cost) : "—"}</span>
                      </li>
                    );
                  })}
                </ul>
                <div className="m-4 mt-2 rounded-xl bg-haze px-4 py-3 md:mx-6">
                  <div className="flex h-2 overflow-hidden rounded-full bg-haze-2" role="img" aria-label="How the budget is used">
                    <span className="bg-night-3 transition-all duration-500 dark:bg-ink-soft" style={{ width: seg(p.transport.cost) }} />
                    <span className="bg-signal transition-all duration-500" style={{ width: seg(p.stops) }} />
                    <span className="bg-spark/70 transition-all duration-500" style={{ width: seg(p.buffer) }} />
                  </div>
                  <div className="num mt-3 grid grid-cols-3 gap-3 text-center text-[15px]">
                    <div><p className="text-xs text-ink-mute">Budget</p><p className="font-bold">{eur(budget)}</p></div>
                    <div><p className="text-xs text-ink-mute">Planned</p><p className="font-bold">{eur(Math.round(planned))}</p></div>
                    <div><p className="text-xs text-ink-mute">Remaining</p><p className="font-bold text-teal-deep">{eur(Math.round(remaining))}</p></div>
                  </div>
                </div>
              </>
            ) : (
              <div className="p-6">
                <span className="inline-flex rounded-full bg-danger-soft px-3 py-1 text-xs font-semibold text-danger">Not feasible</span>
                <p className="mt-4 text-xl font-bold">This trip doesn&apos;t fit in {eur(budget)}.</p>
                <p className="mt-3 text-ink-soft">Even the slow train plus two simple meals costs more than this for two. {brand.name} would suggest somewhere closer, or at least {eur(FLOOR)}, instead of quietly skipping meals.</p>
                <button type="button" onClick={() => setBudget(FLOOR)} className="btn-primary mt-6">Set budget to {eur(FLOOR)}</button>
              </div>
            )}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
