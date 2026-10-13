"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Gift, MessageCircle } from "lucide-react";
import { endpoints } from "@/lib/api";
import type { ReferralCard } from "@/lib/types";

/** Invite friends (ADR 0012): your code and link, what you both get, and how it's going. Everyone gets the same
 *  offer, set live in the admin console. */
export function InviteCard() {
  const [card, setCard] = useState<ReferralCard | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => { endpoints.referrals().then(setCard).catch(() => setCard(null)); }, []);
  if (!card) return null;
  const r = card.rewards;
  const offer = [r.friend_plans ? `${r.friend_plans} free trip plan${r.friend_plans === 1 ? "" : "s"}` : null,
                 r.plus_discount_pct ? `${r.plus_discount_pct}% off their first month of Plus` : null].filter(Boolean).join(" and ");
  const message = `Planning a trip? Yatro plans the whole thing to fit your budget. Sign up with my code ${card.code}${offer ? ` and get ${offer}` : ""}: ${card.link}`;

  async function copy(what: string, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(what); setTimeout(() => setCopied(null), 1800); } catch { /* select by hand */ }
  }

  return (
    <section className="panel mt-6 p-6" aria-labelledby="invite-title">
      <h2 id="invite-title" className="flex items-center gap-2 text-lg font-bold"><Gift className="h-5 w-5 text-signal-deep" aria-hidden />Invite friends</h2>
      {!card.enabled ? (
        <p className="mt-2 text-ink-soft">Invites are paused for now. Your code still works once they're back.</p>
      ) : (
        <>
          <p className="mt-2 text-ink-soft">
            {offer ? <>Your friend gets <b className="text-ink">{offer}</b>. </> : null}
            {r.inviter_plans ? <>You get <b className="text-ink">{r.inviter_plans} free trip plan{r.inviter_plans === 1 ? "" : "s"}</b> when their first plan is ready.</> : null}
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="num rounded-xl border-2 border-dashed border-signal/60 bg-signal-soft/40 px-4 py-2 text-lg font-bold tracking-widest">{card.code}</span>
            <button type="button" className="btn-ghost !py-2" onClick={() => copy("link", card.link)}>
              {copied === "link" ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}{copied === "link" ? "Copied" : "Copy link"}
            </button>
            <a className="btn-primary !py-2" href={`https://wa.me/?text=${encodeURIComponent(message)}`} target="_blank" rel="noopener noreferrer">
              <MessageCircle className="h-4 w-4" aria-hidden />Share on WhatsApp
            </a>
          </div>
        </>
      )}
      <dl className="num mt-5 grid grid-cols-3 gap-3 text-center">
        <div className="rounded-xl bg-haze p-3"><dt className="text-[13px] text-ink-mute">Friends joined</dt><dd className="text-xl font-bold">{card.invited}</dd></div>
        <div className="rounded-xl bg-haze p-3"><dt className="text-[13px] text-ink-mute">Planned a trip</dt><dd className="text-xl font-bold">{card.rewarded}</dd></div>
        <div className="rounded-xl bg-haze p-3"><dt className="text-[13px] text-ink-mute">Plans you earned</dt><dd className="text-xl font-bold">{card.plans_earned}</dd></div>
      </dl>
      {card.invited_by && <p className="mt-4 text-sm text-ink-mute">You joined through {card.invited_by}&apos;s invite.</p>}
    </section>
  );
}
