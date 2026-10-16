"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowRight, Check, Leaf, Lock, MapPin, PartyPopper, Pencil, Users, Utensils } from "lucide-react";
import { Logo } from "@/components/Logo";
import { DateHeat, FoodMix, VoteBars } from "@/components/group/GroupResults";
import { ApiError, endpoints } from "@/lib/api";
import type { GroupPublicView } from "@/lib/types";
import { currencySymbol, niceDate } from "@/lib/format";
import { brand } from "@/lib/brand";

/** A random key this browser keeps, so one phone has one answer it can edit. Only a hash of it is stored (ADR 0022). */
function deviceKey(): string {
  try {
    const k = localStorage.getItem("yatro.device");
    if (k && /^[A-Za-z0-9_-]{16,64}$/.test(k)) return k;
    const b = new Uint8Array(18); crypto.getRandomValues(b);
    const v = btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    localStorage.setItem("yatro.device", v);
    return v;
  } catch { return `tmp${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`; }
}
const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function JoinGroup() {
  const { token } = useParams<{ token: string }>();
  const [device, setDevice] = useState("");
  const [g, setG] = useState<GroupPublicView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [food, setFood] = useState("both");
  const [dates, setDates] = useState<string[]>([]);
  const [votes, setVotes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [justIn, setJustIn] = useState(false);

  const load = useCallback(async (key: string) => {
    try {
      const v = await endpoints.joinGroup(token, key);
      setG(v);
      if (v.mine) { setName(v.mine.name); setBudget(v.mine.budget ? String(v.mine.budget) : ""); setFood(v.mine.food); setDates(v.mine.dates); setVotes(v.mine.votes); }
    } catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't open this group plan."); }
  }, [token]);
  useEffect(() => { const k = deviceKey(); setDevice(k); load(k); }, [load]);

  const span = useMemo(() => {
    if (!g) return [] as string[];
    const out: string[] = [];
    for (let d = new Date(`${g.window_start}T00:00:00Z`); d <= new Date(`${g.window_end}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) out.push(d.toISOString().slice(0, 10));
    return out;
  }, [g]);
  const lead = span.length ? (new Date(`${span[0]}T00:00:00Z`).getUTCDay() + 6) % 7 : 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!g) return;
    if (!name.trim()) { setError("Add your name so the group knows who's in."); return; }
    setBusy(true); setError(null);
    try {
      const v = await endpoints.replyGroup(token, { device, name: name.trim(), budget: budget ? Number(budget) : null, food, dates, votes });
      setG(v); setEditing(false); setJustIn(true);
      scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't save your answer."); }
    finally { setBusy(false); }
  }

  if (error && !g) {
    return <Shell><div className="card p-8 text-center"><p className="text-lg font-bold">This link doesn&apos;t work</p><p className="mt-2 text-ink-soft">{error}</p></div></Shell>;
  }
  if (!g) return <Shell><div className="h-[480px] animate-pulse rounded-3xl border border-line bg-paper/60" aria-busy="true" /></Shell>;
  const open = g.status === "open";
  const showForm = open && (!g.mine || editing);
  const others = g.names.filter((n) => n !== g.mine?.name);

  return (
    <Shell>
      {/* who's asking, and for what */}
      <section className="relative overflow-hidden rounded-3xl border border-line bg-paper p-6 shadow-lift md:p-8">
        <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-signal/14 blur-3xl" />
        <p className="relative inline-flex items-center gap-1.5 rounded-full border border-signal/25 bg-signal-soft/70 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.14em] text-signal-deep">
          <Users className="h-3.5 w-3.5" aria-hidden />Group trip
        </p>
        <h1 className="headline-font relative mt-3 text-[28px] font-extrabold leading-tight text-ink md:text-[34px]">
          {g.organiser} is planning a {g.days}-day trip. <span className="accent highlight">You in?</span>
        </h1>
        <p className="relative mt-2 text-[15px] text-ink-soft">{g.title} · from {g.origin} · sometime {niceDate(g.window_start)} – {niceDate(g.window_end)}</p>
        {g.note && <p className="relative mt-3 rounded-2xl bg-haze/70 px-4 py-3 text-[15px] italic text-ink-soft">&ldquo;{g.note}&rdquo; — {g.organiser}</p>}
        {g.names.length > 0 && (
          <p className="relative mt-4 flex items-center gap-2 text-[14px] text-ink-soft">
            <span className="flex -space-x-2" aria-hidden>
              {g.names.slice(0, 5).map((n, i) => (
                <span key={i} className={`grid h-8 w-8 place-items-center rounded-full text-[12px] font-bold ring-2 ring-paper ${["bg-[#659600] text-white", "bg-[#2a78d6] text-white", "bg-[#eb6834] text-white", "bg-[#6250d6] text-white", "bg-[#eda100] text-[#101311]"][i % 5]}`}>{n.slice(0, 1).toUpperCase()}</span>
              ))}
            </span>
            <span><span className="font-semibold text-ink">{g.names.length}</span> {g.names.length === 1 ? "person has" : "people have"} answered</span>
          </p>
        )}
      </section>

      {g.status === "made" && (
        <section className="mt-5 flex items-center gap-3 rounded-3xl border border-signal/30 bg-signal-soft/60 p-5">
          <PartyPopper className="h-6 w-6 shrink-0 text-signal-deep" aria-hidden />
          <p className="text-[15px] text-ink"><span className="font-bold">The plan is made.</span> {g.organiser} will share the day-by-day plan with the group.</p>
        </section>
      )}
      {g.status === "expired" && <p className="mt-5 rounded-2xl bg-haze px-4 py-3 text-[15px] text-ink-soft">This link isn&apos;t taking answers any more.</p>}

      {showForm ? (
        <form onSubmit={submit} className="mt-5 space-y-5">
          {g.options.length > 1 && (
            <section className="card p-5 md:p-6">
              <h2 className="text-lg font-bold">Where would you go?</h2>
              <p className="text-[13px] text-ink-mute">Pick one or more</p>
              <div className="mt-4 grid gap-2.5 sm:grid-cols-2">
                {g.options.map((o) => {
                  const on = votes.includes(o.id);
                  return (
                    <button key={o.id} type="button" onClick={() => setVotes((v) => (on ? v.filter((x) => x !== o.id) : [...v, o.id]))} aria-pressed={on}
                            className={`flex items-center gap-3 rounded-2xl border p-4 text-left transition-all ${on ? "border-signal bg-signal-soft ring-2 ring-signal/20" : "border-line bg-paper hover:border-ink/30"}`}>
                      <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${on ? "bg-signal text-white" : "bg-haze text-ink-soft"}`}>{on ? <Check className="h-5 w-5" aria-hidden /> : <MapPin className="h-5 w-5" aria-hidden />}</span>
                      <span className="min-w-0 flex-1 truncate text-[16px] font-bold text-ink">{o.name}</span>
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          <section className="card p-5 md:p-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <h2 className="text-lg font-bold">Which days are you free?</h2>
                <p className="text-[13px] text-ink-mute">Tap every day you could travel. The trip is {g.days} {g.days === 1 ? "day" : "days"}.</p>
              </div>
              <button type="button" onClick={() => setDates(dates.length === span.length ? [] : span)} className="text-[13px] font-semibold text-signal-deep hover:underline">
                {dates.length === span.length ? "Clear" : "I'm free all of them"}
              </button>
            </div>
            <div className="mt-4 grid grid-cols-7 gap-1.5 text-center">
              {WEEK.map((w) => <span key={w} className="text-[11px] font-semibold uppercase tracking-wide text-ink-mute">{w}</span>)}
              {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} />)}
              {span.map((d) => {
                const on = dates.includes(d);
                const dt = new Date(`${d}T00:00:00Z`);
                return (
                  <button key={d} type="button" onClick={() => setDates((x) => (on ? x.filter((y) => y !== d) : [...x, d]))} aria-pressed={on}
                          aria-label={niceDate(d, { weekday: "long", day: "numeric", month: "long" })}
                          className={`num flex aspect-square flex-col items-center justify-center rounded-xl border text-[14px] font-bold transition-all ${on ? "border-signal bg-signal text-white shadow-[0_6px_14px_-8px_rgb(var(--signal)/0.9)]" : "border-line bg-paper text-ink hover:border-ink/30"}`}>
                    {dt.getUTCDate()}
                    {dt.getUTCDate() === 1 || d === span[0] ? <span className={`text-[9px] font-semibold ${on ? "text-white/80" : "text-ink-mute"}`}>{dt.toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" })}</span> : null}
                  </button>
                );
              })}
            </div>
          </section>

          <section className="card space-y-4 p-5 md:p-6">
            <h2 className="text-lg font-bold">About you</h2>
            <label className="block text-sm font-semibold">Your name
              <input className="field mt-1.5" maxLength={60} placeholder="What the group calls you" value={name} onChange={(e) => setName(e.target.value)} autoComplete="given-name" />
            </label>
            <label className="block text-sm font-semibold">Your budget for the trip <span className="font-normal text-ink-mute">(optional)</span>
              <span className="relative mt-1.5 block">
                <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 font-semibold text-ink-mute">{currencySymbol(g.currency)}</span>
                <input className="field num !pl-9" inputMode="numeric" placeholder={g.budget_hint ? String(g.budget_hint) : "8000"} value={budget} onChange={(e) => setBudget(e.target.value.replace(/\D/g, ""))} />
              </span>
              <span className="mt-1.5 flex items-center gap-1.5 text-[12.5px] font-normal text-ink-mute"><Lock className="h-3 w-3" aria-hidden />Only {g.organiser} sees this. The plan fits the lowest budget, so nobody is priced out.</span>
            </label>
            <fieldset>
              <legend className="mb-2 text-sm font-semibold">Food</legend>
              <div className="grid grid-cols-3 gap-1 rounded-xl bg-haze p-1">
                {([["veg", "Veg", Leaf], ["non_veg", "Non-veg", Utensils], ["both", "Anything", Check]] as const).map(([k, l, I]) => (
                  <button type="button" key={k} onClick={() => setFood(k)} aria-pressed={food === k}
                          className={`inline-flex items-center justify-center gap-1.5 rounded-lg py-2 text-[13px] font-semibold transition-all ${food === k ? "bg-paper text-ink shadow-sm" : "text-ink-mute hover:text-ink"}`}>
                    <I className="h-3.5 w-3.5" aria-hidden />{l}
                  </button>
                ))}
              </div>
            </fieldset>
          </section>

          {error && <p role="alert" className="rounded-xl bg-danger-soft px-4 py-3 text-[15px] font-medium text-danger">{error}</p>}
          <button className="btn-primary shine w-full !rounded-full !py-3.5 !text-[16px]" disabled={busy}>
            {busy ? "Saving…" : g.mine ? "Save my answer" : <>Count me in<ArrowRight className="h-4 w-4" aria-hidden /></>}
          </button>
          <p className="text-center text-[12.5px] text-ink-mute">No sign-up. No phone number. You can change your answer until the plan is made.</p>
        </form>
      ) : (
        <>
          {g.mine && (
            <section className={`mt-5 flex flex-wrap items-center justify-between gap-3 rounded-3xl border p-5 ${justIn ? "fade-up border-signal/40 bg-signal-soft/60 shadow-glow" : "border-line bg-paper"}`}>
              <p className="flex items-center gap-2.5 text-[16px] font-bold text-ink"><span className="grid h-9 w-9 place-items-center rounded-full bg-signal text-white"><Check className="h-5 w-5" aria-hidden /></span>
                {justIn ? `You're in, ${g.mine.name}!` : `Your answer is in, ${g.mine.name}.`}</p>
              {open && <button type="button" onClick={() => setEditing(true)} className="btn-ghost !rounded-full !px-4 !py-2 !text-[13px]"><Pencil className="h-3.5 w-3.5" aria-hidden />Change my answer</button>}
            </section>
          )}
          <section className="card mt-5 p-5 md:p-6">
            <h2 className="text-lg font-bold">How the group is leaning</h2>
            <div className="mt-4"><VoteBars g={g} mine={g.mine?.votes ?? []} /></div>
            <h3 className="mt-6 font-bold">Best dates so far</h3>
            <div className="mt-3"><DateHeat g={g} /></div>
            <div className="mt-5 border-t border-line pt-4"><FoodMix g={g} /></div>
            {others.length > 0 && <p className="mt-3 text-[13px] text-ink-mute">In so far: {g.names.join(", ")}</p>}
          </section>
        </>
      )}

      <section className="mt-8 rounded-3xl border border-line bg-paper/80 p-5 text-center">
        <p className="font-bold text-ink">Planning a trip of your own?</p>
        <p className="mt-1 text-[14px] text-ink-soft">{brand.name} plans it day by day with real trains, timings and prices that fit your budget.</p>
        <Link href="/" className="btn-ghost mt-4 !rounded-full">See how it works<ArrowRight className="h-4 w-4" aria-hidden /></Link>
      </section>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen">
      <header className="container-x flex items-center justify-between py-5">
        <Link href="/" className="group" aria-label={`${brand.name} home`}><Logo /></Link>
      </header>
      <div className="container-x max-w-2xl pb-16">{children}</div>
    </main>
  );
}
