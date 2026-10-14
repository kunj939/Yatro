"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Check } from "lucide-react";
import { Reveal } from "./Reveal";
import { brand } from "@/lib/brand";

// ADR 0013, on the pattern Spotify, Canva and Duolingo use: a free tier that renews every month (a reason to come
// back), Plus monthly or yearly (the year is the better deal and the default highlight), and a one-off pass for
// people who plan one trip a year.
type Billing = "monthly" | "yearly";
const YEARLY_PER_MONTH = Math.round(brand.plusYearly / 12);
const SAVING = Math.round((1 - brand.plusYearly / (brand.plusMonthly * 12)) * 100);

export function PricingSection() {
  const [billing, setBilling] = useState<Billing>("yearly");
  const yearly = billing === "yearly";
  const plans = [
    { key: "free", name: "Free", price: "₹0", unit: "", lead: "Take it for a spin, every month.",
      features: [`${brand.freePlans === 1 ? "1 new trip plan" : `${brand.freePlans} new trip plans`} every month`, "Unlimited changes to it", "PDF and WhatsApp sharing", "Invite friends: you both get free plans"],
      cta: "Start free", href: "/plan" },
    { key: "plus", name: "Plus", price: yearly ? `₹${brand.plusYearly}` : `₹${brand.plusMonthly}`, unit: yearly ? "/year" : "/month",
      note: yearly ? `About ₹${YEARLY_PER_MONTH} a month · save ${SAVING}%` : "Cancel any time",
      lead: "For people who are always on the move.",
      features: ["Unlimited trip plans", "Priority when Yaitro is busy", "Group expense split", "Everything in Free"],
      cta: "Get Plus", href: yearly ? "/account?upgrade=plus_yearly" : "/account?upgrade=plus", featured: true },
    { key: "one", name: "Trip pass", price: `₹${brand.tripPass}`, unit: "/trip", lead: "Just this one trip.",
      features: ["1 extra trip plan", "Unlimited changes to it", "No subscription"], cta: "Buy a pass", href: "/account?upgrade=one_trip" },
  ];

  return (
    <section id="pricing" className="py-24" aria-labelledby="pricing-heading">
      <div className="container-x">
        <Reveal className="text-center">
          <span className="eyebrow">Pricing</span>
          <h2 id="pricing-heading" className="mt-5 text-title font-bold">Cheaper than <span className="accent highlight">one wrong train ticket</span></h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-ink-soft">Changing a trip you&apos;ve already planned <span className="font-semibold text-ink">never uses up a plan</span>.</p>
          <div className="mt-7 inline-flex rounded-full border border-line bg-paper/80 p-1 text-[14px] font-semibold backdrop-blur" role="group" aria-label="Billing">
            {([["monthly", "Monthly"], ["yearly", "Yearly"]] as [Billing, string][]).map(([v, l]) => (
              <button key={v} type="button" aria-pressed={billing === v} onClick={() => setBilling(v)}
                      className={`rounded-full px-5 py-2 transition-colors ${billing === v ? "bg-signal text-on-signal" : "text-ink-mute hover:text-ink"}`}>
                {l}{v === "yearly" && <span className={`ml-1.5 text-[12px] ${billing === v ? "text-on-signal/80" : "text-signal-deep"}`}>−{SAVING}%</span>}
              </button>
            ))}
          </div>
        </Reveal>
        <div className="mt-12 grid items-stretch gap-5 lg:grid-cols-[1fr_1.12fr_1fr]">
          {plans.map((p, i) => (
            <Reveal key={p.key} delay={i * 100} className="flex min-w-0">
              {p.featured ? (
                // A gradient edge around the featured plan, drawn by a 1px padded wrapper.
                <div className="group relative flex w-full rounded-[20px] bg-gradient-to-b from-signal via-signal/40 to-spark/60 p-px shadow-glow transition-transform duration-300 hover:-translate-y-1 lg:-my-4">
                  <div className="flex w-full flex-col rounded-[19px] bg-[#0f1219] p-7 text-white lg:py-11">
                    <div className="flex items-center justify-between">
                      <p className="text-lg font-bold">{p.name}</p>
                      <span className="rounded-full bg-[rgb(190_255_74)] px-2.5 py-1 text-[12px] font-bold uppercase tracking-wider text-[#0b0d0c]">{yearly ? "Best value" : "Recommended"}</span>
                    </div>
                    <p className="mt-1 text-white/65">{p.lead}</p>
                    <p className="num mt-6"><span className="text-5xl font-extrabold">{p.price}</span><span className="text-white/60">{p.unit}</span></p>
                    <p className="num mt-1.5 text-[14px] text-[rgb(190_255_74)]">{p.note}</p>
                    <ul className="mt-6 flex-1 space-y-2.5">
                      {p.features.map((f) => <li key={f} className="flex gap-2.5"><Check className="mt-0.5 h-4 w-4 shrink-0 text-[rgb(190_255_74)]" aria-hidden />{f}</li>)}
                    </ul>
                    <Link href={p.href} className="shine group/b relative mt-8 inline-flex items-center justify-center gap-2 overflow-hidden rounded-lg bg-[rgb(190_255_74)] px-5 py-3 font-semibold text-[#0b0d0c] transition-all hover:-translate-y-px">
                      {p.cta}<ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover/b:translate-x-1" aria-hidden />
                    </Link>
                  </div>
                </div>
              ) : (
                <div className="spotlight card-hover flex w-full flex-col card rounded-[20px] p-7">
                  <p className="text-lg font-bold">{p.name}</p>
                  <p className="mt-1 text-ink-mute">{p.lead}</p>
                  <p className="num mt-6"><span className="text-5xl font-extrabold">{p.price}</span><span className="text-ink-mute">{p.unit}</span></p>
                  <ul className="mt-6 flex-1 space-y-2.5">
                    {p.features.map((f) => <li key={f} className="flex gap-2.5"><Check className="mt-0.5 h-4 w-4 shrink-0 text-teal" aria-hidden />{f}</li>)}
                  </ul>
                  <Link href={p.href} className="btn-ghost mt-8">{p.cta}</Link>
                </div>
              )}
            </Reveal>
          ))}
        </div>
        <p className="mt-8 text-center text-sm text-ink-mute">Payments are in test mode for now: upgrades work instantly and no card is charged.</p>
      </div>
    </section>
  );
}
