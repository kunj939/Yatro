"use client";

import { useEffect, useState } from "react";
import { Bus, Check, Loader2, TrainFront, Building2, Landmark, Plane, Sunset, UtensilsCrossed, MapPin } from "lucide-react";
import { inr } from "@/lib/format";
import { brand } from "@/lib/brand";

type Mode = "Train" | "Bus" | "Flight";

interface Route {
  from: string; to: string; mode: Mode; who: string; people: number;
  day: [string, string, string]; currency: string; locale: string; budget: number; planned: number;
}

// Illustrative examples for the landing page only. Real plans come from the engine with live data.
const ROUTES: Route[] = [
  { from: "Delhi", to: "Agra", mode: "Train", who: "Couple, vegetarian", people: 2, day: ["Sat", "3", "Oct"],
    currency: "INR", locale: "en-IN", budget: 6000, planned: 5420 },
  { from: "London", to: "Edinburgh", mode: "Train", who: "Friends, 2 nights", people: 3, day: ["Fri", "9", "Oct"],
    currency: "GBP", locale: "en-GB", budget: 900, planned: 842 },
  { from: "Mumbai", to: "Goa", mode: "Flight", who: "Friends, 3 nights", people: 4, day: ["Thu", "15", "Oct"],
    currency: "INR", locale: "en-IN", budget: 60000, planned: 55800 },
  { from: "Tokyo", to: "Kyoto", mode: "Train", who: "Solo, vegetarian", people: 1, day: ["Sun", "11", "Oct"],
    currency: "JPY", locale: "en-US", budget: 60000, planned: 54800 },
  { from: "Jaipur", to: "Udaipur", mode: "Bus", who: "Family, 2 nights", people: 4, day: ["Sat", "17", "Oct"],
    currency: "INR", locale: "en-IN", budget: 30000, planned: 27650 },
  { from: "Dubai", to: "Abu Dhabi", mode: "Bus", who: "Couple, day trip", people: 2, day: ["Sat", "24", "Oct"],
    currency: "AED", locale: "en-AE", budget: 500, planned: 452 },
  { from: "Delhi", to: "Rishikesh", mode: "Train", who: "Friends, 2 nights", people: 3, day: ["Fri", "30", "Oct"],
    currency: "INR", locale: "en-IN", budget: 15000, planned: 13900 },
];

const MODE_ICON = { Train: TrainFront, Bus, Flight: Plane };
const LATER_STOPS = [
  { label: "Hotel", icon: Building2 }, { label: "Attraction", icon: Landmark }, { label: "Sunset", icon: Sunset },
  { label: "Dinner", icon: UtensilsCrossed },
];
const STATES = [
  "Understanding your trip...", "Finding routes...", "Checking opening hours...", "Comparing prices...",
  "Optimizing budget...", "Finding alternatives...", "Your trip is ready.",
];
const TICKS_PER_ROUTE = STATES.length + 3; // hold the finished card for a moment before the next example

function money(r: Route, value: number): string {
  if (r.currency === "INR") return inr(value);
  return new Intl.NumberFormat(r.locale, { style: "currency", currency: r.currency, maximumFractionDigits: 0 }).format(value);
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(m.matches);
    const on = () => setReduced(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** The hero's "planning console": a ticket card that plays the planning pipeline for trips around the world. */
export function PlanningAnimation() {
  const reduced = useReducedMotion();
  const [n, setN] = useState(0);
  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => setN((v) => v + 1), 1150);
    return () => clearInterval(t);
  }, [reduced]);

  const r = ROUTES[reduced ? 0 : Math.floor(n / TICKS_PER_ROUTE) % ROUTES.length];
  const tick = n % TICKS_PER_ROUTE;
  const step = reduced ? STATES.length - 1 : Math.min(tick, STATES.length - 1);
  const done = step === STATES.length - 1;
  const leaving = !reduced && tick === TICKS_PER_ROUTE - 1; // fade out before switching to the next route
  const stops = [
    { label: r.from, icon: MapPin }, { label: r.mode, icon: MODE_ICON[r.mode] }, { label: r.to, icon: MapPin },
    ...LATER_STOPS,
  ];
  const stopsShown = reduced ? stops.length : Math.min(stops.length, Math.round(((step + 1) / STATES.length) * stops.length));
  const planned = step >= 4 ? r.planned : Math.round(r.planned * (step / 5) * 0.8);
  const status = step === 4 ? `Optimizing ${money(r, r.budget)} budget...` : STATES[step];
  const ModeIcon = MODE_ICON[r.mode];

  return (
    <div className="relative w-full max-w-[440px] overflow-hidden rounded-ticket border border-white/15 bg-night/75 text-white shadow-2xl backdrop-blur-md"
         role="img" aria-label={`Animated example: ${brand.name} planning ${r.from} to ${r.to} for ${money(r, r.budget)}`}>
      <div className={`flex transition-opacity duration-500 ${leaving ? "opacity-0" : "opacity-100"}`}>
        <div className="flex w-[74px] shrink-0 flex-col items-center justify-between border-r border-dashed border-white/25 py-5 text-center">
          <span className="text-[12px] leading-tight text-white/60">{r.day[0]}<br /><span className="text-lg font-bold text-white">{r.day[1]}</span><br />{r.day[2]}</span>
          <ModeIcon className="h-5 w-5 text-signal" aria-hidden />
          <span className="text-[12px] leading-tight text-white/60">{r.people}<br />{r.people === 1 ? "person" : "people"}</span>
        </div>
        <div className="flex-1 p-5">
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-[16px] font-semibold">{r.from} to {r.to}</p>
            <p className="shrink-0 text-xs text-white/55">{r.who}</p>
          </div>
          <ol className="mt-4 space-y-0">
            {stops.map((s, i) => {
              const Icon = s.icon;
              const on = i < stopsShown;
              return (
                <li key={i} className={`relative flex items-center gap-3 py-[5px] transition-opacity duration-500 ${on ? "opacity-100" : "opacity-20"}`}>
                  {i < stops.length - 1 && (
                    <span className={`absolute left-[11px] top-[26px] h-[18px] w-[2px] ${i + 1 < stopsShown ? "bg-signal" : "bg-paper/20"}`} />
                  )}
                  <span className={`grid h-6 w-6 place-items-center rounded-full ${on ? "bg-signal text-night" : "bg-paper/10 text-white/60"}`}>
                    <Icon className="h-3.5 w-3.5" aria-hidden />
                  </span>
                  <span className="text-[14px]">{s.label}</span>
                </li>
              );
            })}
          </ol>
          <div className="mt-4 rounded-xl bg-paper/[0.06] p-3.5">
            <div className="flex items-center gap-2 text-[14px]" aria-live="polite">
              {done ? <Check className="h-4 w-4 text-teal" aria-hidden /> : <Loader2 className="h-4 w-4 animate-spin text-signal motion-reduce:animate-none" aria-hidden />}
              <span className={done ? "font-semibold text-white" : "text-white/85"}>{status}</span>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-paper/10">
              <div className="h-full rounded-full bg-signal transition-[width] duration-700" style={{ width: `${(planned / r.budget) * 100}%` }} />
            </div>
            <dl className="num mt-2 grid grid-cols-3 gap-2 text-[13px] leading-tight">
              <div><dt className="text-white/55">Budget</dt><dd className="font-semibold text-white/85">{money(r, r.budget)}</dd></div>
              <div><dt className="text-white/55">Planned</dt><dd className="font-semibold text-white/85">{money(r, planned)}</dd></div>
              <div className="text-right"><dt className="text-white/55">Remaining</dt>
                <dd className={`font-semibold ${done ? "text-teal" : "text-white/85"}`}>{money(r, r.budget - planned)}</dd></div>
            </dl>
          </div>
          {!reduced && (
            <div className="mt-3 flex justify-center gap-1.5" aria-hidden>
              {ROUTES.map((x, i) => (
                <span key={x.from} className={`h-1.5 rounded-full transition-all ${x === r ? "w-4 bg-signal" : "w-1.5 bg-paper/25"}`} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}