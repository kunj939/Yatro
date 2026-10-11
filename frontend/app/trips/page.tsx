"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, CalendarDays, MapPin, Plus, Trash2, Users } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { RequireAuth } from "@/components/AuthPage";
import { ApiError, endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { TripCard } from "@/lib/types";
import { money, niceDate, plural } from "@/lib/format";

const STATUS: Record<string, { label: string; cls: string }> = {
  ready: { label: "Ready to go", cls: "bg-teal-soft text-teal-deep" },
  draft: { label: "Not planned yet", cls: "bg-haze-2 text-ink-soft" },
  planning: { label: "Planning", cls: "bg-signal-soft text-signal-deep" },
  failed: { label: "Needs attention", cls: "bg-danger-soft text-danger" },
};

function TripList() {
  const { quota } = useAuth();
  const [trips, setTrips] = useState<TripCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    endpoints.trips().then((r) => setTrips(r.trips)).catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load your trips."));
  }, []);

  const [confirming, setConfirming] = useState<string | null>(null);

  async function remove(t: TripCard) {
    setConfirming(null);
    try { await endpoints.deleteTrip(t.id); setTrips((ts) => ts?.filter((x) => x.id !== t.id) ?? null); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't delete this trip."); }
  }

  return (
    <div className="container-x pb-16 pt-4 md:pt-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="eyebrow">My trips</span>
          <h1 className="mt-4 text-title font-bold">Your trips</h1>
          {quota && (
            <p className="num mt-2 inline-flex items-center gap-2 text-ink-soft">
              <span className="h-1.5 w-1.5 rounded-full bg-signal" />
              {quota.plan === "plus" ? "Plus: unlimited trip plans" : `${quota.free_plans_left} of ${quota.free_plans_total} free plans left`}
              {quota.trip_credits > 0 ? `, ${plural(quota.trip_credits, "trip credit")}` : ""}
            </p>
          )}
        </div>
        <Link href="/plan" className="btn-primary shine"><Plus className="h-4 w-4" aria-hidden />Plan a new trip</Link>
      </div>
      {error && <p role="alert" className="mt-6 rounded-xl bg-danger-soft px-4 py-3 text-danger">{error}</p>}
      {trips === null && !error && (
        <ul className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading trips">
          {[0, 1, 2].map((i) => <li key={i} className="h-56 animate-pulse rounded-2xl border border-line bg-paper/60" />)}
        </ul>
      )}
      {trips?.length === 0 && (
        <div className="mt-10 rounded-3xl border border-dashed border-line bg-paper/50 p-12 text-center">
          <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-signal-soft"><MapPin className="h-7 w-7 text-signal-deep" aria-hidden /></span>
          <p className="mt-4 text-xl font-bold">No trips yet. Let&apos;s change that.</p>
          <p className="mx-auto mt-1 max-w-sm text-ink-soft">Tell us where, when and how much. We&apos;ll build a plan that actually fits.</p>
          <Link href="/plan" className="btn-primary mt-6">Plan your first trip<ArrowRight className="h-4 w-4" aria-hidden /></Link>
        </div>
      )}
      {!!trips?.length && (
        <ul className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {trips.map((t, i) => {
            const st = STATUS[t.status] ?? STATUS.draft;
            const infeasible = t.feasible === false && t.estimated_cost === null;
            const used = t.estimated_cost !== null ? Math.min(100, (t.estimated_cost / Math.max(1, t.budget)) * 100) : null;
            const over = (t.remaining ?? 0) < 0;
            return (
              <li key={t.id} style={{ ["--d" as string]: `${i * 60}ms` }}
                  className="fade-up group relative flex flex-col rounded-2xl border border-line bg-paper/85 p-5 backdrop-blur transition-all duration-300 hover:-translate-y-1 hover:border-signal/40 hover:shadow-glow">
                <div className="flex items-start justify-between gap-3">
                  <span className={`rounded-full px-2.5 py-0.5 text-[13px] font-semibold ${infeasible ? "bg-caution-soft text-caution" : st.cls}`}>
                    {infeasible ? "Needs a change" : st.label}
                  </span>
                  {confirming === t.id ? (
                    <span className="relative z-10 flex items-center gap-1.5 text-[13px]">
                      <button onClick={() => remove(t)} className="rounded-full bg-danger px-3 py-1 font-semibold text-white hover:opacity-90">Delete</button>
                      <button onClick={() => setConfirming(null)} className="rounded-full border border-line px-3 py-1 font-semibold hover:bg-haze">Keep</button>
                    </span>
                  ) : (
                    <button onClick={() => setConfirming(t.id)} className="relative z-10 rounded-full p-1.5 text-ink-mute opacity-60 transition-all hover:bg-danger-soft hover:text-danger group-hover:opacity-100" aria-label={`Delete ${t.title}`}>
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
                <h2 className="mt-3 text-xl font-bold leading-snug">
                  <Link href={`/trips/${t.id}`} className="after:absolute after:inset-0">{t.title}</Link>
                </h2>
                <p className="mt-2 flex min-w-0 items-center gap-2 text-[14px] text-ink-soft">
                  <span className="truncate">{t.origin}</span>
                  <span aria-hidden className="flex min-w-[28px] flex-1 items-center"><span className="h-px flex-1 border-t border-dashed border-line" /><ArrowRight className="h-3.5 w-3.5 text-signal" /></span>
                  <span className="truncate font-semibold text-ink">{t.destination}</span>
                </p>
                <p className="num mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[14px] text-ink-mute">
                  <span className="inline-flex items-center gap-1"><CalendarDays className="h-3.5 w-3.5" aria-hidden />{niceDate(t.start_date)}{t.end_date !== t.start_date ? ` – ${niceDate(t.end_date)}` : ""}</span>
                  <span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" aria-hidden />{plural(t.travellers, "traveller")}</span>
                </p>
                <div className="mt-auto pt-5">
                  <div className="num grid grid-cols-3 gap-2 border-t border-line pt-4 text-[14px]">
                    <span><span className="block text-xs text-ink-mute">Budget</span><span className="font-bold">{money(t.budget, t.currency)}</span></span>
                    <span><span className="block text-xs text-ink-mute">Planned</span><span className="font-bold">{money(t.estimated_cost, t.currency)}</span></span>
                    <span><span className="block text-xs text-ink-mute">{over ? "Over" : "Left"}</span><span className={`font-bold ${over ? "text-danger" : "text-teal-deep"}`}>{t.remaining === null ? "—" : money(Math.abs(t.remaining), t.currency)}</span></span>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-haze-2" aria-hidden>
                    {used !== null && <div className={`h-full rounded-full ${over ? "bg-danger" : "bg-gradient-to-r from-signal to-spark"}`} style={{ width: `${used}%` }} />}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default function TripsPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <RequireAuth><TripList /></RequireAuth>
    </main>
  );
}