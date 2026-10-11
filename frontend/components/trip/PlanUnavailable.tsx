"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CalendarDays, Clock, Users } from "lucide-react";
import type { Trip } from "@/lib/types";
import { money, niceDate, plural, reopenAt } from "@/lib/format";

/** "We're busy right now" (ADR 0007). A calm, normal screen, never an error: it says when new plans reopen in the person's
 *  own time, that no free plan was used, and that the trip's details are saved so planning is one tap when it is time. */
export function PlanUnavailable({ trip, reason, retryAt, onPlan }: {
  trip: Trip; reason?: "provider_busy" | "daily_cap" | "high_demand" | "paused"; retryAt?: string; onPlan: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const open = !retryAt || now >= new Date(retryAt).getTime();
  const capped = reason === "daily_cap";
  const crowded = reason === "high_demand";
  const paused = reason === "paused";  // an admin paused planning (ADR 0010)
  return (
    <div className="mx-auto max-w-xl px-5 py-16">
      <div className="panel p-7 text-center md:p-9">
        <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-signal-soft"><Clock className="h-6 w-6 text-signal-deep" aria-hidden /></span>
        <h1 className="mt-5 text-2xl font-bold">{capped ? "We've reached today's limit on new plans" : crowded ? "We're very busy right now" : paused ? "Planning is paused for a little while" : "We're a little busy right now"}</h1>
        <p className="mt-2 text-[17px] text-ink-soft">
          {retryAt && !open
            ? <>{crowded ? "Please try again" : "New plans reopen"} <strong className="font-semibold text-ink">{reopenAt(retryAt, new Date(now))}</strong>.</>
            : "New plans are open again."}
        </p>
        <p className="mt-1 text-[17px] text-ink-soft">None of your free plans were used.</p>

        <div className="mt-6 rounded-2xl bg-haze px-5 py-4 text-left">
          <p className="text-sm font-semibold text-teal-deep">Your trip details are saved</p>
          <p className="mt-1 text-[17px] font-bold">{trip.origin} to {trip.destination}</p>
          <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[14px] text-ink-soft">
            <span className="inline-flex items-center gap-1.5"><CalendarDays className="h-3.5 w-3.5" aria-hidden />{niceDate(trip.start_date)}{trip.end_date !== trip.start_date ? ` – ${niceDate(trip.end_date)}` : ""}</span>
            <span className="inline-flex items-center gap-1.5"><Users className="h-3.5 w-3.5" aria-hidden />{plural(trip.travellers, "traveller")}</span>
            <span className="num">{money(trip.budget, trip.currency)}</span>
          </p>
        </div>

        <button className="btn-primary mt-7 w-full py-3" onClick={onPlan} disabled={!open}>
          {open ? "Plan this trip" : `Opens ${reopenAt(retryAt!, new Date(now))}`}
        </button>
        <Link href="/trips" className="mt-4 inline-block text-sm font-semibold text-ink-soft underline underline-offset-4">Your trips</Link>
      </div>
    </div>
  );
}
