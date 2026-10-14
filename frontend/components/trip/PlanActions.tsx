"use client";

import { useEffect, useRef, useState } from "react";
import { CalendarPlus, ChevronDown, Mail, Map as MapIcon, MessageCircle, RefreshCw, Share2 } from "lucide-react";
import { ApiError, endpoints, type CalendarLinks } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import type { ShareResult, Trip } from "@/lib/types";
import { saveFile, tripKml } from "@/lib/tripLinks";
import { remember, remembered, tokenOf } from "./ShareSheet";

/** Everything for taking the plan along, in one slim bar:
 *  - WhatsApp: a private link (budget hidden, ends 30 days after the trip) and the message, opened on the person's OWN number; they
 *    tap Send themselves (no WhatsApp Business API; ADR 0006, invariant I9).
 *  - Share: the share sheet (link, message, and the PDF to send or download).
 *  - Email: the plan to their own inbox.
 *  - Calendar: a feed Google, Apple or Outlook subscribe to, so a changed plan updates there too (ADR 0015).
 *  - My Maps: the whole trip as a KML file for Google My Maps. */
export function PlanActions({ trip, onShare }: { trip: Trip; onShare?: () => void }) {
  const { user } = useAuth();
  const flags = useFlags();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fallback, setFallback] = useState<string | null>(null);
  const [cal, setCal] = useState<CalendarLinks | null>(null);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [menu]);
  if (!user || !trip.itinerary) return null;
  const last4 = user.phone?.slice(-4);
  const it = trip.itinerary;
  const file = (trip.destination.split(",")[0] || "trip").replace(/[^\p{L}\p{N}]+/gu, "-");
  const start = (what: string) => { setBusy(what); setError(null); setNote(null); setFallback(null); };

  async function link(): Promise<Omit<ShareResult, "share">> {
    const old = remembered(trip.id);
    let r: Omit<ShareResult, "share">;
    try {
      r = old ? await endpoints.shareText(trip.id, old) : await endpoints.createShare(trip.id, { show_budget: false, include_split: false, expires: "default" });
    } catch (e) {
      if (old && e instanceof ApiError && e.status === 404) {  // the remembered link was stopped or has ended
        remember(trip.id, null);
        r = await endpoints.createShare(trip.id, { show_budget: false, include_split: false, expires: "default" });
      } else throw e;
    }
    remember(trip.id, tokenOf(r.url));
    return r;
  }

  async function whatsapp() {
    start("wa");
    const tab = window.open("about:blank", "_blank");  // opened inside the tap: browsers block a tab opened after a wait
    try {
      const r = await link();
      if (!r.wa_me_url) throw new Error("There is no mobile number on your account.");
      if (tab) tab.location.href = r.wa_me_url; else setFallback(r.wa_me_url);
    } catch (e) {
      tab?.close();
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Couldn't prepare the message. Try again.");
    } finally { setBusy(null); }
  }

  async function email() {
    start("mail");
    try { setNote(`Emailed to ${(await endpoints.emailPlan(trip.id)).sent_to}.`); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't send the email. Try again."); }
    finally { setBusy(null); }
  }

  async function subscribe(app: "google" | "apple" | "outlook") {
    setMenu(false); start("cal");
    const tab = app === "apple" ? null : window.open("about:blank", "_blank");
    try {
      const l = cal ?? await endpoints.calendarOn(trip.id);
      setCal(l);
      const url = app === "google" ? l.google_url : app === "outlook" ? l.outlook_url : l.webcal_url;
      if (tab) tab.location.href = url; else window.location.href = url;
      setNote("Every stop goes into your calendar, with a reminder before each train. Change the plan and it updates there too.");
    } catch (e) {
      tab?.close();
      setError(e instanceof ApiError ? e.message : "Couldn't make the calendar link. Try again.");
    } finally { setBusy(null); }
  }

  async function stopSync() {
    setMenu(false); start("cal");
    try { await endpoints.calendarOff(trip.id); setCal(null); setNote("Calendar sync is off. The old link no longer works."); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't turn it off. Try again."); }
    finally { setBusy(null); }
  }

  function myMaps() {
    saveFile(`${file}-Yaitro.kml`, tripKml(trip, it), "application/vnd.google-earth.kml+xml");
    setError(null);
    setNote("Map file saved. In Google My Maps: create a map, tap Import and pick this file.");
    window.open("https://www.google.com/maps/d/", "_blank", "noopener");
  }

  const pill = "inline-flex items-center justify-center gap-1.5 rounded-full border px-3 py-2 text-[13.5px] font-semibold transition-all hover:-translate-y-px disabled:opacity-60";
  const plain = `${pill} border-line bg-paper text-ink hover:border-signal/50`;
  return (
    <div className="card mb-5 p-3 sm:p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        {last4 && (
          <button type="button" onClick={whatsapp} disabled={!!busy} className={`${pill} border-signal bg-signal text-on-signal`}
                  title={`Opens WhatsApp on your number ending ${last4}; you tap Send`}>
            <MessageCircle className="h-4 w-4" aria-hidden />{busy === "wa" ? "Opening…" : "WhatsApp"}
          </button>
        )}
        {flags.share && onShare && (
          <button type="button" onClick={onShare} className={plain} title="A link, the message, or the plan as a PDF">
            <Share2 className="h-4 w-4" aria-hidden />Share or PDF
          </button>
        )}
        <div className="relative" ref={menuRef}>
          <button type="button" onClick={() => setMenu(!menu)} disabled={busy === "cal"} aria-haspopup="menu" aria-expanded={menu} className={plain}>
            <CalendarPlus className="h-4 w-4" aria-hidden />{busy === "cal" ? "Opening…" : "Calendar"}
            <ChevronDown className={`h-3.5 w-3.5 text-ink-mute transition-transform ${menu ? "rotate-180" : ""}`} aria-hidden />
          </button>
          {menu && (
            <div role="menu" className="sheet-in absolute left-0 z-30 mt-2 w-56 rounded-2xl border border-line bg-paper p-1.5 shadow-[0_24px_60px_-20px_rgb(var(--shadow)/0.45)]">
              <p className="px-3 pb-1 pt-1.5 text-[12px] text-ink-mute">Stays in sync if you change the plan</p>
              {([["google", "Google Calendar"], ["apple", "iPhone / Mac"], ["outlook", "Outlook"]] as const).map(([k, l]) => (
                <button key={k} role="menuitem" type="button" onClick={() => subscribe(k)}
                        className="flex w-full items-center rounded-lg px-3 py-2.5 text-left text-[14px] font-medium text-ink hover:bg-haze">{l}</button>
              ))}
              {cal && (
                <button role="menuitem" type="button" onClick={stopSync}
                        className="mt-1 flex w-full items-center gap-2 rounded-lg border-t border-line px-3 py-2.5 text-left text-[13px] text-ink-soft hover:bg-haze">
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden />Stop syncing
                </button>
              )}
            </div>
          )}
        </div>
        <button type="button" onClick={myMaps} className={plain} title="The whole trip on one map, a colour per day">
          <MapIcon className="h-4 w-4" aria-hidden />My Maps
        </button>
        {flags.planEmails && (
          <button type="button" onClick={email} disabled={!!busy} className={plain}>
            <Mail className="h-4 w-4" aria-hidden />{busy === "mail" ? "Sending…" : "Email"}
          </button>
        )}
      </div>
      {note && <p role="status" className="mt-2 px-1 text-[13px] font-semibold text-teal-deep">{note}</p>}
      {error && <p role="alert" className="mt-2 px-1 text-[13px] font-medium text-danger">{error}</p>}
      {fallback && <p className="mt-2 px-1 text-[13px]"><a className="font-semibold underline underline-offset-4" href={fallback} target="_blank" rel="noopener noreferrer">Tap here to open WhatsApp</a></p>}
    </div>
  );
}
