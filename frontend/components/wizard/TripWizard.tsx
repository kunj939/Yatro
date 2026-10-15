"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDownUp, ArrowLeft, ArrowRight, CalendarDays, Check, MapPin, Pencil, Sparkles, Star, TrainFront, Wallet, X } from "lucide-react";
import { LocationInput, type LocationValue } from "./LocationInput";
import { WhenPicker } from "./WhenPicker";
import type { HighlightPlace } from "@/lib/types";
import { AgeSelector, BudgetInput, FoodSelector, InterestSelector, StaySelector, TransportSelector, TravellerSelector, TripStyleSelector } from "./Selectors";
import { Modal } from "../Modal";
import { AuthForm } from "../AuthForm";
import { useAuth } from "@/lib/auth";
import { ApiError, endpoints } from "@/lib/api";
import { FOOD_LABEL, MODE_LABEL, STAY_LABEL, STYLE_LABEL, TRAVELLER_LABEL, ageSummary, budgetScale, convertApprox, money, niceDate } from "@/lib/format";
import { parseTripQuery } from "@/lib/tripQuery";

interface WizardState {
  origin: LocationValue; destination: LocationValue; start_date: string; end_date: string; start_time: string;
  end_time: string; budget: number; travellers: number; traveller_type: string; age_groups: string[]; transport_modes: string[];
  food: string; trip_styles: string[]; stay: string; notes: string; interests: string[];
  include_place_ids: string[];
}

const KEY = "tripai.wizard";

function isoDate(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function nextSaturday(): Date {
  const d = new Date();
  d.setDate(d.getDate() + 2);
  d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7));
  return d;
}
function addDays(iso: string, n: number) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + n));
}

function defaults(): WizardState {
  const d = isoDate(nextSaturday());
  return { origin: { name: "" }, destination: { name: "" }, start_date: d, end_date: d, start_time: "07:00", end_time: "22:00",
    budget: 5000, travellers: 2, traveller_type: "couple", age_groups: ["adults"], transport_modes: ["train", "cab"], food: "both",
    trip_styles: ["balanced"], stay: "no_stay", notes: "", interests: [], include_place_ids: [] };
}

function sample(): WizardState {
  const d = isoDate(nextSaturday());
  return { ...defaults(), origin: { name: "New Delhi" }, destination: { name: "Agra" }, start_date: d, end_date: addDays(d, 1),
    start_time: "06:00", end_time: "21:00", budget: 12000, food: "both", trip_styles: ["balanced", "sightseeing"],
    transport_modes: ["train", "metro"], stay: "tier2", interests: ["heritage_history", "viewpoints_sunsets"], include_place_ids: [] };
}

const STEPS = [
  { label: "Where & when", icon: MapPin }, { label: "Budget & group", icon: Wallet },
  { label: "Travel & food", icon: TrainFront }, { label: "Style & stay", icon: Sparkles },
];
const POPULAR = ["Agra", "Jaipur", "Goa", "Rishikesh", "Udaipur", "Dubai", "Bali", "Singapore"];

export function TripWizard() {
  const router = useRouter();
  const { user, ready } = useAuth();
  const [s, setS] = useState<WizardState>(defaults);
  const [step, setStep] = useState(0);
  const [authOpen, setAuthOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // A trip typed as one sentence on the home page (/plan?q=...): which sentence, and the budget in the money it was
  // typed in, so it can be converted once the destination's currency is known (₹40,000 for Dubai -> dirhams).
  const [fromQuery, setFromQuery] = useState<string | null>(null);
  const typedBudget = useRef<{ amount: number; currency: string } | null>(null);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(KEY);
      if (raw) { const v = JSON.parse(raw); setS({ ...defaults(), ...v.state }); setStep(Math.min(v.step ?? 0, STEPS.length - 1)); }
      // Destination links from the landing page and footer (/plan?to=Lisbon) prefill the first step.
      const to = new URLSearchParams(window.location.search).get("to")?.trim();
      if (to) { setS((prev) => ({ ...prev, destination: { name: to } })); setStep(0); }
      const q = new URLSearchParams(window.location.search).get("q")?.trim();
      if (q) {
        // a new sentence starts a new trip: fill what it says over fresh defaults, keep the sentence as notes
        const r = parseTripQuery(q);
        const base = defaults();
        const start = r.startDate ?? base.start_date;
        const end = r.days && r.days > 1 ? addDays(start, Math.min(r.days, 8) - 1) : start;
        const type = r.travellerType ?? base.traveller_type;
        typedBudget.current = r.budget ? { amount: r.budget, currency: r.budgetCurrency ?? "INR" } : null;
        setS({
          ...base,
          origin: r.origin ? { name: r.origin } : base.origin,
          destination: r.destination ? { name: r.destination } : base.destination,
          start_date: start, end_date: end, traveller_type: type,
          age_groups: r.ageGroups?.length ? ["adults", ...r.ageGroups] : base.age_groups,
          travellers: r.travellers ?? (type === "solo" ? 1 : type === "family" ? 4 : type === "friends" ? 3 : base.travellers),
          food: r.food ?? base.food, transport_modes: r.modes?.length ? r.modes : base.transport_modes,
          stay: end > start ? "tier2" : base.stay,
          budget: r.budget ? convertApprox(r.budget, r.budgetCurrency ?? "INR", "INR") : base.budget,
          notes: q.slice(0, 500),
        });
        setFromQuery(q);
        setStep(0);
      }
    } catch { /* ignore */ }
    setLoaded(true);
  }, []);
  useEffect(() => { if (loaded) sessionStorage.setItem(KEY, JSON.stringify({ state: s, step })); }, [s, step, loaded]);

  // Real, named places for the destination, fetched once the destination settles, so the "favourite things"
  // step can offer actual places to pin, not just categories. The same lookup tells us the destination's
  // currency, so the budget is asked for in the money the trip will actually be planned in. Best-effort: errors
  // are swallowed and the section just doesn't show, since this is an optional nicety, never a blocker.
  const [highlights, setHighlights] = useState<HighlightPlace[]>([]);
  const [highlightsFor, setHighlightsFor] = useState<string | null>(null);
  const [highlightsLoading, setHighlightsLoading] = useState(false);
  const [foundDest, setFoundDest] = useState<{ name: string; address?: string | null } | null>(null);
  const [destCurrency, setDestCurrency] = useState<string | null>(null);
  useEffect(() => {
    const name = s.destination.name.trim();
    if (name.length < 3) { setHighlights([]); setHighlightsFor(null); setFoundDest(null); setDestCurrency(null); return; }
    let cancelled = false;  // a slow answer for text the traveller has since changed must not be applied
    const t = setTimeout(async () => {
      setHighlightsLoading(true);
      try {
        const r = await endpoints.placeHighlights(name, s.destination.place_id);
        if (cancelled) return;
        const res = r.resolved;
        setHighlights(r.results);
        setHighlightsFor(res?.name ?? name);
        setFoundDest(res ? { name: res.name, address: res.address } : null);
        setDestCurrency(res?.currency ?? null);
        // Lock in the place the traveller was just shown — with its coordinates, so the trip is created in the
        // same currency the budget step shows.
        if (res) {
          setS((p) => (p.destination.name.trim() !== name ? p : {
            ...p, destination: { ...p.destination, place_id: p.destination.place_id || res.place_id,
              lat: p.destination.lat ?? res.lat ?? null, lng: p.destination.lng ?? res.lng ?? null },
          }));
        }
        // a place picked for a since-changed destination no longer makes sense to keep pinned
        const ids = new Set(r.results.map((x) => x.place_id));
        setS((p) => (p.include_place_ids.some((id) => !ids.has(id))
          ? { ...p, include_place_ids: p.include_place_ids.filter((id) => ids.has(id)) } : p));
      } catch { if (!cancelled) { setHighlights([]); setHighlightsFor(null); setFoundDest(null); setDestCurrency(null); } }
      if (!cancelled) setHighlightsLoading(false);
    }, 600);
    return () => { cancelled = true; clearTimeout(t); };
  }, [s.destination.name, s.destination.place_id]);

  // Without a resolved destination the server treats the budget as rupees and converts it, so ₹ is the honest label.
  const currency = destCurrency ?? "INR";
  // When the currency changes, an untouched preset budget is swapped for the same preset in the new currency
  // (₹5,000 makes no sense as £5,000). A budget the traveller typed themselves is left alone.
  const prevCurrency = useRef(currency);
  useEffect(() => {
    const was = prevCurrency.current;
    prevCurrency.current = currency;
    if (was === currency) return;
    const typed = typedBudget.current;
    if (typed) { setS((p) => ({ ...p, budget: convertApprox(typed.amount, typed.currency, currency) })); return; }
    setS((p) => {
      const old = budgetScale(was).presets;
      return old.includes(p.budget) || p.budget === 5000 ? { ...p, budget: budgetScale(currency).presets[old.indexOf(p.budget) >= 0 ? old.indexOf(p.budget) : 1] } : p;
    });
  }, [currency]);

  const set = <K extends keyof WizardState>(k: K, v: WizardState[K]) => setS((p) => ({ ...p, [k]: v }));
  const today = isoDate(new Date());
  const overnightReturn = s.end_date === s.start_date && s.end_time <= s.start_time;
  const effectiveEnd = overnightReturn ? addDays(s.start_date, 1) : s.end_date;
  const nights = useMemo(() => {
    const [a, b] = [new Date(s.start_date), new Date(effectiveEnd)];
    let n = Math.round((b.getTime() - a.getTime()) / 86400000);
    if (n > 0 && s.end_time < "06:00") n -= 1;
    return Math.max(0, n);
  }, [s.start_date, effectiveEnd, s.end_time]);
  const lengthLabel = nights === 0 ? "Day trip" : `${nights + 1} days · ${nights} night${nights > 1 ? "s" : ""}`;
  const minBudget = budgetScale(currency).min;

  const problems: Record<number, string | null> = {
    0: s.origin.name.trim().length < 2 ? "Enter where you're starting from." :
       s.destination.name.trim().length < 2 ? "Enter where you're going (it can be your own city)." :
       !s.start_date || s.start_date < today ? "Pick a date from today onwards." :
       s.end_date < s.start_date ? "The return date can't be before the start date." :
       nights > 30 ? "Trips can be at most 30 nights." : null,
    1: !s.budget || s.budget < minBudget ? `Enter a budget of at least ${money(minBudget, currency)}.` : null,
    2: s.transport_modes.length === 0 ? "Pick at least one way to travel." : null,
  };

  function go(n: number) { setError(null); setStep(n); window.scrollTo({ top: 0, behavior: "smooth" }); }
  function next() {
    const p = problems[step];
    if (p) { setError(p); return; }
    if (step < STEPS.length - 1) go(step + 1);
    else build();
  }
  function swapRoute() {
    setS((p) => ({ ...p, origin: p.destination, destination: p.origin }));
  }

  async function build(authed = false) {
    for (const k of [0, 1, 2]) if (problems[k]) { setStep(k); setError(problems[k]!); return; }
    if (!user && !authed) { setAuthOpen(true); return; }
    setBusy(true); setError(null);
    try {
      const trip = await endpoints.createTrip({
        origin: s.origin, destination: s.destination, start_date: s.start_date, end_date: effectiveEnd,
        start_time: s.start_time, end_time: s.end_time, budget: s.budget, budget_currency: currency, travellers: s.travellers,
        traveller_type: s.traveller_type, age_groups: s.age_groups, transport_modes: s.transport_modes, food: s.food, trip_styles: s.trip_styles,
        stay: nights > 0 ? s.stay : (s.stay === "no_stay" ? "no_stay" : s.stay), notes: s.notes || null,
        interests: s.interests.length ? s.interests : undefined,
        include_place_ids: s.include_place_ids.length ? s.include_place_ids : undefined,
      });
      sessionStorage.removeItem(KEY);
      router.push(`/trips/${trip.id}?plan=1`);
    } catch (e) {
      setBusy(false);
      if (e instanceof ApiError && Array.isArray(e.details)) setError(e.details.map((d: any) => d.message.replace(/^Value error, /, "")).join(" "));
      else setError(e instanceof Error ? e.message : "Something went wrong.");
    }
  }

  const when = `${niceDate(s.start_date)}, ${s.start_time} → ${overnightReturn || effectiveEnd !== s.start_date ? `${niceDate(effectiveEnd)} ` : ""}${s.end_time}`;
  const summary: { step: number; k: string; v: string }[] = [
    { step: 1, k: "Who", v: `${TRAVELLER_LABEL[s.traveller_type]}, ${s.travellers}` },
    { step: 1, k: "Ages", v: ageSummary(s.age_groups) || "Adults 18–59" },
    { step: 2, k: "Transport", v: s.transport_modes.map((m) => MODE_LABEL[m]).join(", ") },
    { step: 2, k: "Food", v: FOOD_LABEL[s.food] },
    { step: 3, k: "Style", v: s.trip_styles.map((x) => STYLE_LABEL[x]).join(", ") },
    { step: 3, k: "Stay", v: nights > 0 ? STAY_LABEL[s.stay] : "Day trip" },
  ];
  const last = step === STEPS.length - 1;

  return (
    <div className="container-x pb-16 pt-4 md:pt-8">
      {/* Stepper */}
      <ol className="mb-8 grid grid-cols-4 gap-2" aria-label="Progress">
        {STEPS.map(({ label, icon: Icon }, i) => {
          const done = i < step, cur = i === step;
          return (
            <li key={label} className="relative">
              <button type="button" onClick={() => i <= step && go(i)} disabled={i > step}
                      className="group flex w-full flex-col items-start gap-2 text-left disabled:cursor-default" aria-current={cur ? "step" : undefined}>
                <span className="relative h-1.5 w-full overflow-hidden rounded-full bg-line">
                  <span className={`absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-signal to-spark transition-all duration-500 ${done ? "w-full" : cur ? "w-1/2" : "w-0"}`} />
                </span>
                <span className="flex items-center gap-2">
                  <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-bold transition-all duration-300 ${
                    done ? "bg-signal text-on-signal" : cur ? "bg-ink text-paper ring-4 ring-signal/25" : "bg-haze-2 text-ink-mute"}`}>
                    {done ? <Check className="h-3.5 w-3.5" strokeWidth={3} aria-hidden /> : <Icon className="h-3.5 w-3.5" aria-hidden />}
                  </span>
                  <span className={`hidden text-[13px] font-semibold md:block ${cur ? "text-ink" : done ? "text-ink-soft group-hover:text-ink" : "text-ink-mute"}`}>{label}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <div key={step} className="fade-up min-h-[420px]">
            {step === 0 && (
              <StepShell n={1} title="Where and" accent="when?" text="Pick from the suggestions so we plan for exactly the right place.">
                {fromQuery && (
                  <div className="fade-up mb-4 flex items-start gap-3 rounded-2xl border border-signal/30 bg-signal-soft/50 px-4 py-3 text-[14px]">
                    <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-signal-deep" aria-hidden />
                    <p className="min-w-0 flex-1 text-ink-soft">
                      Got it from <span className="font-semibold text-ink">&ldquo;{fromQuery}&rdquo;</span>. Check the details and carry on.
                    </p>
                    <button type="button" onClick={() => setFromQuery(null)} aria-label="Dismiss" className="text-ink-mute hover:text-ink">
                      <X className="h-4 w-4" aria-hidden />
                    </button>
                  </div>
                )}
                <Card>
                  <div className="relative space-y-4">
                    <LocationInput id="origin" label="Starting from" value={s.origin} onChange={(v) => set("origin", v)} allowCurrent placeholder="City, area or landmark" />
                    <div className="flex items-center gap-3">
                      <span className="h-px flex-1 bg-line" />
                      <button type="button" onClick={swapRoute} aria-label="Swap start and destination"
                              className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper text-ink-soft transition-all hover:rotate-180 hover:border-signal hover:text-ink">
                        <ArrowDownUp className="h-4 w-4" aria-hidden />
                      </button>
                      <span className="h-px flex-1 bg-line" />
                    </div>
                    <LocationInput id="destination" label="Destination" value={s.destination} onChange={(v) => set("destination", v)} placeholder="Where to? (can be your own city)" />
                  </div>
                  {foundDest ? (
                    <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl bg-teal-soft/60 px-4 py-3 text-sm text-ink" aria-live="polite">
                      <Check className="h-4 w-4 text-teal" aria-hidden />
                      Planning for <span className="font-semibold">{foundDest.address ?? foundDest.name}</span>
                      {destCurrency && <span className="rounded-full bg-paper px-2 py-0.5 text-xs font-bold">Prices in {destCurrency}</span>}
                    </p>
                  ) : highlightsLoading && s.destination.name.trim().length >= 3 ? (
                    <p className="mt-4 text-sm text-ink-mute" aria-live="polite">Looking up {s.destination.name}…</p>
                  ) : null}
                  <div className="mt-5">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-ink-mute">Popular right now</p>
                    <div className="flex flex-wrap gap-2">
                      {POPULAR.map((c) => (
                        <button key={c} type="button" onClick={() => set("destination", { name: c })}
                                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[14px] font-medium transition-all hover:-translate-y-px ${
                                  s.destination.name === c ? "border-signal bg-signal-soft text-ink" : "border-line text-ink-soft hover:border-ink/30 hover:text-ink"}`}>
                          <MapPin className="h-3 w-3" aria-hidden />{c}
                        </button>
                      ))}
                    </div>
                  </div>
                  <button type="button" onClick={() => setS(sample())} className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-signal-deep hover:text-ink">
                    <Sparkles className="h-4 w-4" aria-hidden />Try a sample trip (Delhi to Agra)
                  </button>
                </Card>

                <SubHeading aside={<span className="inline-flex items-center gap-1.5 rounded-full bg-signal-soft px-3 py-1 text-xs font-bold text-ink"><CalendarDays className="h-3.5 w-3.5" aria-hidden />{lengthLabel}</span>}>When</SubHeading>
                <Card>
                  <WhenPicker value={{ start_date: s.start_date, end_date: s.end_date, start_time: s.start_time, end_time: s.end_time }}
                              today={today} onChange={(patch) => setS((p) => ({ ...p, ...patch }))} />
                  {overnightReturn && <p className="mt-4 rounded-xl bg-signal-soft px-4 py-3 text-[16px]">Back by {s.end_time} means the early hours of {niceDate(effectiveEnd)}. That works for a late train home.</p>}
                </Card>
              </StepShell>
            )}
            {step === 1 && (
              <StepShell n={2} title="Budget and" accent="group" text="Everything gets planned inside this budget, with a small cushion.">
                <Card><BudgetInput value={s.budget} onChange={(v) => { typedBudget.current = null; set("budget", v); }} travellers={s.travellers} days={nights + 1} currency={currency} /></Card>
                <SubHeading>Who&apos;s travelling?</SubHeading>
                <TravellerSelector type={s.traveller_type} count={s.travellers}
                                   onChange={(t, c) => setS((p) => ({ ...p, traveller_type: t, travellers: c,
                                     age_groups: t === "solo" && p.age_groups.length > 1 ? [p.age_groups[0]] : p.age_groups }))} />
                <SubHeading aside={<span className="rounded-full bg-signal-soft px-3 py-1 text-xs font-bold text-ink">So we pick the right places</span>}>How old is everyone?</SubHeading>
                <AgeSelector value={s.age_groups} solo={s.traveller_type === "solo"}
                             onChange={(v) => setS((p) => ({ ...p, age_groups: v,
                               interests: v.includes("kids") || v.includes("teens") ? p.interests.filter((i) => i !== "nightlife") : p.interests }))} />
              </StepShell>
            )}
            {step === 2 && (
              <StepShell n={3} title="Getting there," accent="and food" text="Pick every way of travelling you're okay with.">
                <TransportSelector value={s.transport_modes} onChange={(v) => set("transport_modes", v)} />
                <SubHeading>Food preference</SubHeading>
                <FoodSelector value={s.food} onChange={(v) => set("food", v)} />
              </StepShell>
            )}
            {step === 3 && (
              <StepShell n={4} title="Trip style and" accent="stay">
                <TripStyleSelector value={s.trip_styles} onChange={(v) => set("trip_styles", v)} />
                <SubHeading>Where will you stay?</SubHeading>
                <StaySelector value={s.stay} onChange={(v) => set("stay", v)} overnight={nights > 0} currency={currency} />
                <SubHeading>Favourite things to see</SubHeading>
                <InterestSelector value={s.interests} onChange={(v) => set("interests", v)}
                                  noNightlife={s.age_groups.includes("kids") || s.age_groups.includes("teens")} />
                {(highlightsLoading || highlights.length > 0) && (
                  <div className="mt-6 rounded-2xl border border-line bg-paper/70 p-5">
                    <p className="mb-3 text-sm font-semibold">
                      {highlightsLoading && !highlights.length ? `Finding well-known places in ${s.destination.name}…`
                        : `Must-sees in ${highlightsFor ?? s.destination.name}`}
                      <span className="ml-2 font-normal text-ink-mute">optional, tap to pin</span>
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Specific places to include">
                      {highlights.map((h) => {
                        const on = s.include_place_ids.includes(h.place_id);
                        return (
                          <button key={h.place_id} type="button" aria-pressed={on} title={h.teaser ?? undefined}
                                  onClick={() => set("include_place_ids", on
                                    ? s.include_place_ids.filter((id) => id !== h.place_id)
                                    : [...s.include_place_ids, h.place_id])}
                                  className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-all duration-200 ${
                                    on ? "border-signal bg-signal-soft/60" : "border-line hover:-translate-y-px hover:border-ink/25"}`}>
                            <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full transition-colors ${on ? "bg-signal text-on-signal" : "bg-haze text-ink-mute"}`}>
                              {on ? <Check className="h-3.5 w-3.5" strokeWidth={3} aria-hidden /> : <MapPin className="h-3.5 w-3.5" aria-hidden />}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[14px] font-semibold">{h.name}</span>
                              {h.teaser && <span className="block truncate text-xs text-ink-mute">{h.teaser}</span>}
                            </span>
                            {h.rating && (
                              <span className="num inline-flex shrink-0 items-center gap-0.5 text-xs font-semibold text-ink-soft">
                                <Star className="h-3 w-3 fill-current text-signal" aria-hidden />{h.rating.toFixed(1)}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div className="mt-8">
                  <label htmlFor="notes" className="mb-1.5 block text-sm font-semibold">Anything else? <span className="font-normal text-ink-mute">(optional)</span></label>
                  <textarea id="notes" rows={3} maxLength={500} className="field resize-none" placeholder="e.g. we love castles and street food, not too much walking"
                            value={s.notes} onChange={(e) => set("notes", e.target.value)} />
                  <p className="num mt-1 text-right text-xs text-ink-mute">{s.notes.length}/500</p>
                </div>
              </StepShell>
            )}
          </div>

          {error && <p className="fade-up mt-5 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-[16px] font-medium text-danger" role="alert">{error}</p>}
          <div className="sticky bottom-0 z-10 -mx-5 mt-8 flex items-center justify-between gap-3 border-t border-line bg-haze/90 px-5 py-3 backdrop-blur-xl [padding-bottom:max(0.75rem,env(safe-area-inset-bottom))] sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:p-0 sm:backdrop-blur-none">
            <button type="button" onClick={() => go(Math.max(0, step - 1))} className={`btn-ghost ${step === 0 ? "invisible" : ""}`}>
              <ArrowLeft className="h-4 w-4" aria-hidden />Back
            </button>
            <span className="num hidden text-sm text-ink-mute sm:block">Step {step + 1} of {STEPS.length}</span>
            <button type="button" onClick={next} className={`btn-primary shine group ${last ? "!px-8 !py-3 !text-base" : "!px-7"}`} disabled={busy || !ready}>
              {last ? (busy ? "Building…" : <><Sparkles className="h-4 w-4" aria-hidden />Build my plan</>) : "Continue"}
              {!last && <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" aria-hidden />}
            </button>
          </div>
        </div>

        {/* Live summary: a ticket that fills in as you go */}
        <aside className={`h-fit overflow-hidden rounded-3xl border border-white/10 bg-[#0f1219] text-white shadow-[0_30px_80px_-40px_rgb(0_0_0/0.8)] lg:sticky lg:top-28 ${last ? "" : "hidden lg:block"}`}
               aria-label="Your trip so far">
          <div className="relative p-6">
            <div aria-hidden className="absolute -right-16 -top-16 h-44 w-44 rounded-full bg-[rgb(190_255_74/0.16)] blur-3xl" />
            <div className="relative flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-white/55">{last ? "Check your trip" : "Your trip so far"}</p>
              <span className="num text-xs font-semibold text-[rgb(190_255_74)]">{Math.round(((step + 1) / STEPS.length) * 100)}%</span>
            </div>
            {/* Route */}
            <div className="relative mt-5 flex gap-3">
              <div className="flex flex-col items-center pt-1.5">
                <span className="h-2.5 w-2.5 rounded-full border-2 border-white/60" />
                <span className="my-1 w-px flex-1 border-l border-dashed border-white/30" />
                <MapPin className="h-4 w-4 text-[rgb(190_255_74)]" aria-hidden />
              </div>
              <div className="min-w-0 flex-1 space-y-4">
                <div><p className="text-xs text-white/50">From</p><p className="truncate font-semibold">{s.origin.name || "—"}</p></div>
                <div><p className="text-xs text-white/50">To</p><p className="truncate text-lg font-bold">{s.destination.name || "Somewhere new"}</p></div>
              </div>
              {step > 0 && <EditBtn onClick={() => go(0)} />}
            </div>
            <p className="num relative mt-4 flex flex-wrap items-center gap-2 text-[14px] text-white/75">
              <CalendarDays className="h-4 w-4 text-white/50" aria-hidden />{when}
              <span className="rounded-full bg-white/10 px-2 py-0.5 text-[12px] font-semibold">{lengthLabel}</span>
            </p>
          </div>

          {/* Perforation */}
          <div className="relative h-0 border-t border-dashed border-white/15">
            <span className="absolute -left-3 -top-3 h-6 w-6 rounded-full bg-haze" />
            <span className="absolute -right-3 -top-3 h-6 w-6 rounded-full bg-haze" />
          </div>

          <div className="p-6">
            <div className={`flex items-end justify-between gap-3 transition-opacity ${step < 1 ? "opacity-40" : ""}`}>
              <div>
                <p className="text-xs text-white/50">Budget</p>
                <p className="num text-3xl font-extrabold tracking-tight">{money(s.budget, currency)}</p>
                {s.travellers > 1 && <p className="num text-xs text-white/55">{money(Math.round(s.budget / s.travellers), currency)} per person</p>}
              </div>
              {step > 1 && <EditBtn onClick={() => go(1)} />}
            </div>
            <dl className="mt-5 space-y-3 text-[14px]">
              {summary.map((r) => (
                <div key={r.k} className={`flex items-start justify-between gap-3 transition-opacity ${r.step > step ? "opacity-35" : ""}`}>
                  <div className="min-w-0">
                    <dt className="text-xs text-white/50">{r.k}</dt>
                    <dd className="truncate">{r.v}</dd>
                  </div>
                  {r.step < step && <EditBtn onClick={() => go(r.step)} />}
                </div>
              ))}
              {s.include_place_ids.length > 0 && (
                <div><dt className="text-xs text-white/50">Pinned places</dt><dd>{s.include_place_ids.length} must-see{s.include_place_ids.length > 1 ? "s" : ""}</dd></div>
              )}
            </dl>
          </div>
        </aside>
      </div>

      <Modal open={authOpen} onClose={() => setAuthOpen(false)} title="Save your trip">
        <AuthForm note="Make a free account to build this plan. We've saved your answers." onDone={() => { setAuthOpen(false); setTimeout(() => build(true), 50); }} />
      </Modal>
    </div>
  );
}

function EditBtn({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label="Edit"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-white/50 transition-colors hover:bg-white/10 hover:text-[rgb(190_255_74)]">
      <Pencil className="h-3.5 w-3.5" aria-hidden />
    </button>
  );
}

function StepShell({ n, title, accent, text, children }: { n: number; title: string; accent?: string; text?: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={`step-${n}`}>
      <p className="text-sm font-semibold text-signal-deep">Step {n} of {STEPS.length}</p>
      <h1 id={`step-${n}`} className="mt-1 text-[28px] font-extrabold leading-tight tracking-tight md:text-[40px]">{title}{accent && <> <span className="accent highlight">{accent}</span></>}</h1>
      {text && <p className="mt-2 text-ink-soft">{text}</p>}
      <div className="mt-7">{children}</div>
    </section>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="card p-5 md:p-6">{children}</div>;
}

function SubHeading({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return <h2 className="mb-4 mt-10 flex items-center justify-between gap-3 text-lg font-bold">{children}{aside}</h2>;
}
