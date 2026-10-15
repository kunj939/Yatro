import Image from "next/image";
import Link from "next/link";
import { ArrowRight, BedDouble, Calculator, CalendarCheck, Landmark, PenLine, Search, Share2, Train, Utensils, Wallet, Wrench } from "lucide-react";
import { brand } from "@/lib/brand";
import { Reveal } from "../Reveal";
import { DESTINATION_PHOTOS, TRAIN_PHOTO } from "@/lib/photos";

const photo = (city: string) => DESTINATION_PHOTOS.find((d) => d.city === city)!;

// "Why Yaitro", in plain words: what you give, what it does, what you get. Written for someone who has never heard of
// the product and opened the page cold (friends said the hero alone didn't tell them).
const STEPS = [
  { Icon: PenLine, img: photo("Leh"), title: "You tell us 3 things",
    text: "Where you're going, your dates, and your budget. Add who's coming (kids, grandparents, friends) and what you like." },
  { Icon: Search, img: TRAIN_PHOTO, title: `${brand.name} searches and does the maths`,
    text: "It checks real train and flight timings, distances, opening hours, fares, food and stay prices, and adds up every rupee." },
  { Icon: CalendarCheck, img: photo("Agra"), title: "You get a day-by-day plan",
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
            {brand.name} is a trip planner. Planning a trip usually means 20 open tabs and a calculator.
            {" "}{brand.name} does the searching and the maths for you, and hands you a plan you can actually follow.
          </p>
        </Reveal>

        <ol className="mt-12 grid gap-4 md:grid-cols-3">
          {STEPS.map(({ Icon, img, title, text }, i) => (
            <Reveal as="li" key={title} delay={i * 90} className="card card-hover relative overflow-hidden">
              <div className="relative h-48 overflow-hidden">
                <Image src={img.src} alt={img.alt} fill sizes="(min-width: 768px) 400px, 100vw" className="photo-pop kenburns object-cover"
                       style={{ animationDelay: `${-i * 5}s` }} />
                <span aria-hidden className="absolute inset-0 bg-gradient-to-t from-black/55 via-black/5 to-transparent" />
                <span className="absolute bottom-3 left-4 rounded-full bg-white/95 px-3 py-1 text-[12px] font-bold uppercase tracking-[0.12em] text-[#10131a]">Step {i + 1}</span>
                <span className="absolute bottom-3.5 right-4 text-[12.5px] font-semibold text-white/90 [text-shadow:0_1px_6px_rgba(0,0,0,0.5)]">
                  {img.city}{img.country ? `, ${img.country}` : ""}
                </span>
              </div>
              <div className="p-6">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-signal-soft text-signal-deep"><Icon className="h-5 w-5" aria-hidden /></span>
                <h3 className="mt-4 text-[20px] font-bold leading-snug text-ink">{title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed text-ink-soft">{text}</p>
              </div>
            </Reveal>
          ))}
        </ol>

        <Reveal delay={120} className="mt-6 card rounded-2xl p-6 md:p-8">
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
