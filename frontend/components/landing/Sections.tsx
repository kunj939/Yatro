"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Bot, Check, Clock, Footprints, MapPin, Repeat, Route, ShieldCheck, Sparkles, Users, Wallet, X } from "lucide-react";
import { eur, inr } from "@/lib/format";
import { brand } from "@/lib/brand";
import { Reveal, useCountUp, useInView } from "../Reveal";

/* ---------------------------------------------------------------- Proof */

// Real counts from the engine's own data (backend: rail_stations.STATIONS, airports.AIRPORTS, utils/money.APPROX_INR_TO),
// as of Oct 2026. Proof the page can stand behind: no invented users, ratings or reviews.
const PROOF = [
  { n: 94, label: "railway stations mapped", sub: "plus the cab ride to each one" },
  { n: 109, label: "airports, India and abroad", sub: "flights start where planes actually do" },
  { n: 36, label: "currencies", sub: "every trip priced in local money, no mental maths" },
  { n: 0, label: "invented trains or timings", sub: "if we can't verify it, we say so" },
];

function ProofStat({ n, label, sub, start }: { n: number; label: string; sub: string; start: boolean }) {
  const v = Math.round(useCountUp(n, 1100, start));
  return (
    <div className="px-1 md:px-6 md:first:pl-0">
      <p className={`num leading-none ${n === 0 ? "accent highlight text-[52px] md:text-[64px]" : "headline-font text-[44px] font-extrabold text-ink md:text-[56px]"}`}>
        {n === 0 ? "Zero" : v}
      </p>
      <p className="mt-3 text-[15px] font-semibold text-ink">{label}</p>
      <p className="mt-0.5 text-[14px] text-ink-mute">{sub}</p>
    </div>
  );
}

export function ProofBand() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref);
  return (
    <section aria-label="What the engine knows" className="border-y border-line/70 bg-paper/30 backdrop-blur-sm">
      <div ref={ref} className="container-x grid grid-cols-2 gap-x-6 gap-y-10 py-12 md:grid-cols-4 md:divide-x md:divide-line/70 md:py-14">
        {PROOF.map((p) => <ProofStat key={p.label} {...p} start={seen} />)}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- Chatbot vs Yatro */

// One plain sentence per side, per topic. The left column is what a general-purpose chatbot does; the right is what this engine does.
const COMPARE = [
  { topic: "Prices", Icon: Wallet, bot: "Guesses from stale data.", yatro: "Real fares from live timetables. Estimates are always labelled." },
  { topic: "Opening hours", Icon: Clock, bot: "Might send you to a closed gate.", yatro: "Checked for the day and hour you'll land up there." },
  { topic: "Your budget", Icon: ShieldCheck, bot: "“Around ₹5,000, probably.”", yatro: "Every ticket, meal and auto added up. Never over budget." },
  { topic: "Who is travelling", Icon: Users, bot: "The same plan for everyone.", yatro: "Picks that suit the kids, the adults and the grandparents." },
  { topic: "A plan that can't work", Icon: AlertTriangle, bot: "Makes one up anyway.", yatro: "Tells you why, and the quickest fix." },
];

export function WhyDifferent() {
  return (
    <section id="features" className="py-20 md:py-28" aria-labelledby="compare-heading">
      <div className="container-x">
        <Reveal className="mx-auto max-w-3xl text-center">
          <span className="eyebrow mx-auto">Chatbot vs {brand.name}</span>
          <h2 id="compare-heading" className="mt-5 text-title font-bold">Chatbots guess. <span className="accent highlight">{brand.name} checks.</span></h2>
          <p className="mx-auto mt-4 max-w-2xl text-lg leading-relaxed text-ink-soft">
            A regular chatbot tells you a nice story. {brand.name} hands you a plan: real train times, real opening hours, real prices. With receipts.
          </p>
        </Reveal>

        <Reveal delay={80} className="spotlight mx-auto mt-12 max-w-5xl overflow-hidden rounded-3xl border border-line bg-paper/45 backdrop-blur">
          <div className="hidden grid-cols-[1fr_1.3fr_1.7fr] border-b border-line text-[13px] font-semibold uppercase tracking-[0.12em] md:grid">
            <div className="px-6 py-4" />
            <div className="flex items-center gap-2 px-6 py-4 text-ink-mute"><Bot className="h-4 w-4" aria-hidden />Normal chatbot</div>
            <div className="flex items-center gap-2 bg-signal-soft/50 px-6 py-4 text-signal-deep"><Sparkles className="h-4 w-4" aria-hidden />{brand.name}</div>
          </div>
          <ul className="divide-y divide-line">
            {COMPARE.map(({ topic, Icon, bot, yatro }) => (
              <li key={topic} className="grid md:grid-cols-[1fr_1.3fr_1.7fr]">
                <p className="flex items-center gap-3 px-5 pb-1 pt-5 text-[17px] font-bold md:px-6 md:py-5">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-haze-2 text-ink-soft"><Icon className="h-[18px] w-[18px]" aria-hidden /></span>
                  {topic}
                </p>
                <p className="flex items-start gap-2.5 px-5 py-2 text-[15px] leading-snug text-ink-mute md:px-6 md:py-5">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-danger-soft text-danger"><X className="h-3 w-3" strokeWidth={3} aria-hidden /></span>
                  <span><span className="mb-0.5 block text-[11px] font-semibold uppercase tracking-[0.12em] md:hidden">Chatbot</span>{bot}</span>
                </p>
                <p className="flex items-start gap-2.5 bg-signal-soft/30 px-5 pb-5 pt-2 text-[15px] font-medium leading-snug text-ink md:bg-signal-soft/40 md:px-6 md:py-5">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-teal-soft text-teal"><Check className="h-3 w-3" strokeWidth={3} aria-hidden /></span>
                  <span><span className="mb-0.5 block text-[11px] font-semibold uppercase tracking-[0.12em] text-signal-deep md:hidden">{brand.name}</span>{yatro}</span>
                </p>
              </li>
            ))}
          </ul>
        </Reveal>

        <Reveal delay={120} className="mt-8 text-center">
          <Link href="/#try" className="btn-ghost">See it in action <ArrowRight className="h-4 w-4" aria-hidden /></Link>
        </Reveal>
      </div>
    </section>
  );
}
/* ---------------------------------------------------------------- Budget */

const CATS = [
  { label: "Transport", amount: 2400, color: "bg-ink" },
  { label: "Food", amount: 3200, color: "bg-signal" },
  { label: "Activities", amount: 1800, color: "bg-spark" },
  { label: "Local travel", amount: 600, color: "bg-ink-mute" },
  { label: "Buffer", amount: 700, color: "bg-signal/40" },
];
const CAT_TOTAL = 10000;
const CAT_LEFT = CAT_TOTAL - CATS.reduce((a, c) => a + c.amount, 0);

// Two nights in Goa for two, 5-star, asked for within ₹20,000. Each fix is priced from the same parts.
const ASK = 20000, HOTEL = 38000, REST = 11000;
const FIXES = [
  { key: "3star", label: "Switch to a 3-star hotel", cost: 7000 + REST, budget: ASK, note: "Same dates, a 3-star near Calangute at ₹3,500 a night." },
  { key: "night", label: "One night fewer", cost: 19000 + 7500, budget: ASK, note: "One night in the 5-star; trains, food and sights scale down too." },
  { key: "gokarna", label: "Try Gokarna instead", cost: 18500, budget: ASK, note: "Two nights in a 4-star by Om Beach, quieter and cheaper than North Goa." },
  { key: "raise", label: "Raise budget to ₹52,500", cost: HOTEL + REST, budget: 52500, note: "Keeps everything you asked for, with the 7% buffer." },
];

function Amount({ value, start }: { value: number; start: boolean }) {
  return <>{inr(Math.round(useCountUp(value, 900, start)))}</>;
}

export function BudgetAware() {
  const bar = useRef<HTMLDivElement>(null);
  const seen = useInView(bar);
  const [fix, setFix] = useState<string | null>(null);
  const f = FIXES.find((x) => x.key === fix);
  const withBuffer = f ? Math.round(f.cost * 1.07) : 0;
  const fits = f ? withBuffer <= f.budget : false;

  return (
    <section className="py-24" aria-labelledby="budget-heading">
      <div className="container-x grid gap-12 lg:grid-cols-2 lg:items-center">
        <Reveal className="min-w-0">
          <span className="eyebrow">Budget, sorted</span>
          <h2 id="budget-heading" className="mt-5 text-title font-bold">Your budget is <span className="accent highlight">a promise</span>, not a guess</h2>
          <p className="mt-4 text-lg leading-relaxed text-ink-soft">Train fares, tickets, every auto ride and a small safety cushion, all added up before you see the plan. <span className="font-semibold text-ink">If your limit is ₹5,000, ₹5,001 never happens.</span></p>
          <div ref={bar} className="spotlight mt-8 rounded-2xl border border-line bg-paper/70 p-5 backdrop-blur">
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-ink-mute">Udaipur weekend for two</span>
              <span className="num font-semibold">{inr(CAT_TOTAL)}</span>
            </div>
            <div className="mt-3 flex h-4 gap-[3px] overflow-hidden rounded-full" role="img" aria-label="Budget split for the sample trip">
              {CATS.map((c, i) => (
                <span key={c.label} className={`${c.color} rounded-sm transition-[width] duration-1000 ease-out`}
                      style={{ width: seen ? `${(c.amount / CAT_TOTAL) * 100}%` : "0%", transitionDelay: `${i * 120}ms` }} />
              ))}
              <span className="flex-1 rounded-sm bg-haze-2" />
            </div>
            <ul className="num mt-5 grid grid-cols-1 gap-x-8 gap-y-2.5 text-[16px] sm:grid-cols-2">
              {CATS.map((c) => (
                <li key={c.label} className="flex items-center gap-2"><span className={`h-2.5 w-2.5 rounded-full ${c.color}`} />{c.label}<span className="ml-auto font-semibold"><Amount value={c.amount} start={seen} /></span></li>
              ))}
              <li className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-haze-2 ring-1 ring-line" />Remaining<span className="ml-auto font-semibold text-teal-deep"><Amount value={CAT_LEFT} start={seen} /></span></li>
            </ul>
          </div>
        </Reveal>

        <Reveal delay={120} className="min-w-0">
          <div className="spotlight overflow-hidden rounded-2xl border border-white/10 bg-[#0f1219] p-7 text-white shadow-[0_30px_80px_-40px_rgb(0_0_0/0.8)] md:p-9">
            <div aria-hidden className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-[rgb(244_110_142/0.18)] blur-3xl" />
            <div className="relative">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-[rgb(244_110_142/0.15)] px-3 py-1 text-xs font-semibold text-[rgb(255_150_175)]">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />When the maths doesn&apos;t work
              </span>
              <p className="mt-4 text-2xl font-bold">2 nights in Goa won&apos;t fit in {inr(ASK)}.</p>
              <p className="mt-3 leading-relaxed text-white/70">Your 5-star hotel is the main reason: 2 nights on Candolim beach cost {inr(HOTEL)} on their own.</p>
              <p className="mt-6 text-sm font-medium text-white/60">Pick a fix and watch it re-plan:</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {FIXES.map((x) => (
                  <button key={x.key} type="button" onClick={() => setFix(x.key === fix ? null : x.key)} aria-pressed={fix === x.key}
                          className={`rounded-full border px-3.5 py-1.5 text-sm transition-all duration-200 hover:-translate-y-px ${
                            fix === x.key ? "border-[rgb(190_255_74)] bg-[rgb(190_255_74)] font-semibold text-[#0b0d0c]" : "border-white/20 text-white/85 hover:border-white/50 hover:bg-white/5"}`}>
                    {x.label}
                  </button>
                ))}
              </div>
              <div className="mt-6 min-h-[92px]" aria-live="polite">
                {f ? (
                  <div key={f.key} className="fade-up rounded-xl border border-white/10 bg-white/[0.04] p-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className={`inline-flex items-center gap-1.5 text-sm font-semibold ${fits ? "text-[rgb(140_230_240)]" : "text-[rgb(240_170_100)]"}`}>
                        {fits ? <Check className="h-4 w-4" aria-hidden /> : <AlertTriangle className="h-4 w-4" aria-hidden />}
                        {fits ? `Fits, ${inr(f.budget - withBuffer)} to spare` : `Still ${inr(withBuffer - f.budget)} over`}
                      </span>
                      <span className="num text-sm text-white/70">{inr(withBuffer)} / {inr(f.budget)}</span>
                    </div>
                    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10">
                      <div className={`h-full rounded-full transition-[width] duration-700 ${fits ? "bg-[rgb(140_230_240)]" : "bg-[rgb(240_170_100)]"}`}
                           style={{ width: `${Math.min(100, (withBuffer / f.budget) * 100)}%` }} />
                    </div>
                    <p className="mt-3 text-sm text-white/65">{f.note}{!fits && " Combine it with another fix."}</p>
                  </div>
                ) : (
                  <p className="text-sm text-white/45">One tap and it re-plans. It will never quietly drop a meal just to make the numbers work.</p>
                )}
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- Alternatives */

const hm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const LUNCH_END = 13 * 60 + 45, NEXT_AT = 16 * 60 + 30; // lunch until 13:45, Accademia booked for 16:30
const ALTS = [
  { title: "Uffizi Gallery", reason: "Open 08:15–18:30 · 0.4 km from lunch", walk: 6, visit: 140, delta: 0, current: true },
  { title: "Palazzo Pitti", reason: "Open 08:15–18:30 · 0.9 km from lunch", walk: 12, visit: 120, delta: -26 },
  { title: "Galileo Museum", reason: "Open 09:30–18:00 · 0.3 km from lunch", walk: 5, visit: 90, delta: -34 },
  { title: "Santa Croce Basilica", reason: "Open 09:30–17:30 · 0.8 km from lunch", walk: 11, visit: 75, delta: -42 },
  { title: "Bargello Museum", reason: "Closes 13:50 today", walk: 7, visit: 90, delta: -38, closed: true },
];

export function AlternativesSection() {
  const [pick, setPick] = useState(0);
  const a = ALTS[pick];
  const start = LUNCH_END + a.walk, end = start + a.visit, spare = NEXT_AT - end - 10;

  return (
    <section className="py-24" aria-labelledby="alts-heading">
      <div className="container-x grid items-center gap-12 lg:grid-cols-[1.1fr_0.9fr]">
        <Reveal className="order-2 min-w-0 lg:order-1">
          <div className="spotlight panel overflow-hidden rounded-2xl shadow-[0_30px_80px_-40px_rgb(var(--shadow)/0.55)]">
            <div className="flex items-center gap-3 border-b border-line bg-haze/60 px-5 py-4">
              <Repeat className="h-4 w-4 text-signal" aria-hidden />
              <p className="font-semibold">Florence · your 14:00 slot</p>
              <span className="ml-auto text-xs text-ink-mute">Tap to swap</span>
            </div>
            <ul className="divide-y divide-line" role="radiogroup" aria-label="Alternatives for the 14:00 stop">
              {ALTS.map((x, i) => {
                const on = i === pick;
                const checks = x.closed ? [["Closed then", false]] : [["Open then", true], ["Within budget", true], ...(x.walk <= 8 ? [["Nearby", true]] : [])];
                return (
                  <li key={x.title}>
                    <button type="button" role="radio" aria-checked={on} disabled={x.closed} onClick={() => setPick(i)}
                            className={`flex w-full items-start gap-3 px-5 py-4 text-left transition-colors duration-200 disabled:cursor-not-allowed ${
                              on ? "bg-signal-soft/60" : x.closed ? "opacity-60" : "hover:bg-haze/70"}`}>
                      <span className={`mt-1 grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 transition-colors ${on ? "border-signal" : "border-line"}`}>
                        <span className={`h-1.5 w-1.5 rounded-full bg-signal transition-transform duration-200 ${on ? "scale-100" : "scale-0"}`} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2 font-semibold">{x.title}
                          {x.current && <span className="rounded-full bg-haze-2 px-2 py-0.5 text-[12px] font-semibold text-ink-soft">In your plan</span>}
                        </span>
                        <span className="block text-sm text-ink-mute">{x.reason}</span>
                        <span className="mt-2 flex flex-wrap gap-1.5">
                          {checks.map(([c, ok]) => (
                            <span key={String(c)} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${ok ? "bg-teal-soft text-teal-deep" : "bg-danger-soft text-danger"}`}>
                              {ok ? <Check className="h-3 w-3" aria-hidden /> : <X className="h-3 w-3" aria-hidden />}{c}
                            </span>
                          ))}
                        </span>
                      </span>
                      <span className="num shrink-0 text-sm font-semibold">{x.delta ? (x.delta > 0 ? "+" : "") + eur(x.delta) : "Same cost"}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <div key={pick} className="fade-up border-t border-line bg-haze/50 px-5 py-4 text-sm" aria-live="polite">
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="num font-semibold">{hm(start)}–{hm(end)}</span><span className="font-semibold">{a.title}</span>
                <span className="text-ink-mute">then Accademia at 16:30</span>
              </p>
              <p className="mt-1 text-ink-soft">
                {spare >= 0 ? `${spare} min spare before the walk to the Accademia` : "Too tight for the Accademia booking"}
                {a.delta !== 0 && <> · <span className="font-semibold text-teal-deep">{eur(-a.delta)} back in your budget</span></>}
              </p>
            </div>
          </div>
        </Reveal>
        <Reveal delay={120} className="order-1 min-w-0 lg:order-2">
          <span className="eyebrow">Smart swaps</span>
          <h2 id="alts-heading" className="mt-5 text-title font-bold">Not feeling a stop? <span className="accent highlight">Swap it in one tap.</span></h2>
          <p className="mt-4 text-lg leading-relaxed text-ink-soft">Every swap is tested against your real day: open when you reach, back in time for the next stop, and within what&apos;s left of your budget.</p>
          <ul className="mt-6 space-y-3 text-ink-soft">
            <li className="flex gap-3"><Clock className="h-5 w-5 shrink-0 text-signal" aria-hidden />Opening hours checked for your visit time</li>
            <li className="flex gap-3"><Route className="h-5 w-5 shrink-0 text-signal" aria-hidden />Distance from the previous stop shown</li>
            <li className="flex gap-3"><Wallet className="h-5 w-5 shrink-0 text-signal" aria-hidden />What it does to your budget</li>
          </ul>
        </Reveal>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- Group split */

const PEOPLE = [
  { name: "Aarav", left: 1800, tone: "bg-signal text-on-signal" },
  { name: "Priya", left: 950, tone: "bg-spark text-white" },
  { name: "Kabir", left: 700, tone: "bg-ink text-paper" },
  { name: "Meera", left: 450, tone: "bg-ink-mute text-white" },
];
const BILL = 2400, PAYER = "Aarav";

export function GroupSection() {
  const [inSplit, setInSplit] = useState<string[]>(PEOPLE.map((p) => p.name));
  const share = Math.round((BILL / inSplit.length) * 100) / 100;
  const owers = inSplit.filter((n) => n !== PAYER);
  const toggle = (n: string) => setInSplit((s) => s.includes(n) ? (s.length > 1 ? s.filter((x) => x !== n) : s) : PEOPLE.map((p) => p.name).filter((x) => x === n || s.includes(x)));
  const list = (xs: string[]) => xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

  return (
    <section className="py-24" aria-labelledby="group-heading">
      <div className="container-x grid gap-12 lg:grid-cols-2 lg:items-center">
        <Reveal className="min-w-0">
          <span className="eyebrow">Group trips</span>
          <h2 id="group-heading" className="mt-5 text-title font-bold">Your group chat could <span className="accent highlight">never plan this.</span></h2>
          <p className="mt-4 text-lg leading-relaxed text-ink-soft">We turn &ldquo;bro, let&apos;s go somewhere&rdquo; into <span className="font-semibold text-ink">actual tickets, stays, food stops, timings, and a budget everyone can agree on</span>. Add everyone with their own budget and food choices. One vegetarian in the gang? Every meal stop has veg options. Log spends as you go and {brand.name} settles up, so no more &ldquo;who paid for what&rdquo;.</p>
          <p className="mt-3 text-lg font-semibold text-ink">From bakchodi to boarding pass.</p>
        </Reveal>
        <Reveal delay={120} className="min-w-0">
          <div className="spotlight rounded-2xl border border-line bg-paper/50 p-6 shadow-[0_30px_80px_-40px_rgb(var(--shadow)/0.55)] backdrop-blur md:p-8">
            <div className="flex items-baseline justify-between gap-3">
              <p className="font-semibold">Dinner at Pinch of Spice, Agra, paid by {PAYER}</p>
              <p className="num text-xl font-bold">{inr(BILL)}</p>
            </div>
            <p className="mt-1 text-sm text-ink-mute">Split equally between {inSplit.length} · tap someone to leave them out</p>
            <ul className="num mt-5 space-y-2">
              {PEOPLE.map((p) => {
                const on = inSplit.includes(p.name);
                const short = on && p.left < share;
                return (
                  <li key={p.name}>
                    <button type="button" onClick={() => toggle(p.name)} aria-pressed={on}
                            className={`flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-all duration-200 hover:-translate-y-px ${
                              on ? "border-line bg-haze" : "border-dashed border-line bg-transparent opacity-50"}`}>
                      <span className="flex min-w-0 items-center gap-3">
                        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-bold ${p.tone}`}>{p.name[0]}</span>
                        <span className="min-w-0">
                          <span className="block font-medium">{p.name}{p.name === PAYER && <span className="ml-2 text-xs text-ink-mute">paid</span>}</span>
                          <span className={`block text-xs ${short ? "font-semibold text-caution" : "text-ink-mute"}`}>
                            {short ? `Only ${inr(p.left)} left in their budget` : `${inr(p.left)} left in budget`}
                          </span>
                        </span>
                      </span>
                      <span className={`font-semibold ${on ? "" : "line-through"}`}>{on ? inr(share) : "—"}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <p key={inSplit.join()} className="fade-up mt-5 text-[16px]">
              {owers.length
                ? <>{list(owers)} {owers.length > 1 ? "each pay" : "pays"} {PAYER} <span className="num font-semibold">{inr(share)}</span>.</>
                : <>{PAYER} covers it alone; nobody owes anything.</>}
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- Final call to action */

export function FinalCTA() {
  return (
    <section className="py-24">
      <div className="container-x">
        <Reveal>
          <div className="relative isolate overflow-hidden rounded-3xl border border-white/10 bg-[#0c0f16]/70 px-6 backdrop-blur py-14 text-white md:px-14 md:py-20">
            <div aria-hidden className="absolute -left-20 -top-32 -z-10 h-80 w-80 rounded-full bg-[rgb(190_255_74/0.22)] blur-3xl" />
            <div aria-hidden className="absolute -bottom-32 right-0 -z-10 h-80 w-96 rounded-full bg-[rgb(45_212_230/0.18)] blur-3xl" />
            <div>
              <div>
                <h2 className="max-w-2xl text-title font-bold">Pack the bag. <span className="accent highlight-night">We&apos;ll do the maths.</span></h2>
                <p className="mt-4 max-w-xl text-lg text-white/70">Four quick steps. Under a minute. A plan where every time and every rupee checks out.</p>
                <div className="mt-9 flex flex-wrap items-center gap-3">
                  <Link href="/plan" className="shine group relative inline-flex items-center gap-2 overflow-hidden rounded-lg bg-[rgb(190_255_74)] px-7 py-3.5 text-base font-semibold text-[#0b0d0c] transition-all duration-200 hover:-translate-y-px hover:shadow-[0_14px_40px_-12px_rgb(190_255_74/0.7)]">
                    Build my trip <ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1" aria-hidden />
                  </Link>
                  <Link href="/#pricing" className="btn-ghost-dark px-7 py-3.5 text-base">See pricing</Link>
                </div>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
