"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Navbar } from "@/components/Navbar";
import { TripDashboard } from "@/components/trip/TripDashboard";
import { TripLoading } from "@/components/trip/TripLoading";
import { ApiError, endpoints } from "@/lib/api";
import { usePlanStream } from "@/lib/usePlanStream";
import type { Trip } from "@/lib/types";

export default function DemoTripPage() {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { shown, running, run } = usePlanStream();
  const started = useRef(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setTrip(await endpoints.demoTrip());
    } catch (e) {
      if (e instanceof ApiError && e.code === "demo_not_ready") {
        try { setTrip((await run("/api/demo/plan?stream=true", {})).trip); }
        catch (err) { setError(err instanceof ApiError ? err.message : "The sample trip couldn't be planned."); }
      } else setError(e instanceof ApiError ? e.message : "The sample trip couldn't be loaded.");
    }
  }, [run]);

  useEffect(() => { if (!started.current) { started.current = true; load(); } }, [load]);

  return (
    <main className="min-h-screen">
      <Navbar />
      <div className="border-b border-line bg-demo-soft">
        <p className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-5 py-3 text-[16px] text-ink md:px-8">
          <span>This is a sample plan built by the real engine from sample data: Gurugram to Jaipur, a couple, ₹5,000, vegetarian, train and cab.</span>
          <Link href="/plan" className="btn-dark !py-2">Plan my trip</Link>
        </p>
      </div>
      {running && <TripLoading shown={shown} title="Planning Gurugram to Jaipur" subtitle="₹5,000 for 2 travellers" />}
      {!running && error && (
        <div className="mx-auto max-w-lg px-5 py-16 text-center">
          <h1 className="text-2xl font-bold">The sample trip isn't available</h1>
          <p className="mt-2 text-ink-soft">{error}</p>
          <button className="btn-dark mt-6" onClick={() => load()}>Try again</button>
        </div>
      )}
      {!running && !error && !trip && <div className="grid min-h-[50vh] place-items-center text-ink-mute" aria-busy="true">Loading the sample trip…</div>}
      {!running && trip && <TripDashboard initial={trip} readOnly />}
    </main>
  );
}