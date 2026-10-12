"use client";

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, Baby, CalendarDays, Share2, ChevronDown, Clock, CloudSun, Database, FlaskConical, History, Info, ListChecks, ListOrdered, Map as MapIcon, MapPin, Navigation, Repeat, Send, Sparkles, Users, Wallet } from "lucide-react";
import type { Adjustment, ItineraryItem, Trip } from "@/lib/types";
import { ApiError } from "@/lib/api";
import { usePlanStream } from "@/lib/usePlanStream";
import { MODE_LABEL, TRAVELLER_LABEL, ageSummary, money, niceDate, plural, reopenAt, time12 } from "@/lib/format";
import { Timeline, dayStats, type PlanView } from "./Timeline";
import { itemIcon } from "./TimelineItem";
import { AlternativeDrawer } from "./AlternativeDrawer";
import { BudgetPanel } from "./BudgetPanel";
import { MapPanel } from "./MapPanel";
import { GroupMembers } from "./GroupMembers";
import { ExpenseSplitter } from "./ExpenseSplitter";
import { ShareSheet, SharedLinks } from "./ShareSheet";
import { WhatsAppCard } from "./WhatsAppCard";
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
  const [day, setDay] = useState<number | "all">("all");
  useEffect(() => { try { const v = localStorage.getItem("yatro.planView"); if (v === "detailed" || v === "compact") setView(v); } catch { /* storage unavailable */ } }, []);
  const chooseView = (v: PlanView) => { setView(v); try { localStorage.setItem("yatro.planView", v); } catch { /* storage unavailable */ } };
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

  const replan = useCallback(async (path: string, body: unknown, done: string) => {
    if (readOnly) { router.push("/plan"); return; }
    setError(null); setNotice(null); setReply(null); setChanging(null);
    const before = itemKeys(trip);
    try {
      const out = await run(path, body);
      setTrip(out.trip);
      if (out.status === "unavailable") {  // a normal answer: the plan stays as it was
        setReply(`We can't update the plan right now. Your current plan is unchanged${out.retry_at ? `. Try again ${reopenAt(out.retry_at)}` : ""}.`);
        return;
      }
      if (out.status === "no_change") {
        setReply(out.message || "Nothing to change.");
        return; // the plan is as it was, and what was typed stays so it can be adjusted
      }
      if (out.status === "infeasible") {
        window.scrollTo({ top: 0, behavior: "smooth" });
      } else {
        setNotice(out.trip.itinerary?.change_note || done);
        setHighlight(out.trip.itinerary?.days.flatMap((d) => d.items).filter((i) => !before.has(`${i.kind}:${i.title}`)).map((i) => i.id) ?? []);
        setTab("timeline");
      }
      setMessage("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Re-planning failed. Your current plan is unchanged.");
    }
  }, [readOnly, router, run, trip]);

  const swap = (altId: string) => changing &&
    replan(`/api/trips/${trip.id}/replan?stream=true`, { swap: { item_id: changing.id, alternative_id: altId } }, "Swapped and re-planned.");
  const applyAdjustment = (a: Adjustment) =>
    replan(`/api/trips/${trip.id}/plan?stream=true`, { changes: a.apply }, `Applied: ${a.label}.`);
  const sendMessage = (text: string) => readOnly ? router.push("/plan") : text.trim() &&
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
  function jumpTo(n: number) {
    setTab("timeline");
    setDay(n);  // show just that day; "All days" brings the rest back
    setTimeout(() => document.getElementById("plan-top")?.scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  }

  return (
    <div className="container-x pb-24 pt-4">
      {/* Header */}
      <header className="relative isolate overflow-hidden rounded-3xl border border-white/10 bg-[#0f1219] p-6 text-white shadow-[0_30px_80px_-40px_rgb(0_0_0/0.8)] md:p-9">
        <div aria-hidden className="absolute -right-24 -top-28 -z-10 h-80 w-80 rounded-full bg-[rgb(190_255_74/0.16)] blur-3xl" />
        <div aria-hidden className="absolute -bottom-32 -left-20 -z-10 h-72 w-72 rounded-full bg-[rgb(45_212_230/0.12)] blur-3xl" />
        <div className="flex flex-wrap items-center gap-2 text-sm text-white/70">
          {demo ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-2.5 py-0.5 font-semibold text-white"><FlaskConical className="h-3.5 w-3.5" aria-hidden />Demo mode: sample data</span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[rgb(45_212_230/0.15)] px-2.5 py-0.5 font-semibold text-[rgb(140_230_240)]">
              <span className="board-live h-1.5 w-1.5 rounded-full bg-[rgb(140_230_240)]" />Live data
            </span>
          )}
          {readOnly && <span className="rounded-full border border-white/25 px-2.5 py-0.5">Read-only sample</span>}
          {it && <span className="rounded-full border border-white/15 px-2.5 py-0.5">Version {it.version}</span>}
          <span className="rounded-full border border-white/15 px-2.5 py-0.5">{trip.currency}</span>
          {canShare && (
            <button onClick={() => setSharing(true)} className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 font-semibold text-white transition-colors hover:bg-white/20">
              <Share2 className="h-3.5 w-3.5" aria-hidden />Share
            </button>
          )}
        </div>
        <h1 className="mt-4 text-[28px] font-extrabold leading-tight tracking-tight sm:text-4xl md:text-[44px]">
          {withPlace(it?.title ?? trip.title, trip.destination)}
        </h1>

        {/* Route line */}
        <div className="mt-6 grid grid-cols-[auto_1fr_auto] items-center gap-3 sm:gap-5">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-[0.14em] text-white/45">From</p>
            <p className="max-w-[28vw] truncate text-base font-semibold sm:max-w-[220px] sm:text-lg">{trip.origin}</p>
          </div>
          <div className="relative flex items-center" aria-hidden>
            <span className="h-2 w-2 shrink-0 rounded-full bg-white/60" />
            <span className="h-px flex-1 border-t border-dashed border-white/30" />
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[rgb(190_255_74)] text-[#0b0d0c] shadow-[0_0_0_6px_rgb(190_255_74/0.15)]">
              <ModeIcon className="h-4 w-4" />
            </span>
            <span className="h-px flex-1 border-t border-dashed border-white/30" />
            <MapPin className="h-4 w-4 shrink-0 text-[rgb(190_255_74)]" />
          </div>
          <div className="min-w-0 text-right">
            <p className="text-xs uppercase tracking-[0.14em] text-white/45">To</p>
            <p className="max-w-[28vw] truncate text-base font-semibold sm:max-w-[220px] sm:text-lg">{trip.destination}</p>
          </div>
        </div>

        <p className="mt-5 flex flex-wrap items-center gap-2 text-[14px] text-white/80">
          <Chip><CalendarDays className="h-3.5 w-3.5" aria-hidden />{niceDate(trip.start_date)}{trip.end_date !== trip.start_date ? ` – ${niceDate(trip.end_date)}` : ""}</Chip>
          <Chip><Clock className="h-3.5 w-3.5" aria-hidden />{time12(trip.start_time)} – {time12(trip.end_time)}</Chip>
          <Chip><Users className="h-3.5 w-3.5" aria-hidden />{plural(trip.travellers, "traveller")}{trip.traveller_type ? ` · ${TRAVELLER_LABEL[trip.traveller_type] ?? trip.traveller_type}` : ""}</Chip>
          {!!trip.preferences?.age_groups?.length && trip.preferences.age_groups.some((a) => a !== "adults") && (
            <Chip><Baby className="h-3.5 w-3.5" aria-hidden />{ageSummary(trip.preferences.age_groups)}</Chip>)}
          {it && <Chip><ListChecks className="h-3.5 w-3.5" aria-hidden />{plural(it.days.reduce((a, d) => a + dayStats(d.items).stops, 0), "stop")}</Chip>}
        </p>

        <dl className="num mt-7 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Budget" value={money(trip.budget, trip.currency)} />
          <Stat label="Planned cost" value={it ? money(it.total_cost, trip.currency) : "—"} />
          <Stat label={over ? "Over budget" : "Remaining"} value={it ? money(Math.abs(it.remaining), trip.currency) : "—"} accent={over ? "text-[rgb(255_150_175)]" : "text-[rgb(190_255_74)]"} />
          <Stat label="Per person" value={it ? money(Math.round(it.budget.per_person), trip.currency) : "—"} />
        </dl>
        {it && (
          <div className="mt-5">
            <div className="flex justify-between text-xs text-white/55"><span>Budget used</span><span className="num font-semibold text-white/80">{Math.round(used)}%</span></div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-white/10">
              <div className={`h-full rounded-full transition-[width] duration-1000 ${over ? "bg-[rgb(255_150_175)]" : "bg-gradient-to-r from-[rgb(190_255_74)] to-[rgb(45_212_230)]"}`} style={{ width: `${used}%` }} />
            </div>
          </div>
        )}
      </header>

      {/* Day-by-day overview: tap a day to jump to it */}
      {it && it.days.length > 1 && (
        <div className="mt-5 grid grid-cols-2 gap-3 sm:flex sm:overflow-x-auto sm:pb-1">
          {it.days.map((d, n) => {
            const st = dayStats(d.items);
            return (
              <button key={d.index} type="button" onClick={() => jumpTo(d.index)}
                      className="group min-w-0 flex-1 sm:min-w-[190px] rounded-2xl border border-line bg-paper/80 p-4 text-left backdrop-blur transition-all hover:-translate-y-0.5 hover:border-signal/50 hover:shadow-glow">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold uppercase tracking-[0.12em] text-signal-deep">Day {n + 1}</span>
                  <ArrowDown className="h-3.5 w-3.5 text-ink-mute transition-transform group-hover:translate-y-0.5" aria-hidden />
                </div>
                <p className="mt-1 font-semibold">{niceDate(d.date)}</p>
                <p className="num mt-2 text-sm text-ink-mute">{plural(st.stops, "stop")} · {money(st.cost, trip.currency)}</p>
              </button>
            );
          })}
        </div>
      )}

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
                  {!readOnly && flags.share && <WhatsAppCard trip={trip} />}
                  <div className="mb-6 lg:hidden">
                    <ReplanBox chips={chips} value={message} onChange={setMessage} onSend={sendMessage} busy={running}
                               readOnly={readOnly} replansLeft={trip.replans_left} error={error} reply={reply} />
                  </div>
                  {it.llm_status === "fallback" && (
                    <p className="mb-5 rounded-xl bg-haze-2 px-4 py-3 text-sm text-ink-soft">Trip data was collected, but AI explanation is temporarily unavailable.</p>
                  )}
                  <div id="plan-top" className="mb-4 flex scroll-mt-28 flex-wrap items-center justify-between gap-3">
                    {it.days.length > 1 ? (
                      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show which days">
                        {([["all", "All days"], ...it.days.map((d, n) => [d.index, `Day ${n + 1}`])] as [number | "all", string][]).map(([v, l]) => (
                          <button key={String(v)} type="button" aria-pressed={day === v} onClick={() => setDay(v)} className={day === v ? "chip-on !py-1.5" : "chip-off !py-1.5"}>{l}</button>
                        ))}
                      </div>
                    ) : <span />}
                    <div className="inline-flex rounded-xl border border-line bg-paper p-1 text-[13px] font-semibold" role="group" aria-label="How much detail">
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
                </div>
                <aside className="space-y-5">
                  <div className="hidden lg:block">
                    <ReplanBox chips={chips} value={message} onChange={setMessage} onSend={sendMessage} busy={running}
                               readOnly={readOnly} replansLeft={trip.replans_left} error={error} reply={reply} />
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
                    <SideCard icon={Share2} title="Shared links">
                      <SharedLinks tripId={trip.id} refreshKey={shareTick} />
                    </SideCard>
                  )}
                  {it.warnings.length > 0 && <PlanNotes notes={it.warnings} />}
                  {it.tips.length > 0 && (
                    <SideCard icon={Wallet} title="Good to know">
                      <ul className="list-disc space-y-1.5 pl-4">{it.tips.map((t, i) => <li key={i}>{t}</li>)}</ul>
                    </SideCard>
                  )}
                  <SideCard icon={ListChecks} title="How this plan was checked">
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
                  <SideCard icon={Database} title="Where this data comes from">
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
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
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
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 transition-colors hover:border-white/20">
      <dt className="text-xs text-white/55">{label}</dt>
      <dd className={`mt-0.5 truncate text-xl font-bold md:text-2xl ${accent}`}>{value}</dd>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="num inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.05] px-3 py-1">{children}</span>;
}

function SideCard({ icon: Icon, title, children }: { icon: any; title: string; children: React.ReactNode }) {
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

const EXAMPLES = "“add Fatehpur Sikri”, “start at 9am”, “no temples”, “budget 8000”, “one more day”";

/** Type anything: the engine reads English and Hinglish, applies what it understands and says plainly what it
 *  couldn't. A request that changes nothing gets an answer here — never a red error — and the text stays put. */
function ReplanBox({ chips, value, onChange, onSend, busy, readOnly, replansLeft, error, reply }: {
  chips: string[]; value: string; onChange: (v: string) => void; onSend: (v: string) => void; busy: boolean; readOnly: boolean;
  replansLeft: number; error?: string | null; reply?: string | null;
}) {
  const uid = useId();
  return (
    <section className="rounded-2xl border border-signal/30 bg-paper/90 p-5 shadow-[0_20px_50px_-30px_rgb(var(--signal)/0.6)] backdrop-blur" aria-labelledby={`${uid}-title`}>
      <h2 id={`${uid}-title`} className="flex items-center gap-2 text-[16px] font-bold"><Sparkles className="h-4 w-4 text-signal-deep" aria-hidden />Change the plan</h2>
      <p className="mt-1 text-[14px] text-ink-mute">
        {readOnly ? "This is a sample trip. Plan your own to change it." : "Say it in your own words, in English or Hinglish. The engine re-checks time, hours and budget."}
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {chips.map((c) => (
          <button key={c} type="button" className="chip-off !px-3 !py-1.5 text-[14px]" disabled={busy} onClick={() => onSend(c)}>{c}</button>
        ))}
      </div>
      <form className="mt-3" onSubmit={(e) => { e.preventDefault(); onSend(value); }}>
        <label htmlFor={`${uid}-msg`} className="sr-only">Describe a change</label>
        <textarea id={`${uid}-msg`} rows={2} maxLength={1000} className="field resize-none" placeholder="Try: add Fatehpur Sikri, start at 9am, no temples…"
                  value={value} onChange={(e) => onChange(e.target.value)} disabled={busy}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(value); } }} />
        {error && <p role="alert" className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-[14px] font-medium text-danger">{error}</p>}
        {!error && reply && (
          <p role="status" className="fade-up mt-2 flex gap-2 rounded-lg border border-line bg-haze px-3 py-2.5 text-[14px] leading-relaxed text-ink-soft">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-mute" aria-hidden /><span>{reply}</span>
          </p>
        )}
        {!readOnly && !error && !reply && <p className="mt-2 text-[13px] leading-snug text-ink-mute">Examples: {EXAMPLES}</p>}
        <div className="mt-2 flex items-center justify-between gap-3">
          {!readOnly && <span className="num text-[13px] text-ink-mute">{replansLeft} changes left</span>}
          <button className="btn-primary ml-auto !py-2" disabled={busy || (!readOnly && !value.trim())}>
            {readOnly ? "Plan my trip" : busy ? "Updating…" : <>Update plan<Send className="h-3.5 w-3.5" aria-hidden /></>}
          </button>
        </div>
      </form>
    </section>
  );
}

/** One quiet line beside the part of a plan that was made with less effort because we were busy. Not a warning. */
function BrownoutNote({ text, icon: Icon = Info }: { text: string; icon?: any }) {
  return <p className="flex items-center gap-1.5 text-[13px] text-ink-mute"><Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />{text}</p>;
}
