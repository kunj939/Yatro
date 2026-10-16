"use client";

import { CalendarCheck, Crown, Leaf, Utensils } from "lucide-react";
import type { GroupSummary } from "@/lib/types";
import { niceDate } from "@/lib/format";

/** Votes per place: a bar per option, the leader in olive with a crown. Values are written out, never colour alone. */
export function VoteBars({ g, mine = [] }: { g: GroupSummary; mine?: string[] }) {
  const max = Math.max(1, ...g.options.map((o) => o.votes));
  const lead = g.options.reduce((a, o) => (o.votes > a.votes ? o : a), g.options[0]);
  return (
    <ul className="space-y-2.5">
      {g.options.map((o) => {
        const top = o.votes > 0 && o.id === lead.id;
        return (
          <li key={o.id}>
            <div className="flex items-baseline justify-between gap-3 text-[14px]">
              <span className="flex min-w-0 items-center gap-1.5 font-semibold text-ink">
                {top && <Crown className="h-4 w-4 shrink-0 text-[#eda100]" aria-label="Leading" />}
                <span className="truncate">{o.name}</span>
                {mine.includes(o.id) && <span className="rounded-full bg-signal-soft px-2 py-px text-[11px] font-semibold text-signal-deep">Your vote</span>}
              </span>
              <span className="num shrink-0 text-ink-soft"><span className="font-bold text-ink">{o.votes}</span> {o.votes === 1 ? "vote" : "votes"}</span>
            </div>
            <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-haze-2" aria-hidden>
              <div className={`h-full rounded-full transition-[width] duration-700 ${top ? "bg-signal" : "bg-ink/25"}`} style={{ width: `${(o.votes / max) * 100}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Each day of the window, darker when more people can do it; the best stretches listed underneath. */
export function DateHeat({ g, pick, onPick }: { g: GroupSummary; pick?: string | null; onPick?: (start: string) => void }) {
  const max = Math.max(1, ...g.days_heat.map((d) => d.count));
  const best = g.best_dates[0];
  const inBest = (d: string) => !!best && d >= best.start && d <= best.end;
  return (
    <div>
      <div className="flex gap-[3px] overflow-x-auto pb-1 [scrollbar-width:thin]" role="img"
           aria-label={g.days_heat.map((d) => `${niceDate(d.date)}: ${d.count} free`).join(", ")}>
        {g.days_heat.map((d) => {
          const k = d.count / max;
          return (
            <span key={d.date} className="flex shrink-0 flex-col items-center gap-1" title={`${niceDate(d.date)}: ${d.count} can make it`}>
              <span className={`grid h-9 w-7 place-items-center rounded-md text-[11px] font-bold num ${inBest(d.date) ? "ring-2 ring-signal ring-offset-1 ring-offset-paper" : ""}`}
                    style={{ background: d.count ? `rgb(101 150 0 / ${0.15 + k * 0.75})` : "rgb(var(--haze-2))", color: k > 0.55 ? "#fff" : "rgb(var(--ink-soft))" }}>
                {d.count || ""}
              </span>
              <span className="num text-[10px] text-ink-mute">{new Date(`${d.date}T00:00:00`).getDate()}</span>
            </span>
          );
        })}
      </div>
      {g.best_dates.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {g.best_dates.map((b, n) => {
            const on = pick ? pick === b.start : n === 0;
            const label = `${niceDate(b.start, { weekday: "short", day: "numeric", month: "short" })}${b.end !== b.start ? ` – ${niceDate(b.end, { weekday: "short", day: "numeric" })}` : ""}`;
            const body = (
              <>
                <CalendarCheck className="h-3.5 w-3.5" aria-hidden />{label}
                <span className="num font-normal opacity-80">· {b.fits} of {b.of}</span>
              </>
            );
            return onPick ? (
              <button key={b.start} type="button" onClick={() => onPick(b.start)} aria-pressed={on}
                      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-semibold transition-all ${on ? "border-signal bg-signal-soft text-ink" : "border-line bg-paper text-ink-soft hover:border-ink/30"}`}>{body}</button>
            ) : (
              <span key={b.start} className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-semibold ${n === 0 ? "border-signal bg-signal-soft text-ink" : "border-line bg-paper text-ink-soft"}`}>{body}</span>
            );
          })}
        </div>
      ) : (
        <p className="mt-2 text-[13px] text-ink-mute">The best dates show up as people pick theirs.</p>
      )}
    </div>
  );
}

export function FoodMix({ g }: { g: GroupSummary }) {
  const total = g.food.veg + g.food.non_veg + g.food.both;
  if (!total) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-ink-soft">
      <span className="inline-flex items-center gap-1.5"><Leaf className="h-3.5 w-3.5 text-signal" aria-hidden /><span className="font-semibold text-ink">{g.food.veg}</span> veg</span>
      <span className="inline-flex items-center gap-1.5"><Utensils className="h-3.5 w-3.5 text-[#eb6834]" aria-hidden /><span className="font-semibold text-ink">{g.food.non_veg}</span> non-veg</span>
      <span><span className="font-semibold text-ink">{g.food.both}</span> eat anything</span>
      {g.food.veg > 0 && <span className="text-ink-mute">· every meal stop will have veg options</span>}
    </p>
  );
}
