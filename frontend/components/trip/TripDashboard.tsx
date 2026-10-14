"use client";

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, Baby, CalendarDays, Share2, ChevronDown, Clock, CloudSun, Database, FlaskConical, History, Info, ListChecks, ListOrdered, Map as MapIcon, MapPin, Navigation, Repeat, Send, Sparkles, Users, Wallet } from "lucide-react";
import type { Adjustment, ItineraryItem, Trip } from "@/lib/types";
import { ApiError } from "@/lib/api";
import { usePlanStream } from "@/lib/usePlanStream";
import { MODE_LABEL, TRAVELLER_LABEL, ageSummary, currencySymbol, money, niceDate, plural, reopenAt, setDisplayCurrency, time12 } from "@/lib/format";
import { Timeline, dayStats, isWeekly, weekOf, weekStats, type DayFilter, type PlanView } from "./Timeline";
import { itemIcon } from "./TimelineItem";
import { AlternativeDrawer } from "./AlternativeDrawer";
import { BudgetPanel } from "./BudgetPanel";
import { MapPanel } from "./MapPanel";
import { GroupMembers } from "./GroupMembers";
import { ExpenseSplitter } from "./ExpenseSplitter";
import { ShareSheet, SharedLinks } from "./ShareSheet";
import { PlanActions } from "./PlanActions";
import { TripChat } from "./TripChat";
import { useFlags } from "@/lib/flags";
import { InfeasibleView } from "./InfeasibleView";
import { TripLoading } from "./TripLoading";
import { VERIFICATION_LEGEND, VerificationBadge } from "../VerificationBadge";
import { planSources } from "@/lib/sources";

type Tab = "timeline" | "map" | "budget" | "group";
const TABS: { key: Tab; label: string; icon: any }[] = [
  { key: "timeline", label: "Itinerary", icon: ListOrdered }, { key: "map", label: "Map", icon: MapIcon },
  { key: "budget", label: "Budget", icon: Wallet }, { key: "group", label: "Group", icon: Users },
];

/** The trip's title with the destination's name in the accent style ("Agra in 2 days for two"). */
function withPlace(title: string, destination: string) {
  const name = (destination || "").split(",")[0].trim();
  const at = name ? title.toLowerCase().indexOf(name.toLowerCase()) : -1;
  if (at < 0) return title;
  return <>{title.slice(0, at)}<span className="accent highlight-night">{title.slice(at, at + name.length)}</span>{title.slice(at + name.length)}</>;
}

function itemKeys(trip: Trip | null) {
  return new Set(trip?.itinerary?.days.flatMap((d) => d.items.map((i) => `${i.kind}:${i.title}`)) ?? []);
}

export function TripDashboard({ initial, readOnly = false, onReload }: {
  initial: Trip; readOnly?: boolean; onReload?: () => Promise<Trip | null>;
}) {
  const router = useRouter();
  const [trip, setTrip] = useState<Trip>(initial);
  const [tab, setTab] = useState<Tab>("timeline");
  const [changing, setChanging] = useState<ItineraryItem | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null); // the answer to a request that changed nothing
  const [highlight, setHighlight] = useState<string[]>([]);
  const [sharing, setSharing] = useState(false);
  const [shareTick, setShareTick] = useState(0); // bumps when a link is made, so the Shared links list reloads
  // How the plan reads: one line per stop (compact, the default) or full cards; and one day at a time or all of them.
  const [view, setView] = useState<PlanView>("compact");
  const [day, setDay] = useState<DayFilter>("all");
  useEffect(() => { try { const v = localStorage.getItem("yatro.planView"); if (v === "detailed" || v === "compact") setView(v); } catch { /* storage unavailable */ } }, []);
  const chooseView = (v: PlanView) => { setView(v); try { localStorage.setItem("yatro.planView", v); } catch { /* storage unavailable */ } };
  // Prices in the destination's money (how the plan is costed) or, roughly, in the money of the starting point.
  const home = trip.home_currency && trip.home_rate ? { code: trip.home_currency, rate: trip.home_rate } : null;
  const [inHome, setInHome] = useState(false);
  useEffect(() => { try { setInHome(localStorage.getItem("yaitro.homeMoney") === "1"); } catch { /* storage unavailable */ } }, []);
  const chooseMoney = (h: boolean) => { setInHome(h); try { localStorage.setItem("yaitro.homeMoney", h ? "1" : "0"); } catch { /* storage unavailable */ } };
  // Set while rendering, so every price below (header, timeline, budget, alternatives) comes out in the chosen money.
  setDisplayCurrency(home && inHome ? { from: trip.currency, to: home.code, rate: home.rate } : null);
  useEffect(() => () => setDisplayCurrency(null), []);
  const flags = useFlags();
  const canShare = !readOnly && !!trip.itinerary && flags.share;
  const { shown, running, queued, run } = usePlanStream();
  const it = trip.itinerary;
  const infeasible = trip.feasibility && trip.feasibility.feasible === false;

  useEffect(() => { setTrip(initial); }, [initial]);
  useEffect(() => {
    if (!highlight.length) return;
    const t = setTimeout(() => setHighlight([]), 7000);
    return () => clearTimeout(t);
  }, [highlight]);

  const reload = useCallback(async () => {
    const fresh = onReload ? await onReload() : null;
    if (fresh) setTrip(fresh);
  }, [onReload]);

  const replan = useCallback(async (path: string, body: unknown, done: string): Promise<string> => {
    if (readOnly) { router.push("/plan"); return "Plan your own trip to change it."; }
    setError(null); setNotice(null); setReply(null); setChanging(null);
    const before = itemKeys(trip);
    try {
      const out = await run(path, body);
      setTrip(out.trip);
      if (out.status === "unavailable") {  // a normal answer: the plan stays as it was
        const text = `We can't update the plan right now. Your current plan is unchanged${out.retry_at ? `. Try again ${reopenAt(out.retry_at)}` : ""}.`;
        setReply(text);
        return text;
      }
      if (out.status === "no_change") {
        setReply(out.message || "Nothing to change.");
        return out.message || "Nothing to change."; // the plan is as it was
      }
      if (out.status === "infeasible") {
        window.scrollTo({ top: 0, behavior: "smooth" });
        setMessage("");
        return out.trip.feasibility?.message ? `${out.trip.feasibility.message} The ways to fix it are at the top of the page.`
          : "That change doesn't fit. The ways to fix it are at the top of the page.";
      } else {
        setNotice(out.trip.itinerary?.change_note || done);
        setHighlight(out.trip.itinerary?.days.flatMap((d) => d.items).filter((i) => !before.has(`${i.kind}:${i.title}`)).map((i) => i.id) ?? []);
        setTab("timeline");
      }
      setMessage("");
      return `Done. ${out.trip.itinerary?.change_note || done}`;
    } catch (err) {
      const text = err instanceof ApiError ? err.message : "Re-planning failed. Your current plan is unchanged.";
      setError(text);
      return text;
    }
  }, [readOnly, router, run, trip]);

  const swap = (altId: string) => changing &&
    replan(`/api/trips/${trip.id}/replan?stream=true`, { swap: { item_id: changing.id, alternative_id: altId } }, "Swapped and re-planned.");
  const applyAdjustment = (a: Adjustment) =>
    replan(`/api/trips/${trip.id}/plan?stream=true`, { changes: a.apply }, `Applied: ${a.label}.`);
  const sendMessage = (text: string): Promise<string> =>
    replan(`/api/trips/${trip.id}/replan?stream=true`, { message: text.trim() }, "Plan updated.");

  const firstActivity = useMemo(() => it?.days.flatMap((d) => d.items).find((i) => i.kind === "activity"), [it]);
  const firstTransport = useMemo(() => it?.days.flatMap((d) => d.items).find((i) => i.kind === "transport"), [it]);
  const chips = [
    "Make it cheaper", "More relaxed", "See more places", "More romantic", "Add one more day",
    ...(firstActivity ? [`Skip ${firstActivity.title.split(" (")[0]}`] : []),
  ];
  const demo = (it?.data_mode ?? trip.data_mode) === "demo";

  const used = it ? Math.min(100, (it.total_cost / Math.max(1, trip.budget)) * 100) : 0;
  const over = !!it && it.remaining < 0;
  const ModeIcon = firstTransport ? itemIcon(firstTransport) : Navigation;
  function jumpTo(n: DayFilter) {
    setTab("timeline");
    setDay(n);  // show just that day (or week); "All days" brings the rest back
    setTimeout(() => document.getElementById("plan-top")?.scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  }

  return (
    <div className="container-x pb-24 pt-4">
      {/* Header: one light card. Title, route and facts on the left; the money on the right. */}
      <header className="card p-4 sm:p-5 md:p-6">
        <div className="flex flex-wrap items-center gap-1.5 text-[13px] text-ink-soft">
          {demo ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-haze-2 px-2.5 py-0.5 font-semibold text-ink"><FlaskConical className="h-3.5 w-3.5" aria-hidden />Demo: sample data</span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-soft px-2.5 py-0.5 font-semibold text-teal-deep">
              <span className="board-live h-1.5 w-1.5 rounded-full bg-teal" />Live data
            </span>
          )}
          {readOnly && <span className="rounded-full border border-line px-2.5 py-0.5">Read-only sample</span>}
          {it && <span className="rounded-full border border-line px-2.5 py-0.5">v{it.version}</span>}
          {home ? (
            <span className="inline-flex items-center rounded-full border border-line p-0.5" role="group" aria-label="Show prices in">
              {([[false, trip.currency], [true, home.code]] as [boolean, string][]).map(([h, code]) => (
                <button key={code} type="button" aria-pressed={inHome === h} onClick={() => chooseMoney(h)}
                        title={h ? `Roughly, at ${currencySymbol(home.code)}${home.rate.toFixed(home.rate < 10 ? 2 : 1)} per ${trip.currency}` : "As the plan is priced"}
                        className={`rounded-full px-2.5 py-0.5 font-semibold transition-colors ${inHome === h ? "bg-signal text-on-signal" : "text-ink-soft hover:text-ink"}`}>
                  {currencySymbol(code) !== code ? `${currencySymbol(code)} ${code}` : code}
                </button>
              ))}
            </span>
          ) : (
            <span className="rounded-full border border-line px-2.5 py-0.5">{trip.currency}</span>
          )}
          {home && inHome && <span className="text-ink-mute">≈ approximate</span>}
          {canShare && (
            <button onClick={() => setSharing(true)} className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 font-semibold text-ink transition-colors hover:border-signal/50">
              <Share2 className="h-3.5 w-3.5" aria-hidden />Share
            </button>
          )}
        </div>

        <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,25rem)] lg:items-end lg:gap-8">
          <div className="min-w-0">
            <h1 className="text-[24px] font-extrabold leading-tight tracking-tight sm:text-[30px] md:text-[34px]">
              {withPlace(it?.title ?? trip.title, trip.destination)}
            </h1>
            <p className="mt-2 flex min-w-0 items-center gap-2 text-[15px] font-semibold">
              <span className="truncate">{trip.origin}</span>
              <span aria-hidden className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-signal text-on-signal"><ModeIcon className="h-3.5 w-3.5" /></span>
              <span className="truncate">{trip.destination}</span>
            </p>
            <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[13px] text-ink-soft">
              <Chip><CalendarDays className="h-3.5 w-3.5" aria-hidden />{niceDate(trip.start_date)}{trip.end_date !== trip.start_date ? ` – ${niceDate(trip.end_date)}` : ""}</Chip>
              <Chip><Clock className="h-3.5 w-3.5" aria-hidden />{time12(trip.start_time)} – {time12(trip.end_time)}</Chip>
              <Chip><Users className="h-3.5 w-3.5" aria-hidden />{plural(trip.travellers, "traveller")}{trip.traveller_type ? ` · ${TRAVELLER_LABEL[trip.traveller_type] ?? trip.traveller_type}` : ""}</Chip>
              {!!trip.preferences?.age_groups?.length && trip.preferences.age_groups.some((a) => a !== "adults") && (
                <Chip><Baby className="h-3.5 w-3.5" aria-hidden /><span className="sm:hidden">{ageSummary(trip.preferences.age_groups, true)}</span>
                  <span className="hidden sm:inline">{ageSummary(trip.preferences.age_groups)}</span></Chip>)}
              {it && <Chip><ListChecks className="h-3.5 w-3.5" aria-hidden />{plural(it.days.reduce((a, d) => a + dayStats(d.items).stops, 0), "stop")}</Chip>}
            </p>
          </div>

          <div className="rounded-2xl bg-haze/70 p-3.5">
            <dl className="num grid grid-cols-4 gap-2 lg:grid-cols-2">
              <Stat label="Budget" value={money(trip.budget, trip.currency)} />
              <Stat label="Planned" value={it ? money(it.total_cost, trip.currency) : "—"} />
              <Stat label={over ? "Over" : "Left"} value={it ? money(Math.abs(it.remaining), trip.currency) : "—"} accent={over ? "text-danger" : "text-teal-deep"} />
              <Stat label="Per person" value={it ? money(Math.round(it.budget.per_person), trip.currency) : "—"} />
            </dl>
            {it && (
              <div className="mt-2.5 flex items-center gap-2.5">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-haze-2">
                  <div className={`h-full rounded-full transition-[width] duration-1000 ${over ? "bg-danger" : "bg-gradient-to-r from-signal to-spark"}`} style={{ width: `${used}%` }} />
                </div>
                <span className="num text-[12px] font-semibold text-ink-soft">{Math.round(used)}% used</span>
              </div>
            )}
          </div>
        </div>
      </header>

      {notice && (
        <div role="status" className="fade-up mt-5 flex items-center gap-2 rounded-xl border border-teal/30 bg-teal-soft px-4 py-3 text-[16px] font-medium text-teal-deep"><Sparkles className="h-4 w-4 shrink-0" aria-hidden />{notice}</div>
      )}
      {error && !it && (
        <div role="alert" className="mt-5 rounded-xl bg-danger-soft px-4 py-3 text-[16px] font-medium text-danger">{error}</div>
      )}

      {infeasible && (
        <div className="mt-6">
          <InfeasibleView feasibility={trip.feasibility!} onApply={applyAdjustment} busy={running} readOnly={readOnly} hasPlan={!!it} currency={trip.currency} />
        </div>
      )}

      {it && (
        <>
          {/* Not sticky: floating under the navbar it covered the timeline (half of the Day 2 heading) while scrolling. */}
          <div className="mt-6">
            <div className="grid w-full grid-cols-4 gap-1 rounded-2xl border border-line bg-paper/85 p-1.5 shadow-[0_10px_30px_-20px_rgb(var(--shadow)/0.5)] backdrop-blur-xl sm:flex sm:w-fit" role="tablist" aria-label="Trip views">
              {TABS.map((t) => {
                const on = tab === t.key;
                const Icon = t.icon;
                return (
                  <button key={t.key} role="tab" id={`tab-${t.key}`} aria-controls={`panel-${t.key}`} aria-selected={on}
                          onClick={() => setTab(t.key)}
                          className={`inline-flex min-w-0 items-center justify-center gap-1.5 rounded-xl px-1.5 py-2.5 text-[13px] font-semibold transition-all duration-200 sm:gap-2 sm:px-4 sm:text-[14px] ${
                            on ? "bg-signal text-on-signal shadow-[0_6px_18px_-8px_rgb(var(--signal)/0.8)]" : "text-ink-mute hover:bg-haze hover:text-ink"}`}>
                    <Icon className="hidden h-4 w-4 shrink-0 min-[400px]:block" aria-hidden /><span className="truncate">{t.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div key={tab} className="fade-up mt-6" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === "timeline" && (
              <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
                <div className="min-w-0">
                  {it.summary && <p className={`max-w-2xl text-[16px] leading-relaxed text-ink-soft sm:text-[18px] ${it.brownout ? "mb-2" : "mb-6"}`}>{it.summary}</p>}
                  {/* A plan made while we were busy says so next to the part it affects, in small quiet text (ADR 0008) */}
                  {it.brownout && (
                    <div className="mb-6 space-y-1">
                      {it.brownout.labels.description && <BrownoutNote text={it.brownout.labels.description} />}
                      {it.brownout.labels.options && <BrownoutNote text={it.brownout.labels.options} />}
                      {it.brownout.labels.alternatives && <BrownoutNote text={it.brownout.labels.alternatives} icon={Repeat} />}
                    </div>
                  )}
                  {!readOnly && <div className="hidden lg:block"><PlanActions trip={trip} onShare={canShare ? () => setSharing(true) : undefined} /></div>}
                  {it.llm_status === "fallback" && (
                    <p className="mb-5 rounded-xl bg-haze-2 px-4 py-3 text-sm text-ink-soft">Trip data was collected, but AI explanation is temporarily unavailable.</p>
                  )}
                  <div id="plan-top" className="mb-4 flex scroll-mt-28 flex-wrap items-center justify-between gap-3 sm:flex-nowrap">
                    {it.days.length > 1 ? (
                      <div className="-mx-1 flex min-w-0 flex-1 basis-full gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:thin] sm:basis-0" role="group" aria-label="Show which days">
                        {([["all", "All days"], ...(isWeekly(it)
                            ? Array.from({ length: weekOf(it.days.length - 1) + 1 }, (_, w) => [`w${w}`, `Week ${w + 1}`])
                            : it.days.map((d, n) => [d.index, `Day ${n + 1} · ${niceDate(d.date, { day: "numeric", month: "short" })}`]))] as [DayFilter, string][]).map(([v, l]) => (
                          <button key={String(v)} type="button" aria-pressed={day === v} onClick={() => setDay(v)} className={`shrink-0 whitespace-nowrap ${day === v ? "chip-on !py-1.5" : "chip-off !py-1.5"}`}>{l}</button>
                        ))}
                      </div>
                    ) : <span />}
                    <div className="inline-flex shrink-0 rounded-xl border border-line bg-paper p-1 text-[13px] font-semibold" role="group" aria-label="How much detail">
                      {([["compact", "Compact"], ["detailed", "Detailed"]] as [PlanView, string][]).map(([v, l]) => (
                        <button key={v} type="button" aria-pressed={view === v} onClick={() => chooseView(v)}
                                className={`rounded-lg px-3 py-1.5 transition-colors ${view === v ? "bg-signal text-on-signal" : "text-ink-mute hover:text-ink"}`}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <Timeline itinerary={it} onChange={setChanging} highlightIds={highlight} currency={trip.currency} near={trip.destination}
                            tripId={readOnly ? undefined : trip.id} view={view} day={day} />
                  {/* Google's terms: its content shown outside a Google map carries "Google Maps", unaltered (ADR 0005) */}
                  {planSources(it).some((s) => s.from === "Google Maps") && (
                    <p className="mt-3 whitespace-nowrap text-right text-[12px] text-ink-mute">Place details from Google Maps</p>
                  )}
                  {/* On a phone the plan comes first: keeping it and changing it follow the itinerary, not precede it */}
                  <div className="mt-6 space-y-6 lg:hidden">
                    {!readOnly && <PlanActions trip={trip} onShare={canShare ? () => setSharing(true) : undefined} />}
                    <TripChat tripId={trip.id} chips={chips} busy={running} readOnly={readOnly} replansLeft={trip.replans_left} onChange={sendMessage} />
                  </div>
                </div>
                <aside className="space-y-5">
                  <div className="hidden lg:block">
                    <TripChat tripId={trip.id} chips={chips} busy={running} readOnly={readOnly} replansLeft={trip.replans_left} onChange={sendMessage} />
                  </div>
                  {trip.feasibility?.transport && (
                    <SideCard icon={Sparkles} title="Why this route">
                      <p>{trip.feasibility.transport}</p>
                      {it.local_mode && <p className="mt-2 text-ink-mute">Getting around: {MODE_LABEL[it.local_mode] ?? it.local_mode}</p>}
                    </SideCard>
                  )}
                  {it.weather && (
                    <SideCard icon={CloudSun} title="Weather">
                      <p>{it.weather.summary}{it.weather.max_c !== null ? `, up to ${Math.round(it.weather.max_c)}°C` : ""}{it.weather.rain_chance !== null ? `, ${it.weather.rain_chance}% chance of rain` : ""}.</p>
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <VerificationBadge state={it.weather.state} />
                        <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer" className="text-[12px] text-ink-mute underline-offset-2 hover:underline">
                          Weather by Open-Meteo.com
                        </a>
                      </div>
                    </SideCard>
                  )}
                  {canShare && (
                    <SideCard icon={Share2} title="Shared links" folded>
                      <SharedLinks tripId={trip.id} refreshKey={shareTick} />
                    </SideCard>
                  )}
                  {it.warnings.length > 0 && <PlanNotes notes={it.warnings} />}
                  {it.tips.length > 0 && (
                    <SideCard icon={Wallet} title="Good to know">
                      <ul className="list-disc space-y-1.5 pl-4">{it.tips.map((t, i) => <li key={i}>{t}</li>)}</ul>
                    </SideCard>
                  )}
                  <SideCard icon={ListChecks} title="How this plan was checked" folded>
                    <ol className="space-y-1.5">
                      {it.pipeline_trace.map((s) => (
                        <li key={s.key}>
                          <span className="font-medium text-ink">{s.label}</span>
                          {s.detail && <span className="block text-[13px] text-ink-mute">{s.detail}</span>}
                        </li>
                      ))}
                    </ol>
                    <div className="mt-4 flex flex-wrap gap-1.5 border-t border-line pt-3">
                      {VERIFICATION_LEGEND.map((l) => <span key={l.key} title={l.hint}><VerificationBadge state={l.key} /></span>)}
                    </div>
                  </SideCard>
                  <SideCard icon={Database} title="Where this data comes from" folded>
                    <ul className="space-y-2">
                      {planSources(it).map((s) => (
                        <li key={s.what}><span className="block text-[13px] text-ink-mute">{s.what}</span><span className="font-medium text-ink">{s.from}</span></li>
                      ))}
                    </ul>
                  </SideCard>
                  {trip.versions.length > 1 && (
                    <SideCard icon={History} title="Versions">
                      <ol className="space-y-2">
                        {trip.versions.slice().reverse().map((v) => (
                          <li key={v.version} className="flex justify-between gap-3">
                            <span><span className="font-medium text-ink">Version {v.version}</span>{v.change_note && <span className="block text-[13px] text-ink-mute">{v.change_note}</span>}</span>
                            <span className="num shrink-0">{money(v.total_cost, trip.currency)}</span>
                          </li>
                        ))}
                      </ol>
                    </SideCard>
                  )}
                </aside>
              </div>
            )}
            {tab === "map" && <MapPanel trip={trip} />}
            {tab === "budget" && <BudgetPanel budget={it.budget} expenses={trip.expense_summary} currency={trip.currency} />}
            {tab === "group" && (
              <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
                <GroupMembers trip={trip} onChanged={reload} readOnly={readOnly} />
                <ExpenseSplitter trip={trip} onChanged={reload} readOnly={readOnly} />
              </div>
            )}
          </div>
        </>
      )}

      {canShare && <ShareSheet trip={trip} open={sharing} onClose={() => setSharing(false)} onChanged={() => setShareTick((n) => n + 1)} />}
      <AlternativeDrawer item={changing} onClose={() => setChanging(null)} onUse={swap} readOnly={readOnly} busy={running} currency={trip.currency} />
      {running && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Updating your plan">
          <div className="w-full max-w-lg">
            <TripLoading shown={shown} queued={queued} title="Re-planning your trip" subtitle="Checking every change against time and budget" compact />
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent = "" }: { label: string; value: string; accent?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11.5px] font-medium text-ink-mute">{label}</dt>
      <dd className={`truncate text-[15px] font-bold sm:text-[17px] ${accent || "text-ink"}`}>{value}</dd>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="num inline-flex items-center gap-1.5 rounded-full bg-haze-2/80 px-2.5 py-1">{children}</span>;
}

function SideCard({ icon: Icon, title, children, folded = false }: { icon: any; title: string; children: React.ReactNode; folded?: boolean }) {
  if (folded) {
    // reference detail (how the plan was checked, data sources, links): one tap away, so the column stays short
    return (
      <details className="group rounded-2xl border border-line bg-paper/80 p-4 text-[14px] text-ink-soft backdrop-blur transition-colors hover:border-ink/20 sm:p-5">
        <summary className="flex cursor-pointer list-none items-center gap-2.5 text-[16px] font-bold text-ink">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-signal-soft"><Icon className="h-4 w-4 text-signal-deep" aria-hidden /></span>{title}
          <ChevronDown className="ml-auto h-4 w-4 text-ink-mute transition-transform group-open:rotate-180" aria-hidden />
        </summary>
        <div className="mt-3">{children}</div>
      </details>
    );
  }
  return (
    <section className="rounded-2xl border border-line bg-paper/80 p-5 text-[14px] text-ink-soft backdrop-blur transition-colors hover:border-ink/20">
      <h2 className="mb-2.5 flex items-center gap-2.5 text-[16px] font-bold text-ink">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-signal-soft"><Icon className="h-4 w-4 text-signal-deep" aria-hidden /></span>{title}
      </h2>
      {children}
    </section>
  );
}

/** Quiet, collapsed notes about the plan (an estimated timetable, a must-see that didn't fit...): there if wanted, never
 *  in the way. Replaces the stack of warning boxes that used to sit above the itinerary. */
function PlanNotes({ notes }: { notes: string[] }) {
  return (
    <details className="group rounded-2xl border border-line bg-paper/70 p-4 text-[14px] text-ink-soft backdrop-blur">
      <summary className="flex cursor-pointer list-none items-center gap-2.5 text-[16px] font-bold text-ink">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-haze-2"><Info className="h-4 w-4 text-ink-soft" aria-hidden /></span>
        Plan notes
        <span className="num rounded-full bg-haze-2 px-2 py-0.5 text-xs font-semibold text-ink-soft">{notes.length}</span>
        <ChevronDown className="ml-auto h-4 w-4 text-ink-mute transition-transform group-open:rotate-180" aria-hidden />
      </summary>
      <ul className="mt-3 list-disc space-y-2 pl-5 leading-relaxed">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
    </details>
  );
}


/** Type anything: the engine reads English and Hinglish, applies what it understands and says plainly what it
 *  couldn't. A request that changes nothing gets an answer here — never a red error — and the text stays put. */

/** One quiet line beside the part of a plan that was made with less effort because we were busy. Not a warning. */
function BrownoutNote({ text, icon: Icon = Info }: { text: string; icon?: any }) {
  return <p className="flex items-center gap-1.5 text-[13px] text-ink-mute"><Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />{text}</p>;
}
