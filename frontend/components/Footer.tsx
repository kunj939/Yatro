"use client";

import Link from "next/link";
import { ArrowUp, ArrowUpRight, Mail, MapPin } from "lucide-react";
import { Logo } from "./Logo";
import { brand } from "@/lib/brand";

const COLUMNS = [
  { title: "Product", links: [["Plan my trip", "/plan"], ["Budget demo", "/#try"], ["Why Yaitro", "/#features"], ["Pricing", "/#pricing"]] },
  { title: "Account", links: [["Sign in", "/login"], ["Create account", "/register"], ["My trips", "/trips"], ["My account", "/account"]] },
];
const POPULAR = ["Agra", "Goa", "Rishikesh", "Udaipur", "Dubai", "Bali", "Kyoto", "Paris"];

export function Footer() {
  return (
    <footer className="relative mt-8 overflow-hidden border-t border-line bg-paper/50 text-ink-soft backdrop-blur">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-signal/60 to-transparent" />

      <div className="container-x grid gap-12 pb-10 pt-16 md:grid-cols-2 lg:grid-cols-[1.5fr_1fr_1fr_1.2fr]">
        <div className="min-w-0">
          <Link href="/" aria-label={`${brand.name} home`} className="group inline-block"><Logo /></Link>
          <p className="mt-4 max-w-sm text-[16px] leading-relaxed">{brand.tagline}. We build plans from verified data. Anything marked estimated or demo? Double-check it before you book.</p>
          <a href={`mailto:${brand.supportEmail}`}
             className="group mt-6 inline-flex items-center gap-2 rounded-full border border-line bg-paper px-4 py-2 text-[14px] font-medium text-ink transition-all hover:-translate-y-px hover:border-signal/50 hover:shadow-glow">
            <Mail className="h-4 w-4 text-signal" aria-hidden />{brand.supportEmail}
            <ArrowUpRight className="h-3.5 w-3.5 text-ink-mute transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
          </a>
        </div>

        {COLUMNS.map((c) => (
          <nav key={c.title} aria-label={c.title} className="flex flex-col gap-3 text-[16px]">
            <span className="text-[12px] font-semibold uppercase tracking-[0.14em] text-ink-mute">{c.title}</span>
            {c.links.map(([label, href]) => (
              <Link key={href} href={href} className="group inline-flex w-fit items-center gap-1 transition-colors hover:text-ink">
                <span className="relative">
                  {label}
                  <span aria-hidden className="absolute -bottom-0.5 left-0 h-px w-full origin-left scale-x-0 bg-signal transition-transform duration-300 group-hover:scale-x-100" />
                </span>
              </Link>
            ))}
          </nav>
        ))}

        <div className="min-w-0">
          <span className="text-[12px] font-semibold uppercase tracking-[0.14em] text-ink-mute">Trending trips</span>
          <div className="mt-3 flex flex-wrap gap-2">
            {POPULAR.map((city) => (
              <Link key={city} href={`/plan?to=${encodeURIComponent(city)}`}
                    className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[14px] text-ink transition-all hover:-translate-y-px hover:border-signal/50 hover:bg-signal-soft/50">
                <MapPin className="h-3 w-3 text-ink-mute" aria-hidden />{city}
              </Link>
            ))}
          </div>
        </div>
      </div>

      <div className="container-x">
        <div className="flex flex-col gap-4 border-t border-line py-6 text-sm sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p>© {new Date().getFullYear()} {brand.name}. Made for people who count every rupee.</p>
            <p className="mt-1 text-[12px] text-ink-mute">Data from Google Maps, Wikidata, Open-Meteo and Indian Railways timetables.</p>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
                    className="group inline-flex items-center gap-2 rounded-full border border-line px-4 py-2 font-medium text-ink transition-all hover:-translate-y-px hover:border-ink/30">
              Back to top <ArrowUp className="h-4 w-4 transition-transform duration-300 group-hover:-translate-y-0.5" aria-hidden />
            </button>
          </div>
        </div>
      </div>

      {/* Oversized wordmark that fades into the page edge — decorative only. */}
      <div aria-hidden className="pointer-events-none select-none overflow-hidden">
        <p className="container-x -mb-[0.22em] bg-gradient-to-b from-ink/[0.10] to-transparent bg-clip-text text-center wordmark text-[clamp(5.5rem,24vw,19rem)] leading-none text-transparent">
          {brand.name}
        </p>
      </div>
    </footer>
  );
}
