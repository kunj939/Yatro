"use client";

import { useRef } from "react";
import { ArrowRight, CalendarDays, Clock } from "lucide-react";
import { niceDate, time12 } from "@/lib/format";

export type When = { start_date: string; end_date: string; start_time: string; end_time: string };

function isoDate(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function addDays(iso: string, n: number) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + n));
}
function daysBetween(a: string, b: string) {
  const [y1, m1, d1] = a.split("-").map(Number), [y2, m2, d2] = b.split("-").map(Number);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000);
}
/** Saturday on or after `from` (today counts if it is a Saturday). */
function saturdayFrom(from: Date) {
  const d = new Date(from);
  d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7));
  return d;
}

// Every half hour, written the way people say it ("7:30 am").
const TIMES = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`);
const LENGTHS = [[0, "Day trip"], [1, "2 days"], [2, "3 days"], [4, "5 days"], [6, "1 week"], [13, "2 weeks"]] as const;

/** Dates and times for the trip, as big tappable tiles instead of bare browser inputs, with one-tap picks for the
 *  usual answers (this weekend, 3 days). The browser's own date picker still opens underneath, so it works the same
 *  on phones. Up to 30 nights. */
export function WhenPicker({ value, today, onChange }: { value: When; today: string; onChange: (patch: Partial<When>) => void }) {
  const length = Math.max(0, daysBetween(value.start_date, value.end_date));
  const now = new Date();
  const thisSat = saturdayFrom(now), nextSat = new Date(thisSat);
  nextSat.setDate(nextSat.getDate() + 7);
  const starts = [
    ["Today", today], ["Tomorrow", addDays(today, 1)], ["This weekend", isoDate(thisSat)], ["Next weekend", isoDate(nextSat)],
  ] as const;

  const setStart = (d: string) => onChange({ start_date: d, end_date: addDays(d, length) });

  return (
    <div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Quick start date">
        {starts.map(([label, d]) => (
          <button key={label} type="button" onClick={() => setStart(d)} aria-pressed={value.start_date === d}
                  className={value.start_date === d ? "chip-on !rounded-full !py-1.5" : "chip-off !rounded-full !py-1.5"}>{label}</button>
        ))}
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-[1fr_auto_1fr] md:items-stretch">
        <Side title="Leaving">
          <DateTile id="start-date" value={value.start_date} min={today}
                    onPick={(d) => onChange({ start_date: d, end_date: daysBetween(d, value.end_date) < 0 ? d : value.end_date })} />
          <TimeTile id="start-time" label="Start at" value={value.start_time} onPick={(t) => onChange({ start_time: t })} />
        </Side>
        <div className="flex items-center justify-center md:flex-col" aria-hidden>
          <span className="h-px w-10 bg-line md:h-10 md:w-px" />
          <span className="mx-2 my-0 grid h-9 w-9 place-items-center rounded-full bg-signal text-on-signal md:mx-0 md:my-2"><ArrowRight className="h-4 w-4 rotate-90 md:rotate-0" /></span>
          <span className="h-px w-10 bg-line md:h-10 md:w-px" />
        </div>
        <Side title="Coming back">
          <DateTile id="end-date" value={value.end_date} min={value.start_date} onPick={(d) => onChange({ end_date: d })} />
          <TimeTile id="end-time" label="Back by" value={value.end_time} onPick={(t) => onChange({ end_time: t })} />
        </Side>
      </div>

      <p className="mb-2 mt-5 text-xs font-semibold uppercase tracking-[0.12em] text-ink-mute">How long?</p>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Trip length">
        {LENGTHS.map(([n, label]) => (
          <button key={label} type="button" onClick={() => onChange({ end_date: addDays(value.start_date, n) })} aria-pressed={length === n}
                  className={length === n ? "chip-on !rounded-full !py-1.5" : "chip-off !rounded-full !py-1.5"}>{label}</button>
        ))}
      </div>
    </div>
  );
}

function Side({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-haze/60 p-3">
      <p className="px-1 pb-2 text-xs font-bold uppercase tracking-[0.12em] text-ink-mute">{title}</p>
      <div className="grid grid-cols-[1.4fr_1fr] gap-2">{children}</div>
    </div>
  );
}

/** A date as a big tile; the real <input type="date"> sits on top, invisible, so a tap anywhere opens the picker. */
function DateTile({ id, value, min, onPick }: { id: string; value: string; min: string; onPick: (d: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [y] = value.split("-");
  return (
    <label htmlFor={id} className="group relative block cursor-pointer rounded-xl border border-line bg-paper px-3.5 py-2.5 transition-colors hover:border-signal/60 focus-within:border-signal focus-within:ring-2 focus-within:ring-signal/25">
      <span className="flex items-center gap-1.5 text-[12px] font-medium text-ink-mute"><CalendarDays className="h-3.5 w-3.5" aria-hidden />Date</span>
      <span className="mt-0.5 block text-[18px] font-bold leading-tight text-ink">{niceDate(value)}</span>
      <span className="block text-[12px] text-ink-mute">{y}</span>
      <input ref={ref} id={id} type="date" min={min} value={value} required
             onClick={() => { try { ref.current?.showPicker?.(); } catch { /* older browsers open it themselves */ } }}
             onChange={(e) => e.target.value && onPick(e.target.value)}
             className="absolute inset-0 h-full w-full cursor-pointer opacity-0" />
    </label>
  );
}

/** A time as a big tile over a plain <select> of half hours: easier than a spinner on every phone. */
function TimeTile({ id, label, value, onPick }: { id: string; label: string; value: string; onPick: (t: string) => void }) {
  const options = TIMES.includes(value) ? TIMES : [...TIMES, value].sort();
  return (
    <label htmlFor={id} className="relative block cursor-pointer rounded-xl border border-line bg-paper px-3.5 py-2.5 transition-colors hover:border-signal/60 focus-within:border-signal focus-within:ring-2 focus-within:ring-signal/25">
      <span className="flex items-center gap-1.5 text-[12px] font-medium text-ink-mute"><Clock className="h-3.5 w-3.5" aria-hidden />{label}</span>
      <span className="mt-0.5 block text-[18px] font-bold leading-tight text-ink">{time12(value)}</span>
      <span className="block text-[12px] text-ink-mute">Tap to change</span>
      <select id={id} value={value} onChange={(e) => onPick(e.target.value)} className="absolute inset-0 h-full w-full cursor-pointer opacity-0">
        {options.map((t) => <option key={t} value={t}>{time12(t)}</option>)}
      </select>
    </label>
  );
}
