"use client";

import { useEffect, useState } from "react";

/** The hero's proof: a planned day, filling in row by row the way the engine actually produces it.
 *
 *  Three trips, India and abroad, in three currencies, because the planner reads the destination's own currency and timezone.
 *  Each row carries the same provenance tag the product uses everywhere — Live, Official, Estimated — because
 *  showing the source is the product. Totals and "left" are summed from the rows, never typed in. */
type Row = { t: string; name: string; meta: string; tag: "Live" | "Official" | "Estimated"; amt: number };
type Trip = { key: string; route: string; date: string; currency: string; locale: string; budget: number; compared: number; rows: Row[] };

const TRIPS: Trip[] = [
  { key: "agr", route: "Delhi → Agra", date: "Sat 11 Oct", currency: "INR", locale: "en-IN", budget: 3000, compared: 18, rows: [
    { t: "07:30", name: "Metro to Hazrat Nizamuddin", meta: "Pink line · 20 min", tag: "Live", amt: 40 },
    { t: "08:10", name: "Gatimaan Express 12049", meta: "Nizamuddin → Agra Cantt · 1 h 40 m", tag: "Live", amt: 770 },
    { t: "10:30", name: "Taj Mahal", meta: "Sunrise to sunset · closed Fridays", tag: "Official", amt: 250 },
    { t: "13:30", name: "Pinch of Spice", meta: "North Indian lunch · vegetarian options", tag: "Live", amt: 650 },
    { t: "15:30", name: "Agra Fort", meta: "Sunrise to sunset · 2 h visit", tag: "Official", amt: 50 },
    { t: "17:40", name: "Mehtab Bagh", meta: "Sunset across the river from the Taj", tag: "Estimated", amt: 30 },
  ] },
  { key: "auh", route: "Dubai → Abu Dhabi", date: "Sun 12 Oct", currency: "AED", locale: "en-AE", budget: 250, compared: 9, rows: [
    { t: "07:30", name: "Metro to Al Ghubaiba", meta: "Green line · 15 min", tag: "Live", amt: 5 },
    { t: "08:00", name: "E101 intercity bus", meta: "Al Ghubaiba → Abu Dhabi · 2 h", tag: "Live", amt: 25 },
    { t: "10:30", name: "Sheikh Zayed Grand Mosque", meta: "Open 09:00–22:00 · modest dress", tag: "Official", amt: 0 },
    { t: "13:30", name: "Lebanese Flower", meta: "Mezze lunch · vegetarian options", tag: "Live", amt: 45 },
    { t: "15:30", name: "Louvre Abu Dhabi", meta: "Open 10:00–18:30", tag: "Official", amt: 63 },
  ] },
  { key: "edi", route: "London → Edinburgh", date: "Sat 11 Oct", currency: "GBP", locale: "en-GB", budget: 130, compared: 11, rows: [
    { t: "07:40", name: "Tube to King's Cross", meta: "Victoria line · 12 min", tag: "Live", amt: 2.8 },
    { t: "08:00", name: "LNER 1S09", meta: "King's Cross → Edinburgh · 4 h 20 m", tag: "Live", amt: 48 },
    { t: "13:10", name: "Edinburgh Castle", meta: "Open 09:30–18:00 · last entry 17:00", tag: "Official", amt: 19.5 },
    { t: "15:30", name: "The Scran & Scallie", meta: "Late lunch · vegetarian options", tag: "Live", amt: 26 },
    { t: "18:05", name: "Arthur's Seat", meta: "Sunset 18:21 · 45 min climb", tag: "Estimated", amt: 0 },
  ] },
];

const TONES: Record<Row["tag"], string> = {
  Live: "border-teal/40 bg-teal-soft/40 text-teal",
  Official: "border-teal/40 text-teal",
  Estimated: "border-line text-ink-mute",
};
const STEP_MS = 150, HOLD_MS = 4600; // rows land quickly so the board never sits half-empty

function fmt(trip: Trip, v: number, free = false) {
  if (free && v === 0) return "Free";
  return new Intl.NumberFormat(trip.locale, { style: "currency", currency: trip.currency,
    maximumFractionDigits: Math.round(v * 100) % 100 ? 2 : 0, minimumFractionDigits: Math.round(v * 100) % 100 ? 2 : 0 }).format(v);
}

const ROWS = 4;

export function LiveBoard() {
  const [idx, setIdx] = useState(0);
  // The first trip starts complete: an empty card saying "Counting…" looked broken to anyone who arrived mid-load
  // or scrolled straight past. The next trips fill in row by row.
  const [shown, setShown] = useState(Math.min(ROWS, TRIPS[0].rows.length));
  const [paused, setPaused] = useState(false);
  const [reduced, setReduced] = useState(false);
  const trip = TRIPS[idx];
  const rows = trip.rows.slice(0, ROWS);  // a few roomy rows read better than a whole day squeezed in

  useEffect(() => { setReduced(window.matchMedia("(prefers-reduced-motion: reduce)").matches); }, []);

  // Rows land one by one; once the day is complete it holds, then the next trip takes over.
  useEffect(() => {
    if (reduced) return setShown(rows.length);
    if (shown < rows.length) {
      const t = setTimeout(() => setShown((n) => n + 1), shown === 0 ? 120 : STEP_MS);
      return () => clearTimeout(t);
    }
    if (paused) return;
    const t = setTimeout(() => { setShown(0); setIdx((i) => (i + 1) % TRIPS.length); }, HOLD_MS);
    return () => clearTimeout(t);
  }, [shown, trip, paused, reduced, rows.length]);

  const total = rows.reduce((a, r) => a + r.amt, 0);
  const spent = rows.slice(0, shown).reduce((a, r) => a + r.amt, 0);
  const pct = Math.min(100, (spent / trip.budget) * 100);

  return (
    <div className="board-glow relative" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <div className="spotlight relative overflow-hidden rounded-2xl border border-line bg-paper/55 shadow-[0_30px_80px_-40px_rgb(var(--shadow)/0.6)] backdrop-blur-xl">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-line px-5 py-4 sm:px-6">
          <span className="text-[12px] font-semibold uppercase tracking-[0.16em] text-ink-mute">
            {trip.route} · {trip.date}
          </span>
          <span className="num text-[13px] font-semibold text-ink">{fmt(trip, spent)} <span className="text-ink-mute">/ {fmt(trip, trip.budget)}</span></span>
        </div>
        <div className="h-[3px] bg-haze-2" aria-hidden>
          <div className="h-full bg-gradient-to-r from-signal to-spark transition-[width] duration-500 ease-out" style={{ width: `${pct}%` }} />
        </div>

        <ol aria-live="polite">
          {rows.map((r, i) => (
            <li key={`${trip.key}-${r.t}`}
                className={`group grid grid-cols-[46px_1fr_auto] items-baseline gap-x-3 border-b border-line/70 px-5 py-4 transition-all duration-500 last:border-b-0 hover:bg-signal-soft/50 sm:grid-cols-[58px_1fr_auto] sm:gap-x-4 sm:px-6 ${
                  i < shown ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"}`}>
              <span className="num text-[14px] font-bold text-signal">{r.t}</span>
              <span className="min-w-0">
                <span className="block truncate text-[14px] font-semibold leading-snug text-ink transition-transform duration-300 group-hover:translate-x-0.5">{r.name}</span>
                <span className="mt-0.5 block truncate text-[13px] leading-snug text-ink-mute">{r.meta}</span>
              </span>
              <span className="flex items-center gap-3">
                <span className={`hidden rounded-[4px] border px-1.5 py-0.5 text-[12px] font-semibold uppercase tracking-[0.08em] sm:inline ${TONES[r.tag]}`}>
                  {r.tag}
                </span>
                <span className="num min-w-[52px] text-right text-[14px] font-bold text-ink">{fmt(trip, r.amt, true)}</span>
              </span>
            </li>
          ))}
        </ol>

        <div className="flex items-center justify-between bg-haze/70 px-5 py-3.5 text-[13px] text-ink-soft sm:px-6">
          <span className="flex items-center gap-2">
            <span className="board-live h-1.5 w-1.5 rounded-full bg-teal" />{trip.compared} departures compared
          </span>
          <span className="num font-semibold text-ink">
            {shown === rows.length ? `${fmt(trip, trip.budget - total)} left` : "Counting…"}
          </span>
        </div>
      </div>
    </div>
  );
}
