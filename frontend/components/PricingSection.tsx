import Link from "next/link";
import { ArrowRight, Check } from "lucide-react";
import { Reveal } from "./Reveal";
import { brand } from "@/lib/brand";

const PLANS = [
  { key: "free", name: "Free", price: "₹0", unit: "", lead: "Take it for a spin.", features: [`${brand.freePlans} trip plans, lifetime`, "Unlimited changes to those trips", "Alternatives, budget and group split"], cta: "Start free", href: "/plan" },
  { key: "plus", name: "Plus", price: "₹99", unit: "/month", lead: "For people who are always on the move.", features: ["Unlimited trip plans", "Unlimited changes", "Everything in Free"], cta: "Get Plus", href: "/account?upgrade=plus", featured: true },
  { key: "one", name: "One trip", price: "₹49", unit: "/trip", lead: "Just this one trip.", features: ["1 extra trip plan", "Unlimited changes to it", "No subscription"], cta: "Buy one trip", href: "/account?upgrade=one_trip" },
];

export function PricingSection() {
  return (
    <section id="pricing" className="py-24" aria-labelledby="pricing-heading">
      <div className="container-x">
        <Reveal className="text-center">
          <span className="eyebrow">Pricing</span>
          <h2 id="pricing-heading" className="mt-5 text-title font-bold">Cheaper than <span className="accent highlight">one wrong train ticket</span></h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-ink-soft">Changing a trip you&apos;ve already planned <span className="font-semibold text-ink">never uses up a plan</span>.</p>
        </Reveal>
        <div className="mt-14 grid items-stretch gap-5 lg:grid-cols-[1fr_1.12fr_1fr]">
          {PLANS.map((p, i) => (
            <Reveal key={p.key} delay={i * 100} className="flex min-w-0">
              {p.featured ? (
                // A gradient edge around the featured plan, drawn by a 1px padded wrapper.
                <div className="group relative flex w-full rounded-[20px] bg-gradient-to-b from-signal via-signal/40 to-spark/60 p-px shadow-glow transition-transform duration-300 hover:-translate-y-1 lg:-my-4">
                  <div className="flex w-full flex-col rounded-[19px] bg-[#0f1219] p-7 text-white lg:py-11">
                    <div className="flex items-center justify-between">
                      <p className="text-lg font-bold">{p.name}</p>
                      <span className="rounded-full bg-[rgb(190_255_74)] px-2.5 py-1 text-[12px] font-bold uppercase tracking-wider text-[#0b0d0c]">Recommended</span>
                    </div>
                    <p className="mt-1 text-white/65">{p.lead}</p>
                    <p className="num mt-6"><span className="text-5xl font-extrabold">{p.price}</span><span className="text-white/60">{p.unit}</span></p>
                    <ul className="mt-6 flex-1 space-y-2.5">
                      {p.features.map((f) => <li key={f} className="flex gap-2.5"><Check className="mt-0.5 h-4 w-4 shrink-0 text-[rgb(190_255_74)]" aria-hidden />{f}</li>)}
                    </ul>
                    <Link href={p.href} className="shine group/b relative mt-8 inline-flex items-center justify-center gap-2 overflow-hidden rounded-lg bg-[rgb(190_255_74)] px-5 py-3 font-semibold text-[#0b0d0c] transition-all hover:-translate-y-px">
                      {p.cta}<ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover/b:translate-x-1" aria-hidden />
                    </Link>
                  </div>
                </div>
              ) : (
                <div className="spotlight card-hover flex w-full flex-col rounded-[20px] border border-line bg-paper/80 p-7 backdrop-blur">
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
