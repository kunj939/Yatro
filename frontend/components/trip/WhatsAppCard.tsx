"use client";

import { useState } from "react";
import { Download, FileText, Mail, MessageCircle } from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import type { ShareResult, Trip } from "@/lib/types";
import { remember, remembered, tokenOf } from "./ShareSheet";
import { buildTripPdf, savePdf, sendPdf } from "@/lib/tripPdf";

/** "Send this plan to my WhatsApp": one tap makes a private link (budget hidden, ends 30 days after the trip), builds the message and
 *  opens WhatsApp on the person's OWN saved mobile number with it typed in. They tap Send in WhatsApp themselves: Yaitro cannot and does
 *  not send WhatsApp messages (that would need the paid WhatsApp Business API, which is not used; ADR 0006, invariant I9). */
export function WhatsAppCard({ trip }: { trip: Trip }) {
  const { user } = useAuth();
  const flags = useFlags();  // is email set up on this server, and switched on?
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fallback, setFallback] = useState<string | null>(null);  // a link to tap, if the browser blocked the new tab
  const [mailing, setMailing] = useState(false);
  const [mailed, setMailed] = useState<string | null>(null);
  const [pdf, setPdf] = useState<{ file: File; url: string } | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfNote, setPdfNote] = useState<string | null>(null);
  if (!user) return null;
  const last4 = user.phone?.slice(-4);

  async function email() {
    setMailing(true); setError(null); setMailed(null);
    try { setMailed((await endpoints.emailPlan(trip.id)).sent_to); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't send the email. Try again."); }
    finally { setMailing(false); }
  }

  /** The trip's private link: the one this browser made before, or a fresh one (budget hidden, ends 30 days after the trip). */
  async function link(): Promise<Omit<ShareResult, "share">> {
    const old = remembered(trip.id);
    let r: Omit<ShareResult, "share">;
    try {
      r = old ? await endpoints.shareText(trip.id, old) : await endpoints.createShare(trip.id, { show_budget: false, include_split: false, expires: "default" });
    } catch (e) {
      if (old && e instanceof ApiError && e.status === 404) {  // the remembered link was stopped or has ended: make a fresh one
        remember(trip.id, null);
        r = await endpoints.createShare(trip.id, { show_budget: false, include_split: false, expires: "default" });
      } else throw e;
    }
    remember(trip.id, tokenOf(r.url));
    return r;
  }

  /** First tap makes the PDF; on a phone the button then reads "Send PDF" and the next tap opens the share menu (phones allow it
   *  only right after a tap, which building the PDF would use up). Where files can't be shared, the PDF is saved straight away. */
  async function pdfTap() {
    setError(null);
    if (pdf) {
      const how = await sendPdf(pdf.file, trip.title, pdf.url);
      setPdfNote(how === "saved" ? "PDF saved to your downloads. Attach it in WhatsApp." : null);
      return;
    }
    setPdfBusy(true); setPdfNote(null);
    try {
      const r = await link();
      const file = await buildTripPdf(tokenOf(r.url), r.url);
      setPdf({ file, url: r.url });
      if (navigator.canShare?.({ files: [file] })) setPdfNote("PDF ready. Tap Send PDF and pick WhatsApp.");
      else { await sendPdf(file, trip.title, r.url); setPdfNote("PDF saved to your downloads. Attach it in WhatsApp."); }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't make the PDF. Try again.");
    } finally { setPdfBusy(false); }
  }

  /** Always save the PDF (making it first if needed), whatever the device can share. */
  async function download() {
    setError(null);
    if (pdf) { savePdf(pdf.file); setPdfNote("PDF saved to your downloads."); return; }
    setPdfBusy(true); setPdfNote(null);
    try {
      const r = await link();
      const file = await buildTripPdf(tokenOf(r.url), r.url);
      setPdf({ file, url: r.url });
      savePdf(file);
      setPdfNote("PDF saved to your downloads.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't make the PDF. Try again.");
    } finally { setPdfBusy(false); }
  }

  async function send() {
    setBusy(true); setError(null); setFallback(null);
    // Open the tab now, inside the tap (browsers block a tab opened after a wait), and point it at WhatsApp once the message is ready.
    const tab = window.open("about:blank", "_blank");
    try {
      const r = await link();
      if (!r.wa_me_url) throw new Error("There is no mobile number on your account.");
      if (tab) tab.location.href = r.wa_me_url;
      else setFallback(r.wa_me_url);
    } catch (e) {
      tab?.close();
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Couldn't prepare the message. Try again.");
    } finally { setBusy(false); }
  }

  // Four equal tiles, icon over label: one look whichever ones this account has (WhatsApp needs a mobile number,
  // email needs plan emails switched on).
  const tile = "flex flex-col items-center justify-center gap-1.5 rounded-xl px-2 py-3 text-center text-[13.5px] font-semibold transition-all hover:-translate-y-px disabled:opacity-60 sm:text-[14px]";
  return (
    <div className="card mb-6 p-4 md:p-5">
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-teal-soft text-teal"><MessageCircle className="h-5 w-5" aria-hidden /></span>
        <div className="min-w-0 flex-1">
          <p className="font-bold leading-snug">Keep this plan with you</p>
          <p className="text-[13.5px] text-ink-soft">
            {last4 ? <>WhatsApp opens with the plan typed for your number ending {last4}; you tap Send. </> : "Take it with you as a PDF or by email. "}
            {flags.planEmails ? (user.email_plans !== false ? "New plans are also emailed to you." : "Plan emails are off (Account).") : null}
          </p>
        </div>
      </div>
      <div className={`mt-4 grid grid-cols-2 gap-2 ${[last4, true, true, flags.planEmails].filter(Boolean).length === 4 ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
        {last4 && (
          <button type="button" className={`${tile} bg-signal text-on-signal shadow-[0_8px_20px_-10px_rgb(var(--signal)/0.7)]`} onClick={send} disabled={busy}>
            <MessageCircle className="h-5 w-5" aria-hidden />{busy ? "Preparing…" : "WhatsApp"}
          </button>
        )}
        <button type="button" className={`${tile} ${last4 ? "bg-night text-white" : "bg-signal text-on-signal"}`} onClick={pdfTap} disabled={pdfBusy}>
          <FileText className="h-5 w-5" aria-hidden />{pdfBusy ? "Making PDF…" : pdf ? "Send PDF" : "Share PDF"}
        </button>
        <button type="button" className={`${tile} border border-line bg-paper text-ink hover:border-ink/30`} onClick={download} disabled={pdfBusy}>
          <Download className="h-5 w-5" aria-hidden />Download PDF
        </button>
        {flags.planEmails && (
          <button type="button" className={`${tile} border border-line bg-paper text-ink hover:border-ink/30`} onClick={email} disabled={mailing}>
            <Mail className="h-5 w-5" aria-hidden />{mailing ? "Sending…" : "Email it to me"}
          </button>
        )}
      </div>
      {pdfNote && <p role="status" className="mt-3 text-[13px] font-semibold text-teal-deep">{pdfNote}</p>}
      {mailed && <p role="status" className="mt-3 text-[13px] font-semibold text-teal-deep">Emailed to {mailed}.</p>}
      {error && <p role="alert" className="mt-3 text-[13px] font-medium text-danger">{error}</p>}
      {fallback && <p className="mt-3 text-[13px]"><a className="font-semibold underline underline-offset-4" href={fallback} target="_blank" rel="noopener noreferrer">Tap here to open WhatsApp</a></p>}
    </div>
  );
}
