"use client";

import { useState } from "react";
import { ChevronDown, Coffee, Sparkles } from "lucide-react";
import type { Itinerary, ItineraryItem } from "@/lib/types";
import { MODE_LABEL, minutes, money, niceDate } from "@/lib/format";
import { KIND_TONE, TimelineItem, itemIcon } from "./TimelineItem";

/** What a day costs and how many real stops it has — shared by the day headers and the overview strip. */
export function dayStats(items: ItineraryItem[]) {
  const stops = items.filter((i) => ["activity", "meal", "sunset"].includes(i.kind)).length;
  const cost = items.reduce((a, i) => a + (i.cost ?? 0) + (i.travel_cost ?? 0), 0);
  const timed = items.filter((i) => i.time); // a train with no timetable has no clock time (I1)
  const first = timed[0]?.time, last = timed[timed.length - 1]?.end_time || timed[timed.length - 1]?.time;
  return { stops, cost, first, last };
}

export type PlanView = "compact" | "detailed";

/** One line per stop: time, what, a line of the useful detail, the cost. Tap it for everything the full card shows (hours, why this,
 *  swap, sources). A long day reads in one screen instead of five. */
function CompactRow({ item, onChange, highlight, currency, near, tripId }: {
  item: ItineraryItem; onChange?: (i: ItineraryItem) => void; highlight?: boolean; currency?: string; near?: string; tripId?: string;
}) {
  const [open, setOpen] = useState(false);
  if (item.kind === "free") {
    return (
      <li className="flex items-center gap-2 px-3.5 py-2 text-[13px] text-ink-mute">
        <Coffee className="h-3.5 w-3.5 shrink-0" aria-hidden /><span className="num w-[48px] shrink-0">{item.time}</span>{item.title}, {minutes(item.duration_minutes)}
      </li>
    );
  }
  const Icon = itemIcon(item);
  const when = item.time ?? item.meta?.when ?? "—";
  const travel = item.travel_minutes_from_prev
    ? `${minutes(item.travel_minutes_from_prev)} ${(MODE_LABEL[item.travel_mode ?? ""] ?? "travel").toLowerCase()} from the last stop`
    : null;
  const detail = item.kind === "transport" && item.meta?.from_name && item.meta?.to_name
    ? `${item.meta.from_name} → ${item.meta.to_name}`
    : item.opening_time ? `Open ${item.opening_time}–${item.closing_time}` : item.subtitle;
  const free = item.cost === 0 && (item.kind === "activity" || item.kind === "sunset");
  // "06:40 train to Jaipur" sits beside "06:40": the time is already there
  const title = item.time && item.title.startsWith(`${item.time} `)
    ? item.title.slice(item.time.length + 1).replace(/^./, (c) => c.toUpperCase()) : item.title;
  return (
    <li className={highlight ? "bg-signal-soft/50" : ""}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="grid w-full grid-cols-[42px_minmax(0,1fr)_auto] items-center gap-x-2.5 px-3 py-2.5 text-left transition-colors hover:bg-haze/70 sm:grid-cols-[56px_34px_minmax(0,1fr)_auto] sm:gap-x-3 sm:px-3.5">
        <span className="num text-[14px] font-bold text-ink">{when}</span>
        <span className={`hidden h-8 w-8 place-items-center rounded-lg sm:grid ${KIND_TONE[item.kind] ?? "bg-haze text-ink"}`}><Icon className="h-4 w-4" aria-hidden /></span>
        <span className="min-w-0">
          <span className="flex min-w-0 items-baseline gap-1.5">
            <span className="line-clamp-2 text-[15px] font-semibold leading-snug sm:truncate">{title}</span>
            {item.meta?.source === "google" && typeof item.meta?.rating === "number" && (
              <span className="num shrink-0 text-[12px] font-semibold text-ink-mute" title="Rating on Google Maps">★ {item.meta.rating}</span>
            )}
          </span>
          {(detail || travel) && (
            <span className="block truncate text-[13px] text-ink-mute">{[detail, travel].filter(Boolean).join(" · ")}</span>
          )}
          {item.meta?.why_go && (
            <span className="mt-0.5 flex min-w-0 items-center gap-1 text-[12.5px] font-medium text-signal-deep">
              <Sparkles className="h-3 w-3 shrink-0" aria-hidden /><span className="sm:truncate">{item.meta.why_go}</span>
            </span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="num text-[14px] font-bold">{item.cost ? money(item.cost, currency) : free ? <span className="text-[13px] font-semibold text-teal-deep">Free</span> : ""}</span>
          <ChevronDown className={`h-4 w-4 text-ink-mute transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
        </span>
      </button>
      {open && (
        <ol className="fade-up border-t border-line bg-haze/40 px-2 pb-3 pt-3 sm:px-4">
          <TimelineItem item={item} onChange={onChange} tripId={tripId} highlight={highlight} currency={currency} near={near} first last />
        </ol>
      )}
    </li>
  );
}

export function Timeline({ itinerary, onChange, highlightIds = [], currency, near, tripId, view = "detailed", day = "all" }: {
  itinerary: Itinerary; onChange?: (i: ItineraryItem) => void; highlightIds?: string[]; currency?: string; near?: string; tripId?: string;
  view?: PlanView; day?: number | "all";
}) {
  const days = day === "all" ? itinerary.days : itinerary.days.filter((d) => d.index === day);
  return (
    <div className={view === "compact" ? "space-y-8" : "space-y-12"}>
      {days.map((d) => {
        const st = dayStats(d.items);
        const n = itinerary.days.findIndex((x) => x.index === d.index);
        return (
          <section key={d.index} id={`day-${d.index}`} className="scroll-mt-40" aria-label={`${d.label}, ${niceDate(d.date)}`}>
            <header className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl border border-line bg-paper/70 px-4 py-3 backdrop-blur sm:px-5">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-signal text-lg font-extrabold text-on-signal">{n + 1}</span>
              <div className="min-w-[8.5rem] flex-1">
                <h2 className="text-lg font-bold leading-tight">{d.label}</h2>
                <p className="text-sm text-ink-mute">{niceDate(d.date, { weekday: "long", day: "numeric", month: "long" })}</p>
              </div>
              <dl className="num flex w-full justify-between gap-5 border-t border-line pt-2.5 text-sm sm:w-auto sm:justify-start sm:border-0 sm:pt-0">
                <div><dt className="text-xs text-ink-mute">Stops</dt><dd className="font-bold">{st.stops}</dd></div>
                {st.first && <div><dt className="text-xs text-ink-mute">Hours</dt><dd className="font-bold">{st.first}–{st.last}</dd></div>}
                <div><dt className="text-xs text-ink-mute">Day cost</dt><dd className="font-bold">{money(st.cost, currency)}</dd></div>
              </dl>
            </header>
            {view === "compact" ? (
              <ol className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-paper">
                {d.items.map((item) => (
                  <CompactRow key={item.id} item={item} onChange={onChange} tripId={tripId} highlight={highlightIds.includes(item.id)} currency={currency} near={near} />
                ))}
              </ol>
            ) : (
              <ol>
                {d.items.map((item, i) => (
                  <TimelineItem key={item.id} item={item} onChange={onChange} tripId={tripId} highlight={highlightIds.includes(item.id)}
                                currency={currency} near={near} first={i === 0} last={i === d.items.length - 1} />
                ))}
              </ol>
            )}
          </section>
        );
      })}
    </div>
  );
}
