"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Download, FileText, Link2, MessageCircle, Share2, Trash2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { ApiError, endpoints } from "@/lib/api";
import type { ShareLink, ShareResult, Trip } from "@/lib/types";
import { buildTripPdf, savePdf, sendPdf } from "@/lib/tripPdf";

// The full link is shown once by the server and only its hash is kept there, so the browser keeps the last one it made
// (to share again later). The WhatsApp message is never stored: it is made again, from fresh data, each time.
const keyFor = (tripId: string) => `yatro.share.${tripId}`;
export function remembered(tripId: string): string | null {
  try { return window.localStorage.getItem(keyFor(tripId)); } catch { return null; }
}
export function remember(tripId: string, token: string | null) {
  try { token ? window.localStorage.setItem(keyFor(tripId), token) : window.localStorage.removeItem(keyFor(tripId)); } catch { /* storage unavailable */ }
}
/** "6 Oct" from a full timestamp (the API sends one; niceDate wants a bare date). */
const day = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
export const tokenOf = (url: string) => url.slice(url.lastIndexOf("/") + 1);

export function ShareSheet({ trip, open, onClose, onChanged }: { trip: Trip; open: boolean; onClose: () => void; onChanged: () => void }) {
  const [showBudget, setShowBudget] = useState(false);
  const [split, setSplit] = useState(false);
  const [never, setNever] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Omit<ShareResult, "share"> | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [canShare, setCanShare] = useState(false);
  const [hasOld, setHasOld] = useState(false);
  // The PDF is made as soon as the link exists, so "Send as PDF" can open the share menu straight away (phones only
  // allow it within a moment of the tap, which a PDF being built would use up).
  const [pdf, setPdf] = useState<File | null>(null);
  const [pdfState, setPdfState] = useState<"idle" | "making" | "ready" | "failed">("idle");
  const [pdfNote, setPdfNote] = useState<string | null>(null);

  useEffect(() => {
    setPdf(null); setPdfNote(null);
    if (!result) { setPdfState("idle"); return; }
    let live = true;
    setPdfState("making");
    buildTripPdf(tokenOf(result.url), result.url, trip)
      .then((f) => { if (live) { setPdf(f); setPdfState("ready"); } })
      .catch(() => live && setPdfState("failed"));
    return () => { live = false; };
  }, [result]);

  async function sharePdf() {
    if (!pdf || !result) return;
    const how = await sendPdf(pdf, trip.title, result.url);
    setPdfNote(how === "saved" ? "PDF saved to your downloads. Open WhatsApp and attach it to the chat." : null);
  }

  useEffect(() => {
    if (!open) return;
    setResult(null); setError(null); setCopied(null);
    setCanShare(typeof navigator !== "undefined" && typeof navigator.share === "function");
    setHasOld(!!remembered(trip.id));
  }, [open, trip.id]);

  const run = useCallback(async (job: () => Promise<Omit<ShareResult, "share"> & { share?: ShareLink }>) => {
    setBusy(true); setError(null);
    try {
      const r = await job();
      setResult(r); remember(trip.id, tokenOf(r.url)); setHasOld(true); onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && hasOld) { remember(trip.id, null); setHasOld(false); }
      setError(e instanceof ApiError ? e.message : "Couldn't make the link. Try again.");
    } finally { setBusy(false); }
  }, [trip.id, hasOld, onChanged]);

  const create = () => run(() => endpoints.createShare(trip.id, { show_budget: showBudget, include_split: split, expires: never ? "never" : "default" }));
  const again = () => run(() => endpoints.shareText(trip.id, remembered(trip.id) ?? ""));

  async function copy(what: string, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(what); setTimeout(() => setCopied(null), 1800); }
    catch { setError("Couldn't copy. Select the text and copy it."); }
  }
  async function nativeShare() {
    if (!result) return;
    try { await navigator.share({ title: trip.title, text: result.text }); } catch { /* the person closed the share sheet */ }
  }

  return (
    <Modal open={open} onClose={onClose} title="Share this trip" side>
      <div className="space-y-6">
        {!result && (
          <>
            <p className="text-[15px] text-ink-soft">
              Anyone with the link can see this plan. They can&apos;t see your email, phone number or anything you haven&apos;t chosen below.
              You send it yourself, from your own WhatsApp or app.
            </p>
            <fieldset className="space-y-3">
              <legend className="mb-1 text-sm font-semibold">What they see</legend>
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-line p-3">
                <input type="checkbox" className="mt-1 h-4 w-4" checked={showBudget} onChange={(e) => setShowBudget(e.target.checked)} />
                <span><span className="block font-semibold">Show budget</span>
                  <span className="text-[13px] text-ink-mute">Your budget and what&apos;s left. Off by default: it shows what you can spend. Stop costs and the total are always shown, with their labels.</span></span>
              </label>
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-line p-3">
                <input type="checkbox" className="mt-1 h-4 w-4" checked={split} onChange={(e) => setSplit(e.target.checked)} />
                <span><span className="block font-semibold">Plan + split</span>
                  <span className="text-[13px] text-ink-mute">Also show who paid what and who owes whom (names and amounts only, never UPI IDs).</span></span>
              </label>
            </fieldset>
            <fieldset className="space-y-2">
              <legend className="mb-1 text-sm font-semibold">How long the link works</legend>
              {[[false, "30 days after the trip ends", "Recommended"], [true, "Until I stop it", "No expiry"]].map(([v, l, h]) => (
                <label key={String(l)} className="flex cursor-pointer items-center gap-3 rounded-xl border border-line p-3">
                  <input type="radio" name="expiry" checked={never === v} onChange={() => setNever(v as boolean)} />
                  <span className="font-semibold">{l as string}</span><span className="ml-auto text-[13px] text-ink-mute">{h as string}</span>
                </label>
              ))}
            </fieldset>
            {error && <p role="alert" className="text-sm font-medium text-danger">{error}</p>}
            <button className="btn-dark w-full py-3" disabled={busy} onClick={create}><Link2 className="h-4 w-4" aria-hidden />{busy ? "Making the link…" : "Create link"}</button>
            {hasOld && (
              <button className="btn-ghost w-full" disabled={busy} onClick={again}>Share my last link again</button>
            )}
          </>
        )}

        {result && (
          <>
            <div>
              <label htmlFor="share-url" className="mb-1 block text-sm font-semibold">Your link</label>
              <input id="share-url" readOnly className="field num" value={result.url} onFocus={(e) => e.currentTarget.select()} />
              <p className="mt-1.5 text-[13px] text-ink-mute">Keep it with the people you meant it for. You can stop it any time from Shared links.</p>
            </div>
            <div>
              <label htmlFor="share-text" className="mb-1 block text-sm font-semibold">Message</label>
              <textarea id="share-text" readOnly rows={10} className="field text-[14px] leading-relaxed" value={result.text} />
              <p className="mt-1.5 text-[13px] text-ink-mute">Made just now from up-to-date details. It isn&apos;t saved anywhere.</p>
            </div>
            {error && <p role="alert" className="text-sm font-medium text-danger">{error}</p>}
            <div className="grid gap-2 sm:grid-cols-2">
              <button className="btn-primary" disabled={pdfState !== "ready"} onClick={sharePdf}>
                <FileText className="h-4 w-4" aria-hidden />
                {pdfState === "making" ? "Making the PDF…" : pdfState === "failed" ? "PDF couldn't be made" : "Send as PDF"}
              </button>
              <button className="btn-ghost" disabled={pdfState !== "ready"}
                      onClick={() => { if (pdf) { savePdf(pdf); setPdfNote("PDF saved to your downloads."); } }}>
                <Download className="h-4 w-4" aria-hidden />Download PDF
              </button>
              {pdfNote && <p role="status" className="text-[13px] text-ink-soft sm:col-span-2">{pdfNote}</p>}
              {canShare && <button className="btn-dark sm:col-span-2" onClick={nativeShare}><Share2 className="h-4 w-4" aria-hidden />Share…</button>}
              {result.wa_me_url && (
                <a className="btn-dark sm:col-span-2" href={result.wa_me_url} target="_blank" rel="noopener noreferrer">
                  <MessageCircle className="h-4 w-4" aria-hidden />Send to my WhatsApp
                </a>
              )}
              <a className={result.wa_me_url ? "btn-ghost" : "btn-dark"} href={result.wa_url} target="_blank" rel="noopener noreferrer"><MessageCircle className="h-4 w-4" aria-hidden />Send to someone</a>
              <button className="btn-ghost" onClick={() => copy("text", result.text)}>{copied === "text" ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}{copied === "text" ? "Copied" : "Copy message"}</button>
              <button className="btn-ghost sm:col-span-2" onClick={() => copy("link", result.url)}>{copied === "link" ? <Check className="h-4 w-4" aria-hidden /> : <Link2 className="h-4 w-4" aria-hidden />}{copied === "link" ? "Copied" : "Copy link only"}</button>
            </div>
            <button className="text-sm font-semibold text-ink-soft underline underline-offset-4" onClick={() => setResult(null)}>Make another link</button>
          </>
        )}
      </div>
    </Modal>
  );
}

/** "Shared links": every active link of this trip, with what it shows, when it ends, and a Revoke button. */
export function SharedLinks({ tripId, refreshKey }: { tripId: string; refreshKey: number }) {
  const [links, setLinks] = useState<ShareLink[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setLinks((await endpoints.shares(tripId)).shares); setError(null); }
    catch (e) { setLinks(null); setError(e instanceof ApiError && e.status === 404 ? "off" : "Couldn't load your links."); }
  }, [tripId]);
  useEffect(() => { load(); }, [load, refreshKey]);

  async function change(fn: () => Promise<unknown>) {
    try { await fn(); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "That didn't work."); }
  }
  const revoke = (l: ShareLink) => {
    if (!window.confirm("Stop this link? Anyone who has it will no longer be able to open the plan.")) return;
    change(async () => { await endpoints.revokeShare(tripId, l.id); if (links && links.length <= 1) remember(tripId, null); });
  };

  if (error === "off") return null;
  return (
    <div>
      {error && <p role="alert" className="mb-2 text-sm text-danger">{error}</p>}
      {links && links.length === 0 && <p>No active links. Use Share to make one.</p>}
      <ul className="space-y-3">
        {(links ?? []).map((l) => (
          <li key={l.id} className="rounded-xl border border-line bg-haze/50 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="num font-semibold text-ink">…{l.prefix}</span>
              <button onClick={() => revoke(l)} className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[13px] font-semibold text-danger hover:bg-danger-soft">
                <Trash2 className="h-3.5 w-3.5" aria-hidden />Revoke
              </button>
            </div>
            <p className="mt-1 text-[13px] text-ink-mute">
              Made {day(l.created_at)} · {l.view_count} {l.view_count === 1 ? "view" : "views"} ·{" "}
              {l.expires_at ? `ends ${day(l.expires_at)}` : "no expiry"}
            </p>
            <p className="mt-1 flex flex-wrap gap-1.5 text-[12px] font-semibold">
              {l.show_budget && <span className="rounded-full bg-caution-soft px-2 py-0.5 text-caution">Budget shown</span>}
              {l.include_split && <span className="rounded-full bg-haze-2 px-2 py-0.5 text-ink-soft">Plan + split</span>}
            </p>
            <div className="mt-2 flex flex-wrap gap-2 text-[13px] font-semibold">
              <button className="underline underline-offset-4" onClick={() => change(() => endpoints.updateShare(tripId, l.id, { extend_days: 30 }))}>Extend 30 days</button>
              {l.expires_at
                ? <button className="underline underline-offset-4" onClick={() => change(() => endpoints.updateShare(tripId, l.id, { expires: "never" }))}>Remove expiry</button>
                : <button className="underline underline-offset-4" onClick={() => change(() => endpoints.updateShare(tripId, l.id, { expires: "default" }))}>Expire after the trip</button>}
              <button className="underline underline-offset-4" onClick={() => change(() => endpoints.updateShare(tripId, l.id, { show_budget: !l.show_budget }))}>
                {l.show_budget ? "Hide budget" : "Show budget"}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
