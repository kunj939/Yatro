"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Navbar } from "@/components/Navbar";
import { RequireAuth } from "@/components/AuthPage";
import { TripDashboard } from "@/components/trip/TripDashboard";
import { TripLoading } from "@/components/trip/TripLoading";
import { QuotaPanel } from "@/components/trip/InfeasibleView";
import { PlanUnavailable } from "@/components/trip/PlanUnavailable";
import { ApiError, endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { usePlanStream } from "@/lib/usePlanStream";
import type { Trip } from "@/lib/types";
import { money, plural } from "@/lib/format";

function TripView() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const { quota, refresh } = useAuth();
  const [trip, setTrip] = useState<Trip | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [quotaBlocked, setQuotaBlocked] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ reason?: "provider_busy" | "daily_cap" | "high_demand" | "paused"; retryAt?: string } | null>(null); // can't plan right now (ADR 0007)
  const { shown, running, queued, run } = usePlanStream();
  const started = useRef(false);

  const load = useCallback(async () => {
    try { const t = await endpoints.trip(id); setTrip(t); setError(null); return t; }
    catch (e) { setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Couldn't load this trip.")); return null; }
  }, [id]);

  const plan = useCallback(async () => {
    setError(null); setQuotaBlocked(null); setBusy(null);
    try {
      const out = await run(`/api/trips/${id}/plan?stream=true`, { stream: true });
      setTrip(out.trip);
      if (out.status === "unavailable") setBusy({ reason: out.reason, retryAt: out.retry_at });  // a normal screen, not an error
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "unknown", "Planning failed. Please try again.");
      if (err.status === 402 || err.code === "quota_exhausted") setQuotaBlocked(err.message);
      else { await load(); setError(err); }  // load() clears errors, so set this one after it
    } finally {
      refresh();
      router.replace(`/trips/${id}`, { scroll: false });
    }
  }, [id, load, refresh, router, run]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    load().then((t) => {
      if (t && params.get("plan") === "1" && t.plan_count === 0 && !t.feasibility) plan();
      else if (params.get("plan")) router.replace(`/trips/${id}`, { scroll: false });
    });
  }, [id, load, params, plan, router]);

  if (running || (!trip && !error && !quotaBlocked)) {
    return running && trip ? (
      <TripLoading shown={shown} queued={queued} title={`Planning ${trip.origin} to ${trip.destination}`}
                   subtitle={`${money(trip.budget, trip.currency)} for ${plural(trip.travellers, "traveller")}`} />
    ) : (
      <div className="grid min-h-[50vh] place-items-center text-ink-mute" aria-busy="true">Getting your trip ready…</div>
    );
  }
  if (busy && trip) {
    return <PlanUnavailable trip={trip} reason={busy.reason} retryAt={busy.retryAt} onPlan={plan} />;
  }
  if (quotaBlocked) {
    return <div className="px-5 py-12"><QuotaPanel quota={quota} message={quotaBlocked} next={`/trips/${id}?plan=1`} /></div>;
  }
  if (!trip) {
    return (
      <div className="mx-auto max-w-lg px-5 py-16 text-center">
        <h1 className="text-2xl font-bold">{error?.status === 404 ? "Trip not found" : "Something went wrong"}</h1>
        <p className="mt-2 text-ink-soft">{error?.status === 404 ? "It may have been deleted, or it belongs to another account." : error?.message}</p>
        <div className="mt-6 flex justify-center gap-3">
          <Link href="/trips" className="btn-dark">Your trips</Link>
          {error?.status !== 404 && <button className="btn-ghost" onClick={() => load()}>Try again</button>}
        </div>
      </div>
    );
  }
  if (trip.plan_count === 0 && !trip.feasibility) {
    return (
      <div className="mx-auto max-w-lg px-5 py-16 text-center">
        <h1 className="text-2xl font-bold">{trip.title}</h1>
        <p className="mt-2 text-ink-soft">Saved, but not planned yet.</p>
        {error && <p role="alert" className="mt-4 rounded-xl bg-danger-soft px-4 py-3 text-danger">{error.message}</p>}
        <button className="btn-primary mt-6" onClick={plan}>Build my plan</button>
      </div>
    );
  }
  return (
    <>
      {error && <p role="alert" className="mx-auto mt-6 max-w-7xl rounded-xl bg-danger-soft px-4 py-3 text-danger md:px-8">{error.message}</p>}
      <TripDashboard initial={trip} onReload={load} />
    </>
  );
}

export default function TripPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <RequireAuth note="Sign in to see this trip.">
        <Suspense><TripView /></Suspense>
      </RequireAuth>
    </main>
  );
}