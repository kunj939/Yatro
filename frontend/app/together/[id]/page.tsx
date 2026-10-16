"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Check, Copy, Leaf, MessageCircle, Share2, Sparkles, Trash2, Users, Wallet } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { RequireAuth } from "@/components/AuthPage";
import { DateHeat, FoodMix, VoteBars } from "@/components/group/GroupResults";
import { ApiError, endpoints } from "@/lib/api";
import type { GroupBundle } from "@/lib/types";
import { FOOD_LABEL, money, niceDate } from "@/lib/format";

const TONES = ["bg-[#659600] text-white", "bg-[#2a78d6] text-white", "bg-[#eb6834] text-white", "bg-[#6250d6] text-white", "bg-[#eda100] text-[#101311]", "bg-ink text-paper"];

function Organiser() {
  const { id } = useParams<{ id: string }>();
  const fresh = useSearchParams().get("new") === "1";
  const router = useRouter();
  const [b, setB] = useState<GroupBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [option, setOption] = useState<string | null>(null);
  const [start, setStart] = useState<string | null>(null);
  const [making, setMaking] = useState(false);

  const load = useCallback(async () => {
    try { setB(await endpoints.group(id)); setError(null); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't load this group plan."); }
  }, [id]);
  useEffect(() => { load(); const t = setInterval(load, 20_000); return () => clearInterval(t); }, [load]);  // replies arrive while you watch

  if (error && !b) return <p role="alert" className="container-x mt-10 rounded-xl bg-danger-soft px-4 py-3 text-danger">{error}</p>;
  if (!b) return <div className="container-x mt-10 h-96 animate-pulse rounded-3xl border border-line bg-paper/60" aria-busy="true" />;
  const g = b.group;
  const lead = g.options.reduce((a, o) => (o.votes > a.votes ? o : a), g.options[0]);
  const chosen = option ?? lead.id;
  const open = g.status === "open";

  async function copy() {
    try { await navigator.clipboard.writeText(g.url); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch { /* blocked */ }
  }
  async function share() {
    if (navigator.share) { try { await navigator.share({ title: g.title, text: b!.text }); return; } catch { /* cancelled */ } }
    copy();
  }
  async function make() {
    setMaking(true); setError(null);
    try {
      const out = await endpoints.makeGroupTrip(g.id, { option_id: chosen, start_date: start ?? g.best_dates[0]?.start ?? null });
      router.push(`/trips/${out.trip_id}?plan=1`);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't make the plan."); setMaking(false); }
  }
  async function remove(rid: string) {
    try { setB(await endpoints.removeGroupReply(g.id, rid)); } catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't remove that reply."); }
  }

  return (
    <div className="container-x pb-16 pt-4 md:pt-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <span className="eyebrow"><Users className="h-3.5 w-3.5 text-signal" aria-hidden />Plan together</span>
          <h1 className="mt-4 text-title font-bold">{g.title}</h1>
          <p className="num mt-2 text-ink-soft">From {g.origin} · {g.days} {g.days === 1 ? "day" : "days"} · sometime {niceDate(g.window_start)} – {niceDate(g.window_end)}</p>
        </div>
        <span className={`rounded-full px-3 py-1 text-sm font-semibold ${open ? "bg-signal-soft text-signal-deep" : g.status === "made" ? "bg-teal-soft text-teal-deep" : "bg-haze-2 text-ink-mute"}`}>
          {open ? `Collecting answers · ${g.replies_count} in` : g.status === "made" ? "Plan made" : "Link expired"}
        </span>
      </div>

      {/* step 1: the link, front and centre */}
      {open && (
        <section className={`relative mt-6 overflow-hidden rounded-3xl border bg-paper p-5 md:p-6 ${fresh ? "border-signal/40 shadow-glow" : "border-line shadow-lift"}`}>
          <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-signal/12 blur-3xl" />
          <div className="relative flex flex-wrap items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="font-bold">{fresh ? "Your link is ready. Send it to the group." : "Share the link"}</p>
              <p className="mt-1 truncate text-[13px] text-ink-mute">{g.url}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <a href={b.wa_url} target="_blank" rel="noopener noreferrer" className="btn-primary shine !rounded-full"><MessageCircle className="h-4 w-4" aria-hidden />Send on WhatsApp</a>
              <button type="button" onClick={share} className="btn-ghost !rounded-full"><Share2 className="h-4 w-4" aria-hidden />Share</button>
              <button type="button" onClick={copy} className="btn-ghost !rounded-full">{copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}{copied ? "Copied" : "Copy"}</button>
            </div>
          </div>
          <p className="relative mt-3 text-[13px] text-ink-mute">Fill in your own answer on the link too. It stays open until {niceDate(g.expires_at.slice(0, 10))} or until you make the plan. Up to {g.max_replies} people.</p>
        </section>
      )}

      <div className="mt-5 grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <section className="card p-5 md:p-6">
            <h2 className="font-bold">Where</h2>
            <p className="mb-4 text-[13px] text-ink-mute">{open ? "Tap a place to plan it even if it isn't leading." : "The votes"}</p>
            <VoteBars g={g} />
            {open && g.options.length > 1 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {g.options.map((o) => (
                  <button key={o.id} type="button" onClick={() => setOption(o.id)} aria-pressed={chosen === o.id} className={chosen === o.id ? "chip-on" : "chip-off"}>
                    Plan {o.name}
                  </button>
                ))}
              </div>
            )}
          </section>
          <section className="card p-5 md:p-6">
            <h2 className="font-bold">When</h2>
            <p className="mb-4 text-[13px] text-ink-mute">How many people can make each day. The best {g.days}-day stretches are underneath.</p>
            <DateHeat g={g} pick={start} onPick={open ? setStart : undefined} />
          </section>
          <section className="card overflow-hidden">
            <div className="flex items-baseline justify-between px-5 pt-5 md:px-6">
              <h2 className="font-bold">Who&apos;s in</h2>
              <span className="num text-[13px] text-ink-mute">{g.replies_count} of {g.max_replies}</span>
            </div>
            {g.replies.length === 0 ? (
              <p className="px-5 pb-5 pt-2 text-[15px] text-ink-mute md:px-6">No answers yet. Send the link and watch them come in.</p>
            ) : (
              <ul className="mt-3 divide-y divide-line border-t border-line">
                {g.replies.map((r, n) => (
                  <li key={r.id} className="group/r flex items-center gap-3 px-5 py-3 md:px-6">
                    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full text-[14px] font-bold ${TONES[n % TONES.length]}`} aria-hidden>{r.name.slice(0, 1).toUpperCase()}</span>
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 truncate font-semibold">{r.name}{r.is_owner && <span className="rounded-full bg-haze-2 px-2 py-px text-[11px] font-semibold text-ink-soft">You</span>}</p>
                      <p className="num flex flex-wrap items-center gap-x-2 text-[13px] text-ink-mute">
                        <span>{r.budget ? money(r.budget, g.currency) : "No budget given"}</span>
                        <span className="inline-flex items-center gap-1">{r.food === "veg" && <Leaf className="h-3 w-3 text-signal" aria-hidden />}{FOOD_LABEL[r.food] ?? r.food}</span>
                        <span>{r.dates.length} {r.dates.length === 1 ? "day" : "days"} free</span>
                      </p>
                    </div>
                    {open && !r.is_owner && (
                      <button type="button" onClick={() => remove(r.id)} aria-label={`Remove ${r.name}`} className="rounded-full p-2 text-ink-mute opacity-60 hover:bg-haze hover:text-danger group-hover/r:opacity-100"><Trash2 className="h-4 w-4" /></button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* step 2: everything the plan will use, and the button */}
        <aside className="space-y-5 lg:sticky lg:top-24">
          <section className="card relative overflow-hidden p-5 md:p-6">
            <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 h-48 w-48 rounded-full bg-signal/12 blur-3xl" />
            <p className="relative inline-flex items-center gap-1.5 text-[12px] font-bold uppercase tracking-[0.14em] text-signal-deep"><Sparkles className="h-3.5 w-3.5" aria-hidden />The plan will use</p>
            <dl className="relative mt-4 space-y-3 text-[14px]">
              <div className="flex justify-between gap-3"><dt className="text-ink-mute">Place</dt><dd className="font-semibold text-ink">{g.options.find((o) => o.id === chosen)?.name}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-ink-mute">Dates</dt><dd className="num text-right font-semibold text-ink">
                {(() => { const s = start ?? g.best_dates[0]?.start; const e = g.best_dates.find((x) => x.start === s)?.end; return s ? `${niceDate(s)}${e && e !== s ? ` – ${niceDate(e)}` : ""}` : "First days of the window"; })()}
              </dd></div>
              <div className="flex justify-between gap-3"><dt className="text-ink-mute">People</dt><dd className="num font-semibold text-ink">{g.budget.people}</dd></div>
              <div className="flex justify-between gap-3"><dt className="flex items-center gap-1.5 text-ink-mute"><Wallet className="h-3.5 w-3.5" aria-hidden />Budget</dt>
                <dd className="num text-right">
                  <span className="block font-extrabold text-ink">{g.budget.per_person ? `${money(g.budget.per_person, g.currency)} each` : "Not given yet"}</span>
                  {g.budget.total && <span className="block text-[12px] text-ink-mute">{money(g.budget.total, g.currency)} total · {g.budget.from === "lowest" ? "lowest anyone gave" : "your suggestion"}</span>}
                </dd>
              </div>
            </dl>
            <div className="relative mt-4 border-t border-line pt-4"><FoodMix g={g} /></div>
            {error && <p role="alert" className="relative mt-3 text-[13px] font-medium text-danger">{error}</p>}
            {g.status === "made" && g.trip_id ? (
              <Link href={`/trips/${g.trip_id}`} className="btn-primary relative mt-5 w-full !rounded-full !py-3">Open the trip<ArrowRight className="h-4 w-4" aria-hidden /></Link>
            ) : (
              <button type="button" onClick={make} disabled={making || !open || !g.replies_count}
                      className="btn-primary shine relative mt-5 w-full !rounded-full !py-3 !text-[15px]">
                {making ? "Making the plan…" : <>Make the plan<ArrowRight className="h-4 w-4" aria-hidden /></>}
              </button>
            )}
            <p className="relative mt-2 text-center text-[12px] text-ink-mute">
              {g.replies_count ? "Everyone who answered joins the trip with their budget and food, ready for Settle Up. Uses one plan." : "Waiting for the first answer."}
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

export default function OrganiserPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <RequireAuth><Suspense><Organiser /></Suspense></RequireAuth>
    </main>
  );
}
