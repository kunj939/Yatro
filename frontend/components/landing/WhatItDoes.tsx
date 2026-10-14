import Link from "next/link";
import { ArrowRight, BedDouble, Calculator, CalendarCheck, Landmark, PenLine, Search, Share2, Train, Utensils, Wallet, Wrench } from "lucide-react";
import { brand } from "@/lib/brand";
import { Reveal } from "../Reveal";

// "Why Yaitro", in plain words: what you give, what it does, what you get. Written for someone who has never heard of
// the product and opened the page cold (friends said the hero alone didn't tell them).
const STEPS = [
  { Icon: PenLine, title: "You tell us 3 things",
    text: "Where you're going, your dates, and your budget. Add who's coming (kids, grandparents, friends) and what you like." },
  { Icon: Search, title: `${brand.name} searches and does the maths`,
    text: "It checks real train and flight timings, distances, opening hours, fares, food and stay prices, and adds up every rupee." },
  { Icon: CalendarCheck, title: "You get a day-by-day plan",
    text: "Hour by hour: when to leave, how to get there, what to see, where to eat and sleep, and what each thing costs." },
];

const IN_EVERY_PLAN = [
  { Icon: Train, text: "Trains, flights, buses and cabs with real timings" },
  { Icon: Landmark, text: "Places worth seeing, open when you get there" },
  { Icon: Utensils, text: "Breakfast, lunch and dinner spots for your food choice" },
  { Icon: BedDouble, text: "Stays that fit the money you have" },
  { Icon: Wallet, text: "The full cost, item by item, and what's left over" },
  { Icon: Wrench, text: "If the budget is too tight, the quickest ways to fix it" },
  { Icon: Calculator, text: "Group expenses split so everyone knows who owes what" },
  { Icon: Share2, text: "Share it on WhatsApp or download it as a PDF" },
];

export function WhatItDoes() {
  return (
    <section id="features" className="py-20 md:py-24" aria-labelledby="what-heading">
      <div className="container-x">
        <Reveal className="mx-auto max-w-3xl text-center">
          <span className="eyebrow mx-auto">Why {brand.name}</span>
          <h2 id="what-heading" className="mt-5 text-title font-bold">
            Your whole trip, planned and priced <span className="accent highlight whitespace-nowrap">in one minute.</span>
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-lg leading-relaxed text-ink-soft">
            {brand.name} is an AI trip planner. Planning a trip usually means 20 open tabs and a calculator.
            {" "}{brand.name} does the searching and the maths for you, and hands you a plan you can actually follow.
          </p>
        </Reveal>

        <ol className="mt-12 grid gap-4 md:grid-cols-3">
          {STEPS.map(({ Icon, title, text }, i) => (
            <Reveal as="li" key={title} delay={i * 90}
                    className="spotlight relative rounded-2xl border border-line bg-paper/70 p-6 backdrop-blur md:p-7">
              <div className="flex items-center gap-3">
                <span className="grid h-11 w-11 place-items-center rounded-xl bg-signal text-on-signal"><Icon className="h-5 w-5" aria-hidden /></span>
                <span className="num text-[13px] font-bold uppercase tracking-[0.14em] text-ink-mute">Step {i + 1}</span>
              </div>
              <h3 className="mt-5 text-[20px] font-bold leading-snug text-ink">{title}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-ink-soft">{text}</p>
            </Reveal>
          ))}
        </ol>

        <Reveal delay={120} className="mt-6 rounded-2xl border border-line bg-paper/70 p-6 backdrop-blur md:p-8">
          <h3 className="text-[18px] font-bold text-ink">What you get in every plan</h3>
          <ul className="mt-5 grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
            {IN_EVERY_PLAN.map(({ Icon, text }) => (
              <li key={text} className="flex items-start gap-3 text-[15px] leading-snug text-ink">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-signal-soft text-signal-deep"><Icon className="h-4 w-4" aria-hidden /></span>
                {text}
              </li>
            ))}
          </ul>
        </Reveal>

        <Reveal delay={160} className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/plan" className="btn-primary shine group !rounded-full !px-6 !py-3">
            Plan my trip<ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1" aria-hidden />
          </Link>
          <Link href="/#try" className="btn-ghost !rounded-full !px-6 !py-3">Try it without signing up</Link>
        </Reveal>
      </div>
    </section>
  );
}
