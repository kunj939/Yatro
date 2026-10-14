import Link from "next/link";
import Image from "next/image";
import { ArrowRight, BadgeCheck, Clock3, CreditCard } from "lucide-react";
import { Navbar } from "../Navbar";
import { HeroStory } from "./HeroStory";
import { LiveBoard } from "./LiveBoard";
import { brand } from "@/lib/brand";
import { DESTINATION_PHOTOS } from "@/lib/photos";


export function LandingHero() {
  return (
    // z-50: `isolate` makes this header its own stacking layer, which trapped the fixed navbar's z-index inside it,
    // so later sections (animated with transforms) scrolled OVER the navbar. Lifting the whole layer fixes that.
    <header className="relative isolate z-50">
      <Navbar />
      <div className="container-x relative grid gap-12 pb-14 pt-6 md:pb-16 md:pt-10 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:gap-16 lg:pb-14 lg:pt-10">
        <div className="min-w-0">
          {/* The headline turns through six traveller stories (students, couples, families…); see HeroStory. */}
          <div className="fade-up" style={{ ["--d" as string]: "80ms" }}>
            <HeroStory />
          </div>
          <div className="fade-up mt-8 flex flex-wrap items-center gap-3" style={{ ["--d" as string]: "240ms" }}>
            <Link href="/plan" className="btn-primary shine group !rounded-full !px-6 !py-3 !text-[16px]">
              Build my trip<ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1" aria-hidden />
            </Link>
            <Link href="/#try" className="btn-ghost !rounded-full !px-6 !py-3 !text-[16px]">Try the budget demo</Link>
          </div>
          <ul className="fade-up mt-6 flex flex-wrap gap-x-6 gap-y-2 text-[14px] text-ink-mute" style={{ ["--d" as string]: "320ms" }}>
            <li className="inline-flex items-center gap-1.5"><BadgeCheck className="h-4 w-4 text-teal" aria-hidden />{brand.freePlansText} a month</li>
            <li className="inline-flex items-center gap-1.5"><Clock3 className="h-4 w-4 text-teal" aria-hidden />Done in under a minute</li>
            <li className="inline-flex items-center gap-1.5"><CreditCard className="h-4 w-4 text-teal" aria-hidden />No card. No catch.</li>
          </ul>
        </div>

        <div className="fade-up min-w-0" style={{ ["--d" as string]: "200ms" }}>
          <LiveBoard />
        </div>
      </div>

      {/* Where people are heading: photo cards that drift past; each opens the planner with that place filled in. */}
      <div className="marquee-wrap mask-x relative overflow-hidden py-6">
        <p className="sr-only">Popular destinations</p>
        <ul className="marquee flex w-max gap-4">
          {[...DESTINATION_PHOTOS, ...DESTINATION_PHOTOS].map((d, i) => {
            const copy = i >= DESTINATION_PHOTOS.length;
            return (
              <li key={i} aria-hidden={copy || undefined}>
                <Link href={`/plan?to=${encodeURIComponent(d.city)}`} tabIndex={copy ? -1 : undefined}
                      className="card group relative block h-40 w-60 overflow-hidden !rounded-2xl transition-transform duration-300 hover:-translate-y-1">
                  <Image src={d.src} alt={copy ? "" : d.alt} fill sizes="240px" className="kenburns object-cover"
                         style={{ animationDelay: `${-(i % DESTINATION_PHOTOS.length) * 2}s` }} />
                  <span aria-hidden className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent" />
                  <span className="absolute inset-x-3.5 bottom-3 flex items-end justify-between gap-2 text-white">
                    <span>
                      <span className="block text-[16px] font-semibold leading-tight">{d.city}</span>
                      <span className="block text-[12px] text-white/75">{d.country}</span>
                    </span>
                    <span className="rounded-full bg-white/90 px-2.5 py-1 text-[11px] font-semibold text-[#10131a] opacity-0 transition-opacity duration-300 group-hover:opacity-100">Plan this</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </header>
  );
}
