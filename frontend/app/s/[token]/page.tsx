"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { CalendarDays, ExternalLink, Users, Wallet } from "lucide-react";
import { Logo } from "@/components/Logo";
import { Timeline } from "@/components/trip/Timeline";
import { ApiError, endpoints } from "@/lib/api";
import { money, niceDate, plural } from "@/lib/format";
import type { Itinerary, ItineraryItem, SharedItem, SharedTrip } from "@/lib/types";

/** The share view has only what it is allowed to show; the timeline wants a full item, so the rest is left empty. */
function toItem(i: SharedItem): ItineraryItem {
  return { ...i, position: 0, place_id: null, polyline: null, leg_state: null, recommended_minutes: null, hours_state: null,
    hours_note: null, sources: [], reasons: [], alternatives: [] };
}

export default function SharedTripPage() {
  const { token } = useParams<{ token: string }>();
  const [view, setView] = useState<SharedTrip | null>(null);
  const [gone, setGone] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    endpoints.shared(token).then((v) => live && setView(v))
      .catch((e) => live && setGone(e instanceof ApiError && e.status === 404 ? e.message : "This plan couldn't be loaded. Try again in a moment."));
    return () => { live = false; };
  }, [token]);

  const itinerary = useMemo(() => view && ({
    days: view.itinerary.days.map((d) => ({ ...d, items: d.items.map(toItem) })),
  } as unknown as Itinerary), [view]);

  return (
    <main className="min-h-screen">
      <header className="container-x flex items-center justify-between py-5">
        <Link href="/" className="group" aria-label="Yaitro home"><Logo /></Link>
        <Link href="/plan" className="btn-primary !py-2">Plan your own</Link>
      </header>

      {gone && (
        <div className="container-x py-24 text-center">
          <h1 className="text-title font-bold">This link isn&apos;t available</h1>
          <p className="mx-auto mt-3 max-w-md text-ink-soft">{gone}</p>
          <Link href="/plan" className="btn-primary mt-8 inline-flex">Plan your own trip</Link>
        </div>
      )}
      {!gone && !view && <p className="container-x py-24 text-center text-ink-mute" role="status">Opening the plan…</p>}

      {view && itinerary && (
        <div className="container-x pb-24">
          <section className="rounded-3xl border border-white/10 bg-[#0f1219] p-6 text-white md:p-9">
            <p className="text-sm text-white/60">A trip shared with you</p>
            <h1 className="mt-2 text-[28px] font-extrabold leading-tight tracking-tight sm:text-4xl">{view.itinerary.title || view.trip.title}</h1>
            <p className="mt-4 flex flex-wrap items-center gap-2 text-[14px] text-white/80">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1"><CalendarDays className="h-3.5 w-3.5" aria-hidden />
                {niceDate(view.trip.start_date)}{view.trip.end_date !== view.trip.start_date ? ` – ${niceDate(view.trip.end_date)}` : ""}</span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1"><Users className="h-3.5 w-3.5" aria-hidden />{plural(view.trip.travellers, "traveller")}</span>
            </p>
            <dl className="num mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
              <div><dt className="text-xs uppercase tracking-[0.14em] text-white/45">Planned cost</dt><dd className="text-xl font-bold">{money(view.itinerary.total_cost, view.trip.currency)}</dd></div>
              <div><dt className="text-xs uppercase tracking-[0.14em] text-white/45">Per person</dt><dd className="text-xl font-bold">{money(Math.round(view.itinerary.cost_per_person), view.trip.currency)}</dd></div>
              {view.budget && <div><dt className="text-xs uppercase tracking-[0.14em] text-white/45">Budget</dt><dd className="text-xl font-bold">{money(view.budget.budget, view.trip.currency)}</dd></div>}
              {view.budget && <div><dt className="text-xs uppercase tracking-[0.14em] text-white/45">Left</dt><dd className="text-xl font-bold">{money(view.budget.remaining, view.trip.currency)}</dd></div>}
            </dl>
            <p className="mt-4 text-[13px] text-white/55">Costs are labelled on each stop: a fare or price marked Estimated is a calculated estimate, so check it before booking.</p>
          </section>

          {view.itinerary.summary && <p className="mt-8 max-w-3xl text-[17px] leading-relaxed text-ink-soft">{view.itinerary.summary}</p>}

          <div className="mt-8">
            <Timeline itinerary={itinerary} currency={view.trip.currency} near={view.trip.destination} />
          </div>

          {view.itinerary.tips.length > 0 && (
            <section className="panel mt-10 p-5"><h2 className="font-bold">Good to know</h2>
              <ul className="mt-2 list-disc space-y-1.5 pl-5 text-ink-soft">{view.itinerary.tips.map((t, i) => <li key={i}>{t}</li>)}</ul></section>
          )}

          {view.split && (
            <section className="panel mt-10 p-5" aria-labelledby="split-title">
              <h2 id="split-title" className="flex items-center gap-2 font-bold"><Wallet className="h-4 w-4" aria-hidden />Who paid, who owes</h2>
              <ul className="mt-3 space-y-2">
                {view.split.settlements.length === 0 && <li className="text-ink-soft">Everyone is square.</li>}
                {view.split.settlements.map((s, i) => (
                  <li key={i} className="flex justify-between rounded-xl bg-haze px-4 py-2.5"><span className="font-semibold">{s.from} pays {s.to}</span><span className="num font-bold">{money(s.amount, view.trip.currency)}</span></li>
                ))}
              </ul>
              {view.split.expenses.length > 0 && (
                <ul className="mt-4 divide-y divide-line text-[14px]">
                  {view.split.expenses.map((e, i) => (
                    <li key={i} className="flex justify-between py-2"><span>{e.description}{e.paid_by && <span className="text-ink-mute"> · paid by {e.paid_by}</span>}</span><span className="num font-semibold">{money(e.amount, view.trip.currency)}</span></li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <footer className="mt-12 space-y-2 border-t border-line pt-6 text-[13px] text-ink-mute">
            {view.google.attribution && (
              <p>Place details from{" "}
                {/* Google's terms: "Google Maps" unaltered and never wrapped onto two lines (the logo is preferred; see ADR 0005) */}
                <a className="inline-flex items-center gap-1 whitespace-nowrap underline underline-offset-4" href="https://www.google.com/maps" target="_blank" rel="noopener noreferrer">Google Maps<ExternalLink className="h-3 w-3" aria-hidden /></a>
              </p>
            )}
            <p>Made with Yaitro. This page is private to the people who have the link and may stop working when its owner ends it.</p>
          </footer>
        </div>
      )}
    </main>
  );
}
