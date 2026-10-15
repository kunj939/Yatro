"use client";

import { useState } from "react";
import { Bus, ChevronDown, Coffee, MessageCircle, Navigation, Sparkles } from "lucide-react";
import type { Itinerary, ItineraryItem } from "@/lib/types";
import { MODE_LABEL, minutes, money, niceDate } from "@/lib/format";
import { dayRouteUrl } from "@/lib/tripLinks";
import { KIND_TONE, TimelineItem, itemIcon } from "./TimelineItem";
import { askAboutStop } from "./TripChat";
import { transitSummary } from "@/lib/transit";

/** What a day costs and how many real stops it has — shared by the day headers and the overview strip. */
export function dayStats(items: ItineraryItem[]) {
  const stops = items.filter((i) => ["activity", "meal", "sunset"].includes(i.kind)).length;
  const cost = items.reduce((a, i) => a + (i.cost ?? 0) + (i.travel_cost ?? 0), 0);
  const timed = items.filter((i) => i.time); // a train with no timetable has no clock time (I1)
  const first = timed[0]?.time, last = timed[timed.length - 1]?.end_time || timed[timed.length - 1]?.time;
  return { stops, cost, first, last };
}

export type PlanView = "compact" | "detailed";
/** Which days to show: all, one day (its index), or one week ("w0" is days 1-7). */
export type DayFilter = number | "all" | `w${number}`;
/** A trip longer than a week reads week by week. */
export const isWeekly = (it: Itinerary) => it.days.length > 7;
export const weekOf = (n: number) => Math.floor(n / 7);
/** The days of week `w` (0-based) and what they add up to, for the week headers and the overview cards. */
export function weekStats(it: Itinerary, w: number) {
  const days = it.days.slice(w * 7, w * 7 + 7);
  const totals = days.map((d) => dayStats(d.items));
  return { days, first: days[0]?.date, last: days[days.length - 1]?.date,
           stops: totals.reduce((a, s) => a + s.stops, 0), cost: totals.reduce((a, s) => a + s.cost, 0) };
}

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
  const ride = transitSummary(item.meta?.transit);  // "Metro Yellow Line from Rajiv Chowk" (ADR 0019)
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
      <div className="flex items-stretch">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="grid min-w-0 flex-1 grid-cols-[42px_minmax(0,1fr)_auto] items-center gap-x-2.5 py-2.5 pl-3 pr-1 text-left transition-colors hover:bg-haze/70 sm:grid-cols-[56px_34px_minmax(0,1fr)_auto] sm:gap-x-3 sm:pl-3.5">
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
          {ride && (
            <span className="mt-0.5 flex min-w-0 items-start gap-1 text-[12.5px] font-medium text-ink-soft">
              <Bus className="mt-0.5 h-3 w-3 shrink-0 text-signal-deep" aria-hidden /><span className="line-clamp-2">{ride}</span>
            </span>
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
      {tripId && ["activity", "sunset", "meal", "stay"].includes(item.kind) ? (
        <button type="button" onClick={() => askAboutStop(item)} title={`Ask about ${title}`}
                className="my-2 mr-2 grid w-9 shrink-0 place-items-center rounded-xl text-signal-deep transition-colors hover:bg-signal-soft sm:mr-2.5">
          <MessageCircle className="h-[18px] w-[18px]" aria-hidden /><span className="sr-only">Ask about {title}</span>
        </button>
      ) : <span className="w-2 shrink-0 sm:w-2.5" aria-hidden />}
      </div>
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
  view?: PlanView; day?: DayFilter;
}) {
  const weekly = isWeekly(itinerary);
  const days = day === "all" ? itinerary.days
    : typeof day === "string" ? itinerary.days.filter((_, n) => weekOf(n) === Number(day.slice(1)))
    : itinerary.days.filter((d) => d.index === day);
  return (
    <div className={view === "compact" ? "space-y-8" : "space-y-12"}>
      {days.map((d, shownAt) => {
        const st = dayStats(d.items);
        const n = itinerary.days.findIndex((x) => x.index === d.index);
        const weekHead = weekly && (n % 7 === 0 || shownAt === 0) ? weekStats(itinerary, weekOf(n)) : null;
        return (
          <div key={d.index}>
          {weekHead && (
            <div className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b-2 border-signal pb-2">
              <h2 className="text-[22px] font-extrabold tracking-tight">Week {weekOf(n) + 1}</h2>
              <p className="num text-sm text-ink-mute">
                {niceDate(weekHead.first!)} – {niceDate(weekHead.last!)} · {weekHead.days.length} days · {weekHead.stops} stops · {money(weekHead.cost, currency)}
              </p>
            </div>
          )}
          <section id={`day-${d.index}`} className="scroll-mt-40" aria-label={`${d.label}, ${niceDate(d.date)}`}>
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
              {dayRouteUrl(d.items) && (
                <a href={dayRouteUrl(d.items)!} target="_blank" rel="noopener noreferrer"
                   className="inline-flex w-full items-center justify-center gap-1.5 rounded-full bg-signal px-3.5 py-2 text-[13px] font-semibold text-on-signal transition-all hover:-translate-y-px sm:w-auto">
                  <Navigation className="h-3.5 w-3.5" aria-hidden />Day route in Maps
                </a>
              )}
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
          </div>
        );
      })}
    </div>
  );
}
