"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import { useAuth } from "@/lib/auth";

const SECTIONS = [
  { id: "try", label: "Try it" },
  { id: "features", label: "Why Yatro" },
  { id: "pricing", label: "Pricing" },
];

/** A label that rolls up to a fresh copy of itself on hover. */
function Roll({ children }: { children: string }) {
  return (
    <span className="relative block h-[1.3em] overflow-hidden leading-[1.3em]">
      <span className="block transition-transform duration-500 ease-[cubic-bezier(.6,0,.2,1)] group-hover/l:-translate-y-1/2">
        {children}
        <span aria-hidden className="block">{children}</span>
      </span>
    </span>
  );
}

/**
 * The night island: one dark capsule in both themes, a comet of lime running round its edge. The links sit on a
 * metro line; a lit train rides to the section you're reading, and follows the pointer between stations.
 */
export function Navbar() {
  const { user, ready, signOut } = useAuth();
  const pathname = usePathname();
  const onHome = pathname === "/";
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // On the landing page, the train stops at the section currently in view.
  useEffect(() => {
    if (!onHome) return setActive(null);
    const els = SECTIONS.map((s) => document.getElementById(s.id)).filter(Boolean) as HTMLElement[];
    if (!els.length) return;
    const io = new IntersectionObserver((entries) => {
      const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (vis) setActive(vis.target.id);
    }, { rootMargin: "-40% 0px -50% 0px", threshold: [0, 0.25, 0.5] });
    els.forEach((el) => io.observe(el));
    const top = () => { if (window.scrollY < 200) setActive(null); };
    window.addEventListener("scroll", top, { passive: true });
    return () => { io.disconnect(); window.removeEventListener("scroll", top); };
  }, [onHome]);

  // The mobile sheet: Escape closes it, the page behind it doesn't scroll, and it closes on navigation.
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", esc);
    return () => { document.body.style.overflow = prev; document.removeEventListener("keydown", esc); };
  }, [open]);

  const links = [
    ...SECTIONS.map((s) => ({ href: `/#${s.id}`, label: s.label, current: onHome && active === s.id })),
    user ? { href: "/trips", label: "My trips", current: pathname.startsWith("/trips") }
         : { href: "/plan", label: "Plan", current: pathname === "/plan" },
  ];

  // Metro line geometry: station centres measured from the links, the train parked on the current one.
  const lineRef = useRef<HTMLDivElement>(null);
  const [stations, setStations] = useState<number[]>([]);
  const [train, setTrain] = useState<number | null>(null);
  function measure() {
    const box = lineRef.current;
    if (!box) return;
    const a = box.getBoundingClientRect();
    setStations(Array.from(box.querySelectorAll<HTMLElement>("[data-station]")).map((el) => {
      const b = el.getBoundingClientRect();
      return b.left - a.left + b.width / 2;
    }));
  }
  function rest() {
    const i = links.findIndex((l) => l.current);
    setTrain(i >= 0 ? i : null);
  }
  useLayoutEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [user, scrolled]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(rest, [active, pathname, user]); // eslint-disable-line react-hooks/exhaustive-deps

  const first = stations[0] ?? 0, last = stations[stations.length - 1] ?? 0;
  const at = train !== null ? stations[train] ?? first : first;

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-40 px-3 pt-3 md:px-5 md:pt-4">
        <nav aria-label="Main"
             className={`nav-island relative mx-auto flex items-center gap-3 rounded-full pl-4 pr-2 text-white transition-all duration-500 ease-out md:gap-4 md:pl-6 ${
               scrolled ? "h-14 max-w-5xl md:h-[60px]" : "h-14 max-w-6xl md:h-16"}`}>
          <Link href="/" aria-label="Yatro home" className="group mr-auto shrink-0 rounded-lg [&_.fill-signal]:fill-[rgb(190_255_74)] [&_.stroke-signal]:stroke-[rgb(190_255_74)]"><Logo light /></Link>

          <div ref={lineRef} onMouseLeave={rest} className="relative hidden h-full items-center md:flex">
            {/* the line, the part already travelled, then the train */}
            <span aria-hidden className="pointer-events-none absolute bottom-[9px] h-px bg-white/15" style={{ left: first, width: last - first }} />
            <span aria-hidden className="pointer-events-none absolute bottom-[9px] h-px bg-gradient-to-r from-[rgb(190_255_74/0.2)] to-[rgb(190_255_74)] transition-all duration-500 ease-[cubic-bezier(.6,0,.2,1)]"
                  style={{ left: first, width: Math.max(0, at - first) }} />
            {stations.map((x, i) => (
              <span key={i} aria-hidden style={{ left: x }}
                    className={`pointer-events-none absolute bottom-[7px] h-[5px] w-[5px] -translate-x-1/2 rounded-full transition-colors duration-500 ${
                      train !== null && i <= train ? "bg-[rgb(190_255_74)]" : "bg-white/30"}`} />
            ))}
            <span aria-hidden style={{ left: at }}
                  className={`nav-train pointer-events-none absolute bottom-[5px] h-[9px] w-[9px] -translate-x-1/2 rounded-full bg-[rgb(190_255_74)] transition-all duration-500 ease-[cubic-bezier(.6,0,.2,1)] ${
                    train !== null ? "opacity-100" : "scale-50 opacity-0"}`} />

            {links.map((l, i) => (
              <Link key={l.href} href={l.href} aria-current={l.current ? "true" : undefined} data-station
                    onMouseEnter={() => setTrain(i)} onFocus={() => setTrain(i)}
                    className={`group/l relative px-3.5 text-[14px] font-medium transition-colors duration-300 lg:px-4 ${
                      l.current ? "text-white" : "text-white/60 hover:text-white"}`}>
                <Roll>{l.label}</Roll>
              </Link>
            ))}
          </div>

          <div className="ml-1 hidden items-center gap-1 md:flex">
            <ThemeToggle className="!h-10 !w-10 !rounded-full !text-white/70 hover:!bg-white/10 hover:!text-white" />
            {ready && (user
              ? <UserMenu dark />
              : <Link href="/login" className="group/l rounded-full px-3.5 py-2 text-[14px] font-medium text-white/70 transition-colors hover:bg-white/10 hover:text-white"><Roll>Sign in</Roll></Link>)}
            <Link href="/plan"
                  className={`group/cta group/l shine relative ml-1 inline-flex items-center gap-2 overflow-hidden rounded-full bg-[rgb(190_255_74)] pl-5 pr-1.5 text-[14px] font-semibold text-[#0b0d0c] shadow-[0_0_0_4px_rgb(190_255_74/0.12),0_10px_30px_-8px_rgb(190_255_74/0.6)] transition-all duration-500 hover:shadow-[0_0_0_6px_rgb(190_255_74/0.18),0_14px_36px_-8px_rgb(190_255_74/0.75)] ${
                    scrolled ? "h-10 md:h-11" : "h-11 md:h-12"}`}>
              <Roll>Build my trip</Roll>
              <span className="grid h-8 w-8 place-items-center rounded-full bg-[#0b0d0c] text-[rgb(190_255_74)] transition-transform duration-500 ease-[cubic-bezier(.6,0,.2,1)] group-hover/cta:rotate-45">
                <ArrowUpRight className="h-4 w-4" aria-hidden />
              </span>
            </Link>
          </div>

          <div className="flex items-center gap-1 md:hidden">
            <ThemeToggle className="!h-10 !w-10 !rounded-full !text-white/70 hover:!bg-white/10 hover:!text-white" />
            <button className="flex h-10 items-center gap-2.5 rounded-full bg-white/10 pl-4 pr-3.5 text-[14px] font-semibold text-white transition-colors hover:bg-white/15"
                    onClick={() => setOpen(!open)} aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="mobile-menu">
              {open ? "Close" : "Menu"}
              <span aria-hidden className="relative block h-2.5 w-4">
                <span className={`absolute left-0 h-[2px] w-4 rounded-full bg-[rgb(190_255_74)] transition-all duration-300 ${open ? "top-1 rotate-45" : "top-0"}`} />
                <span className={`absolute left-0 h-[2px] rounded-full bg-[rgb(190_255_74)] transition-all duration-300 ${open ? "top-1 w-4 -rotate-45" : "top-2 w-2.5"}`} />
              </span>
            </button>
          </div>
        </nav>

        {open && (
          <div id="mobile-menu" className="nav-sheet sheet-in mx-auto mt-2 max-w-6xl overflow-hidden rounded-[28px] p-3 text-white md:hidden">
            {/* the same metro line, standing up: every link is a stop */}
            <ol className="relative px-2 pt-2">
              <span aria-hidden className="absolute bottom-7 left-[21px] top-7 w-px bg-white/15" />
              {links.map((l, i) => (
                <li key={l.href} className="fade-up" style={{ ["--d" as string]: `${i * 50}ms` }}>
                  <Link href={l.href} onClick={() => setOpen(false)} className="group relative flex items-center gap-4 rounded-2xl py-3 pl-1 pr-3">
                    <span aria-hidden className={`relative z-10 grid h-6 w-6 place-items-center rounded-full ring-4 ring-[#0d1016] ${
                      l.current ? "bg-[rgb(190_255_74)]" : "border border-white/25 bg-[#0d1016]"}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${l.current ? "bg-[#0b0d0c]" : "bg-white/40"}`} />
                    </span>
                    <span className="num w-6 text-[12px] font-semibold text-white/40">0{i + 1}</span>
                    <span className={`flex-1 text-[26px] font-bold leading-tight tracking-tight ${l.current ? "text-[rgb(190_255_74)]" : "text-white"}`}>{l.label}</span>
                    <ArrowRight className="h-5 w-5 -translate-x-1 text-white/30 transition-all group-hover:translate-x-0 group-hover:text-[rgb(190_255_74)]" aria-hidden />
                  </Link>
                </li>
              ))}
            </ol>
            <div className="mx-2 my-3 h-px bg-white/10" />
            {user ? (
              <div className="px-2">
                <div className="flex items-center gap-3 py-2">
                  <span className="grid h-10 w-10 place-items-center rounded-full bg-[rgb(190_255_74)] text-[14px] font-bold text-[#0b0d0c]">{user.name[0]?.toUpperCase()}</span>
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{user.name}</p>
                    <p className="truncate text-[14px] text-white/55">{user.email}</p>
                  </div>
                </div>
                <div className="mt-1 grid grid-cols-2 gap-2">
                  <Link href="/account" onClick={() => setOpen(false)} className="rounded-xl bg-white/[0.06] px-4 py-3 text-[15px] font-medium hover:bg-white/10">My account</Link>
                  <button onClick={() => { setOpen(false); signOut(); }} className="rounded-xl bg-white/[0.06] px-4 py-3 text-left text-[15px] font-medium hover:bg-white/10">Sign out</button>
                  {user.is_admin && <Link href="/admin" onClick={() => setOpen(false)} className="col-span-2 rounded-xl bg-[rgb(190_255_74/0.1)] px-4 py-3 text-[15px] font-semibold text-[rgb(190_255_74)] hover:bg-[rgb(190_255_74/0.16)]">Admin console</Link>}
                </div>
              </div>
            ) : (
              <Link href="/login" onClick={() => setOpen(false)} className="mx-2 block rounded-xl bg-white/[0.06] px-4 py-3 font-medium hover:bg-white/10">Sign in</Link>
            )}
            <Link href="/plan" onClick={() => setOpen(false)}
                  className="mt-3 flex items-center justify-between rounded-2xl bg-[rgb(190_255_74)] py-2 pl-5 pr-2 text-[17px] font-bold text-[#0b0d0c]">
              Build my trip
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-[#0b0d0c] text-[rgb(190_255_74)]"><ArrowUpRight className="h-5 w-5" aria-hidden /></span>
            </Link>
          </div>
        )}
      </header>
      {open && <div className="fixed inset-0 z-30 bg-[#05070b]/50 backdrop-blur-sm md:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <div className="h-[68px] md:h-[84px]" aria-hidden />
    </>
  );
}
