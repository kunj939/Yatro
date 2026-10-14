"use client";

import { useState } from "react";
import { CalendarCheck, CalendarPlus, Download, ExternalLink, Map as MapIcon, RefreshCw } from "lucide-react";
import type { Trip } from "@/lib/types";
import { ApiError, endpoints, type CalendarLinks } from "@/lib/api";
import { saveFile, tripKml } from "@/lib/tripLinks";

/** "Take it on the trip" (ADR 0015): the plan into the traveller's own calendar, kept in sync, and onto Google My Maps.
 *  The day routes and each stop's directions sit on the plan itself (Timeline). */
export function TakeAlong({ trip }: { trip: Trip }) {
  const [links, setLinks] = useState<CalendarLinks | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const it = trip.itinerary;
  if (!it) return null;
  const file = (trip.destination.split(",")[0] || "trip").replace(/[^\p{L}\p{N}]+/gu, "-");

  /** Subscribe in one calendar app. The tab is opened inside the tap (browsers block one opened after a wait). */
  async function subscribe(app: "google" | "apple" | "outlook") {
    setError(null); setNote(null); setBusy(app);
    const tab = app === "apple" ? null : window.open("about:blank", "_blank");
    try {
      const l = links ?? await endpoints.calendarOn(trip.id);
      setLinks(l);
      const url = app === "google" ? l.google_url : app === "outlook" ? l.outlook_url : l.webcal_url;
      if (tab) tab.location.href = url; else window.location.href = url;
      setNote("Your calendar shows every stop, with a reminder before each train. Change the plan and it updates there too.");
    } catch (e) {
      tab?.close();
      setError(e instanceof ApiError ? e.message : "Couldn't make the calendar link. Try again.");
    } finally { setBusy(null); }
  }

  async function download() {
    setError(null); setNote(null); setBusy("ics");
    try {
      saveFile(`${file}-Yaitro.ics`, await endpoints.calendarFile(trip.id), "text/calendar");
      setNote("Saved. Open the file to add the plan to your calendar (this copy won't update if the plan changes).");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't make the file. Try again.");
    } finally { setBusy(null); }
  }

  async function stopSync() {
    setBusy("off"); setError(null);
    try { await endpoints.calendarOff(trip.id); setLinks(null); setNote("Calendar sync is off. The old link no longer works."); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't turn it off. Try again."); }
    finally { setBusy(null); }
  }

  function myMaps() {
    saveFile(`${file}-Yaitro.kml`, tripKml(trip, it!), "application/vnd.google-earth.kml+xml");
    setNote("Map file saved. In Google My Maps: Create a new map, then Import, and pick this file.");
    window.open("https://www.google.com/maps/d/", "_blank", "noopener");
  }

  const btn = "inline-flex items-center justify-center gap-2 rounded-xl border border-line bg-paper px-3 py-2.5 text-[14px] font-semibold text-ink transition-all hover:-translate-y-px hover:border-signal/50 disabled:opacity-60";
  return (
    <div className="card mb-6 p-4 md:p-5">
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-signal-soft text-signal-deep"><CalendarPlus className="h-5 w-5" aria-hidden /></span>
        <div className="min-w-0">
          <p className="font-bold leading-snug">Take it on the trip</p>
          <p className="text-[13.5px] text-ink-soft">
            Every stop in your calendar with reminders, kept in sync if you change the plan. Each day&apos;s route opens in Google Maps from the plan below.
          </p>
        </div>
      </div>

      <p className="mt-4 text-xs font-bold uppercase tracking-[0.12em] text-ink-mute">Add to your calendar</p>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <button type="button" className={`${btn} !border-signal/40 !bg-signal !text-on-signal`} onClick={() => subscribe("google")} disabled={!!busy}>
          <CalendarCheck className="h-4 w-4" aria-hidden />{busy === "google" ? "Opening…" : "Google"}
        </button>
        <button type="button" className={btn} onClick={() => subscribe("apple")} disabled={!!busy}>
          <CalendarCheck className="h-4 w-4" aria-hidden />{busy === "apple" ? "Opening…" : "iPhone / Mac"}
        </button>
        <button type="button" className={btn} onClick={() => subscribe("outlook")} disabled={!!busy}>
          <CalendarCheck className="h-4 w-4" aria-hidden />{busy === "outlook" ? "Opening…" : "Outlook"}
        </button>
        <button type="button" className={btn} onClick={download} disabled={!!busy}>
          <Download className="h-4 w-4" aria-hidden />{busy === "ics" ? "Making…" : ".ics file"}
        </button>
      </div>

      <p className="mt-4 text-xs font-bold uppercase tracking-[0.12em] text-ink-mute">Whole trip on a map</p>
      <button type="button" className={`${btn} mt-2 w-full sm:w-auto`} onClick={myMaps}>
        <MapIcon className="h-4 w-4" aria-hidden />Save to Google My Maps<ExternalLink className="h-3.5 w-3.5 text-ink-mute" aria-hidden />
      </button>

      {note && <p role="status" className="mt-3 text-[13px] font-semibold text-teal-deep">{note}</p>}
      {error && <p role="alert" className="mt-3 text-[13px] font-medium text-danger">{error}</p>}
      {links && (
        <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-ink-mute">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />Calendar sync is on.
          <button type="button" onClick={stopSync} disabled={!!busy} className="font-semibold text-ink-soft underline underline-offset-2 hover:text-ink">Stop syncing</button>
        </p>
      )}
    </div>
  );
}
