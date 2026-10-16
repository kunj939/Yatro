"use client";

import Image from "next/image";
import Link from "next/link";
import { ArrowRight, ArrowUp, ArrowUpRight, Mail, MapPin } from "lucide-react";
import { Logo, LogoMark, Wordmark } from "./Logo";
import { brand } from "@/lib/brand";
import { FOOTER_PHOTOS } from "@/lib/photos";

const COLUMNS = [
  { title: "Product", links: [["Plan my trip", "/plan"], ["Plan with friends", "/together/new"], ["Why Yaitro", "/#features"], ["Budget demo", "/#try"], ["Pricing", "/#pricing"]] },
  { title: "Account", links: [["Sign in", "/login"], ["Create account", "/register"], ["My trips", "/trips"], ["My account", "/account"]] },
];
const POPULAR = ["Agra", "Goa", "Rishikesh", "Udaipur", "Dubai", "Bali", "Kyoto", "Paris"];

export function Footer() {
  return (
    <footer className="relative mt-10 pb-6 text-ink-soft">
      <div className="container-x">
        {/* one premium card: the links on top, the places below with the wordmark across them */}
        <div className="relative isolate overflow-hidden rounded-[32px] border border-line bg-paper shadow-[0_1px_0_rgb(255_255_255/0.9)_inset,0_1px_2px_rgb(var(--shadow)/0.05),0_30px_80px_-48px_rgb(var(--shadow)/0.45)]">
          <div aria-hidden className="pointer-events-none absolute -left-24 -top-32 -z-10 h-80 w-80 rounded-full bg-signal/10 blur-3xl" />

          <div className="grid grid-cols-2 gap-x-6 gap-y-10 px-6 pb-10 pt-10 sm:px-10 md:pt-12 lg:grid-cols-[1.4fr_0.8fr_0.8fr_1.2fr] lg:gap-12">
            <div className="col-span-2 min-w-0 lg:col-span-1">
              <Link href="/" aria-label={`${brand.name} home`} className="group inline-block"><Logo /></Link>
              <p className="mt-4 max-w-sm text-[15px] leading-relaxed">{brand.tagline}. Plans built from verified data; anything estimated is labelled, so you know what to double-check before you book.</p>
              <div className="mt-6 flex flex-wrap gap-2">
                <Link href="/plan" className="btn-primary shine group !rounded-full !px-5 !py-2.5 !text-[14px]">
                  Plan my trip<ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                </Link>
                <a href={`mailto:${brand.supportEmail}`}
                   className="group inline-flex items-center gap-2 rounded-full border border-line bg-paper px-4 py-2.5 text-[14px] font-medium text-ink transition-all hover:-translate-y-px hover:border-signal/50">
                  <Mail className="h-4 w-4 text-signal" aria-hidden />{brand.supportEmail}
                  <ArrowUpRight className="h-3.5 w-3.5 text-ink-mute transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
                </a>
              </div>
            </div>

            {COLUMNS.map((c) => (
              <nav key={c.title} aria-label={c.title} className="flex flex-col gap-2.5 text-[15px]">
                <span className="mb-1 text-[12px] font-semibold uppercase tracking-[0.14em] text-ink-mute">{c.title}</span>
                {c.links.map(([label, href]) => (
                  <Link key={href} href={href} className="group inline-flex w-fit items-center gap-1 text-ink-soft transition-colors hover:text-ink">
                    <span className="relative">
                      {label}
                      <span aria-hidden className="absolute -bottom-0.5 left-0 h-px w-full origin-left scale-x-0 bg-signal transition-transform duration-300 group-hover:scale-x-100" />
                    </span>
                  </Link>
                ))}
              </nav>
            ))}

            <div className="col-span-2 min-w-0 lg:col-span-1">
              <span className="text-[12px] font-semibold uppercase tracking-[0.14em] text-ink-mute">Trending trips</span>
              <div className="mt-3.5 flex flex-wrap gap-2">
                {POPULAR.map((city) => (
                  <Link key={city} href={`/plan?to=${encodeURIComponent(city)}`}
                        className="group inline-flex items-center gap-1.5 rounded-full border border-line bg-paper px-3 py-1.5 text-[13.5px] font-medium text-ink transition-all hover:-translate-y-px hover:border-signal/50 hover:bg-signal-soft/50">
                    <MapPin className="h-3 w-3 text-signal transition-transform group-hover:scale-110" aria-hidden />{city}
                  </Link>
                ))}
              </div>
            </div>
          </div>

          {/* the places, taking turns behind the wordmark; the whole wordmark always shows (it used to be cut in half) */}
          <div aria-hidden className="pointer-events-none relative isolate grid h-[190px] select-none place-items-center overflow-hidden sm:h-[260px] lg:h-[320px]">
            {FOOTER_PHOTOS.map((p, i) => (
              <Image key={p.src} src={p.src} alt="" fill sizes="(min-width: 1280px) 1200px, 100vw" className="footer-slide photo-pop -z-10 object-cover"
                     style={{ animationDelay: `${i * 7}s`, objectPosition: "center 60%" }} />
            ))}
            <span className="absolute inset-0 -z-10 bg-gradient-to-b from-paper via-black/10 to-black/45" />
            <span className="absolute inset-x-0 bottom-0 -z-10 h-2/3 bg-gradient-to-t from-black/35 to-transparent" />
            <span className="flex items-center gap-[2.5vw] text-white drop-shadow-[0_8px_30px_rgba(0,0,0,0.35)] lg:gap-6">
              <LogoMark lime className="h-[clamp(3rem,9vw,7.5rem)] w-[clamp(3rem,9vw,7.5rem)]" />
              <Wordmark light className="h-[clamp(3.4rem,10vw,8.5rem)]" />
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-3 px-2 pt-5 text-[13px] sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} {brand.name}. Made for people who count every rupee.</p>
          <button type="button" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
                  className="group inline-flex w-fit items-center gap-2 rounded-full border border-line bg-paper px-4 py-2 font-medium text-ink transition-all hover:-translate-y-px hover:border-ink/30">
            Back to top <ArrowUp className="h-4 w-4 transition-transform duration-300 group-hover:-translate-y-0.5" aria-hidden />
          </button>
        </div>
      </div>
    </footer>
  );
}
