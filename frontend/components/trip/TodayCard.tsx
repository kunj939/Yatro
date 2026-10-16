"use client";

import { useEffect, useMemo, useState } from "react";
import { BellRing, Check, Clock, ExternalLink, Navigation, RotateCcw, Smartphone, Sparkles, TimerReset, TrainFront } from "lucide-react";
import type { ItineraryItem, LateResult, Trip } from "@/lib/types";
import { ApiError, endpoints } from "@/lib/api";
import { minutes as fmtMinutes } from "@/lib/format";

// Live trip mode (ADR 0021). Everything here is worked out from the saved plan and the destination's clock; the only
// call is "Running late", which re-flows the rest of today on the server without touching any provider.

/** The destination's wall clock as "YYYY-MM-DDTHH:MM" (how plan times are stored). `?now=` overrides it, for trying it out. */
function clockIn(tz: string): string {
  if (typeof window !== "undefined") {
    const forced = new URLSearchParams(window.location.search).get("now");
    if (forced && /^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(forced)) return forced;
  }
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz || "Asia/Kolkata", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date()).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}
const toMin = (iso: string) => { const [d, t] = iso.split("T"); const [h, m] = t.split(":").map(Number); return Date.parse(`${d}T00:00:00Z`) / 60000 + h * 60 + m; };
const hhmm = (iso: string) => iso.slice(11, 16);
const addMin = (iso: string, n: number) => { const d = new Date(Date.parse(`${iso}:00Z`) + n * 60000); return d.toISOString().slice(0, 16); };

function slack(i: ItineraryItem) {
  if (i.kind !== "transport") return 5;
  return Number(i.meta?.check_in_minutes) || 15;
}
function leaveBy(i: ItineraryItem): string { return addMin(i.start_at, -((i.travel_minutes_from_prev || 0) + slack(i))); }
function directions(i: ItineraryItem): string | null {
  const lat = i.meta?.lat ?? i.meta?.from?.[0], lng = i.meta?.lng ?? i.meta?.from?.[1];
  if (typeof lat === "number" && typeof lng === "number") return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
  return i.meta?.maps_url || null;
}
function inWords(mins: number) {
  if (mins <= 0) return "now";
  if (mins < 60) return `in ${mins} min`;
  const h = Math.floor(mins / 60), m = mins % 60;
  return `in ${h} h${m ? ` ${m} min` : ""}`;
}

export function TodayCard({ trip, onTrip }: { trip: Trip; onTrip: (t: Trip) => void }) {
  const [now, setNow] = useState(() => clockIn(trip.timezone));
  useEffect(() => { const t = setInterval(() => setNow(clockIn(trip.timezone)), 30_000); return () => clearInterval(t); }, [trip.timezone]);
  const days = trip.itinerary?.days ?? [];
  const today = now.slice(0, 10);
  const dayN = days.findIndex((d) => d.date === today);
  const day = dayN >= 0 ? days[dayN] : null;

  const storeKey = `yatro.done.${trip.id}`;
  const [done, setDone] = useState<string[]>([]);
  useEffect(() => { try { setDone(JSON.parse(localStorage.getItem(storeKey) || "[]")); } catch { /* storage unavailable */ } }, [storeKey]);
  const toggle = (id: string) => setDone((d) => {
    const next = d.includes(id) ? d.filter((x) => x !== id) : [...d, id];
    try { localStorage.setItem(storeKey, JSON.stringify(next)); } catch { /* storage unavailable */ }
    return next;
  });

  const [late, setLate] = useState<LateResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const [wake, setWake] = useState<any>(null);
  const [reminder, setReminder] = useState<string | null>(null);

  const stops = useMemo(() => (day?.items ?? []).filter((i) => i.kind !== "free" && !(i.kind === "stay" && i.start_at === i.end_at && i.position !== 0)), [day]);
  const current = stops.find((i) => i.start_at <= now && now < i.end_at && i.kind !== "arrival");
  const next = stops.find((i) => i.start_at > now && i.kind !== "arrival");
  const left = next ? toMin(leaveBy(next)) - toMin(now) : null;
  const train = stops.find((i) => i.kind === "transport" && i.start_at >= now);

  // a quiet countdown before the trip: live mode is coming, nothing to do yet
  if (!day) {
    const first = days[0]?.date;
    const until = first ? Math.round((Date.parse(`${first}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86400000) : -1;
    if (until < 1 || until > 7) return null;
    return (
      <section className="mt-6 flex items-center gap-3 rounded-2xl border border-line bg-paper/85 px-4 py-3 text-[14px] text-ink-soft">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-signal-soft text-signal-deep"><Sparkles className="h-4 w-4" aria-hidden /></span>
        <p><span className="font-semibold text-ink">Trip starts {until === 1 ? "tomorrow" : `in ${until} days`}.</span> Live mode switches on that morning: what&apos;s next, when to leave, and a fix if you run late.</p>
      </section>
    );
  }

  async function runLate(mins: number) {
    if (!day || !mins) return;
    setBusy(true); setError(null);
    try {
      const out = await endpoints.liveLate(trip.id, { minutes: mins, day_index: day.index, now });
      setLate(out.result);
      if (out.result.status === "shifted") onTrip(out.trip);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't move the plan. Try again."); }
    finally { setBusy(false); setCustom(""); }
  }
  async function undo() {
    setBusy(true); setError(null);
    try { const out = await endpoints.liveUndo(trip.id); onTrip(out.trip); setLate(null); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't undo. Try again."); }
    finally { setBusy(false); }
  }
  async function keepAwake() {
    try {
      if (wake) { await wake.release(); setWake(null); return; }
      setWake(await (navigator as any).wakeLock?.request("screen"));
    } catch { /* not supported */ }
  }
  async function remind() {
    if (!next || left === null || typeof Notification === "undefined") return;
    const ok = Notification.permission === "granted" || (await Notification.requestPermission()) === "granted";
    if (!ok) { setReminder("Notifications are blocked for this site."); return; }
    const wait = Math.max(0, (left - 10) * 60000);
    setTimeout(() => new Notification(`Leave in 10 min for ${next.title}`, { body: `Leave by ${hhmm(leaveBy(next))} to be there at ${hhmm(next.start_at)}.` }), wait);
    setReminder(`We'll remind you at ${hhmm(addMin(leaveBy(next), -10))} while this page is open.`);
  }

  const doneCount = stops.filter((i) => done.includes(i.id)).length;
  const closing = next?.closing_time ? next.closing_time : null;
  const closeSoon = next && closing ? toMin(`${today}T${closing}`) - toMin(next.end_at) : null;

  return (
    <section className="relative mt-6 overflow-hidden rounded-3xl border border-line bg-paper shadow-[0_1px_2px_rgb(var(--shadow)/0.05),0_24px_60px_-34px_rgb(var(--shadow)/0.4)]" aria-label="Today on this trip">
      <div aria-hidden className="pointer-events-none absolute -right-24 -top-28 h-72 w-72 rounded-full bg-signal/12 blur-3xl" />
      <div className="relative p-5 md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="inline-flex items-center gap-2 rounded-full border border-signal/25 bg-signal-soft/70 px-3 py-1 text-[12px] font-bold uppercase tracking-[0.14em] text-signal-deep">
            <span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal opacity-60" /><span className="relative h-2 w-2 rounded-full bg-signal" /></span>
            Live · {day.label} of {days.length}
          </p>
          <p className="num text-[13px] text-ink-mute">{hhmm(now)} in {trip.destination} · {doneCount} of {stops.length} done</p>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div className="rounded-2xl border border-line bg-haze/50 p-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-mute">Now</p>
            {current ? (
              <>
                <p className="mt-1 text-[18px] font-bold leading-snug text-ink">{current.title}</p>
                <p className="num text-[13px] text-ink-soft">until {hhmm(current.end_at)} · {fmtMinutes(toMin(current.end_at) - toMin(now))} left</p>
                <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-haze-2" aria-hidden>
                  <div className="h-full rounded-full bg-signal" style={{ width: `${Math.min(100, ((toMin(now) - toMin(current.start_at)) / Math.max(1, toMin(current.end_at) - toMin(current.start_at))) * 100)}%` }} />
                </div>
              </>
            ) : (
              <p className="mt-1 text-[16px] font-semibold text-ink-soft">
                {next ? (left !== null && left <= 0 ? `On the way to ${next.title}` : `Free until ${hhmm(leaveBy(next))}`) : "That's the day done. Rest up."}
              </p>
            )}
          </div>
          <div className="rounded-2xl border border-signal/25 bg-gradient-to-br from-signal-soft/70 to-paper p-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-signal-deep">Next</p>
            {next ? (
              <>
                <p className="mt-1 text-[18px] font-bold leading-snug text-ink">{next.title}</p>
                <p className="num mt-0.5 flex flex-wrap items-baseline gap-x-2">
                  <span className="headline-font text-[26px] font-extrabold text-ink">Leave by {hhmm(leaveBy(next))}</span>
                  <span className={`text-[13px] font-semibold ${left !== null && left <= 10 ? "text-[#b84a1c]" : "text-ink-soft"}`}>{left !== null && left < 0 ? "you're running late" : inWords(left ?? 0)}</span>
                </p>
                <p className="num text-[13px] text-ink-soft">
                  {next.travel_minutes_from_prev ? `${next.travel_minutes_from_prev} min ${next.travel_mode === "walk" ? "on foot" : "by road"}, ` : ""}there at {hhmm(next.start_at)}
                  {closeSoon !== null && closeSoon >= 0 && closeSoon <= 45 ? ` · closes ${closing}` : ""}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {directions(next) && (
                    <a href={directions(next)!} target="_blank" rel="noopener noreferrer" className="btn-primary !rounded-full !px-4 !py-2 !text-[13px]">
                      <Navigation className="h-3.5 w-3.5" aria-hidden />Directions
                    </a>
                  )}
                  {typeof Notification !== "undefined" && left !== null && left > 10 && (
                    <button type="button" onClick={remind} className="btn-ghost !rounded-full !px-4 !py-2 !text-[13px]"><BellRing className="h-3.5 w-3.5" aria-hidden />Remind me</button>
                  )}
                </div>
                {reminder && <p className="mt-2 text-[12px] text-ink-mute">{reminder}</p>}
              </>
            ) : (
              <p className="mt-1 text-[16px] font-semibold text-ink-soft">Nothing else planned today.</p>
            )}
          </div>
        </div>

        {/* the day as a strip: tick off what's done */}
        <ol className="mt-4 flex gap-1.5 overflow-x-auto pb-1 [scrollbar-width:thin]" aria-label="Today's stops">
          {stops.map((i) => {
            const isDone = done.includes(i.id), isNow = current?.id === i.id, isNext = next?.id === i.id;
            return (
              <li key={i.id} className="shrink-0">
                <button type="button" onClick={() => toggle(i.id)} aria-pressed={isDone} title={isDone ? "Mark as not done" : "Mark as done"}
                        className={`flex max-w-[190px] items-center gap-2 rounded-full border py-1.5 pl-1.5 pr-3 text-left text-[12.5px] transition-all ${
                          isDone ? "border-line bg-haze text-ink-mute line-through" : isNow ? "border-signal bg-signal-soft text-ink" : isNext ? "border-signal/40 bg-paper text-ink" : "border-line bg-paper text-ink-soft hover:border-ink/30"}`}>
                  <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ${isDone ? "bg-signal text-white" : "border border-line bg-paper"}`}>
                    {isDone ? <Check className="h-3 w-3" aria-hidden /> : null}
                  </span>
                  <span className="num font-semibold no-underline">{hhmm(i.start_at)}</span>
                  <span className="truncate">{i.title}</span>
                </button>
              </li>
            );
          })}
        </ol>

        {/* running late: the rest of today moves, offline; tickets keep their times */}
        <div className="mt-4 rounded-2xl border border-line bg-paper/80 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="flex items-center gap-2 font-semibold text-ink"><TimerReset className="h-4 w-4 text-[#b84a1c]" aria-hidden />Running late?</p>
            <div className="flex flex-wrap items-center gap-1.5">
              {[15, 30, 60].map((m) => (
                <button key={m} type="button" disabled={busy} onClick={() => runLate(m)}
                        className="num rounded-full border border-line bg-paper px-3.5 py-1.5 text-[13px] font-semibold text-ink transition-all hover:border-[#eb6834]/50 hover:bg-[#eb6834]/5 disabled:opacity-50">
                  +{m === 60 ? "1 h" : `${m} min`}
                </button>
              ))}
              <form onSubmit={(e) => { e.preventDefault(); runLate(Number(custom)); }} className="flex items-center gap-1">
                <label htmlFor="late-min" className="sr-only">Minutes late</label>
                <input id="late-min" inputMode="numeric" placeholder="min" value={custom} onChange={(e) => setCustom(e.target.value.replace(/\D/g, "").slice(0, 3))}
                       className="num w-16 rounded-full border border-line bg-paper px-3 py-1.5 text-[13px] focus:border-signal focus:outline-none" />
                <button disabled={busy || !custom} className="rounded-full bg-ink px-3 py-1.5 text-[13px] font-semibold text-paper disabled:opacity-40">Move</button>
              </form>
            </div>
          </div>
          <p className="mt-1.5 text-[12.5px] text-ink-mute">We push the rest of today back, drop what would be closed, and never touch your train or flight. It doesn&apos;t use a re-plan.</p>
          {late && (
            <div className={`fade-up mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl px-3.5 py-3 text-[14px] ${
              late.status === "missed" ? "bg-[#eb6834]/10 text-[#8f3a14]" : late.status === "shifted" ? "bg-signal-soft text-ink" : "bg-haze text-ink-soft"}`} role="status">
              <span className="min-w-0">{late.note}</span>
              {late.status === "shifted" && (
                <button type="button" onClick={undo} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-paper px-3 py-1 text-[13px] font-semibold text-ink hover:border-ink/30">
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden />Undo
                </button>
              )}
            </div>
          )}
          {error && <p role="alert" className="mt-2 text-[13px] font-medium text-danger">{error}</p>}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
          {train && (
            <a href="https://enquiry.indianrail.gov.in/mntes/" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 font-semibold text-signal-deep hover:underline">
              <TrainFront className="h-3.5 w-3.5" aria-hidden />Live status of {train.title.replace(/^Train:\s*/, "").split(" (")[0]} on NTES<ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          )}
          {typeof navigator !== "undefined" && "wakeLock" in navigator && (
            <button type="button" onClick={keepAwake} className="inline-flex items-center gap-1.5 font-semibold text-ink-soft hover:text-ink">
              <Smartphone className="h-3.5 w-3.5" aria-hidden />{wake ? "Screen stays on" : "Keep screen on"}
            </button>
          )}
          <span className="inline-flex items-center gap-1.5 text-ink-mute"><Clock className="h-3.5 w-3.5" aria-hidden />Times are {trip.destination}&apos;s local clock</span>
        </div>
      </div>
    </section>
  );
}
