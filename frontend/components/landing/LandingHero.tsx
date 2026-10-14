import Link from "next/link";
import { BadgeCheck, Clock3, CreditCard, MapPin } from "lucide-react";
import { Navbar } from "../Navbar";
import { HeroSearch } from "./HeroSearch";
import { HeroStory } from "./HeroStory";
import { LiveBoard } from "./LiveBoard";
import { brand } from "@/lib/brand";

// India and abroad, alternating, so neither reads as an afterthought.
const DESTINATIONS = [
  ["Agra", "Uttar Pradesh"], ["Dubai", "UAE"], ["Jaipur", "Rajasthan"], ["Bali", "Indonesia"],
  ["Goa", "India"], ["Kyoto", "Japan"], ["Rishikesh", "Uttarakhand"], ["Istanbul", "Türkiye"],
  ["Varanasi", "Uttar Pradesh"], ["Singapore", "Singapore"], ["Udaipur", "Rajasthan"], ["Santorini", "Greece"],
  ["Munnar", "Kerala"], ["Bangkok", "Thailand"], ["Leh", "Ladakh"], ["Paris", "France"],
  ["Darjeeling", "West Bengal"], ["Kathmandu", "Nepal"], ["Hampi", "Karnataka"], ["Edinburgh", "Scotland"],
];

export function LandingHero() {
  return (
    // z-50: `isolate` makes this header its own stacking layer, which trapped the fixed navbar's z-index inside it,
    // so later sections (animated with transforms) scrolled OVER the navbar. Lifting the whole layer fixes that.
    <header className="relative isolate z-50">
      <Navbar />
      <div className="container-x relative grid gap-8 pb-10 pt-4 md:pb-12 md:pt-8 lg:grid-cols-[1fr_1fr] lg:items-center lg:gap-10 lg:pb-8 lg:pt-4">
        <div className="min-w-0">
          {/* The headline turns through six traveller stories (students, couples, families…); see HeroStory. */}
          <div className="fade-up" style={{ ["--d" as string]: "80ms" }}>
            <HeroStory />
          </div>
          {/* One box instead of two buttons: type the trip the way you'd text a friend. */}
          <div className="fade-up mt-5" style={{ ["--d" as string]: "240ms" }}>
            <HeroSearch />
          </div>
          <ul className="fade-up mt-4 flex flex-wrap gap-x-6 gap-y-2 text-[14px] text-ink-mute" style={{ ["--d" as string]: "320ms" }}>
            <li className="inline-flex items-center gap-1.5"><BadgeCheck className="h-4 w-4 text-teal" aria-hidden />{brand.freePlans} free plans a month</li>
            <li className="inline-flex items-center gap-1.5"><Clock3 className="h-4 w-4 text-teal" aria-hidden />Done in under a minute</li>
            <li className="inline-flex items-center gap-1.5"><CreditCard className="h-4 w-4 text-teal" aria-hidden />No card. No catch.</li>
          </ul>
        </div>

        <div className="fade-up min-w-0" style={{ ["--d" as string]: "200ms" }}>
          <LiveBoard />
        </div>
      </div>

      {/* Where people are heading: each pill opens the planner with that destination filled in. */}
      <div className="marquee-wrap mask-x relative overflow-hidden border-y border-line/70 bg-paper/40 py-4 backdrop-blur-sm">
        <p className="sr-only">Popular destinations</p>
        <ul className="marquee flex w-max gap-3">
          {[...DESTINATIONS, ...DESTINATIONS].map(([city, country], i) => (
            <li key={i} aria-hidden={i >= DESTINATIONS.length || undefined}>
              <Link href={`/plan?to=${encodeURIComponent(city)}`} tabIndex={i >= DESTINATIONS.length ? -1 : undefined}
                    className="group inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-line bg-paper px-4 py-2 text-[14px] font-medium text-ink transition-all duration-200 hover:-translate-y-0.5 hover:border-signal/50 hover:shadow-glow">
                <MapPin className="h-3.5 w-3.5 text-ink-mute transition-colors group-hover:text-signal" aria-hidden />
                {city}<span className="text-ink-mute">{country}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </header>
  );
}
