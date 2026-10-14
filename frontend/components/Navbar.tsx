"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Menu, X } from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import { useAuth } from "@/lib/auth";

const SECTIONS = [
  { id: "try", label: "Try it" },
  { id: "features", label: "Why Yatro" },
  { id: "pricing", label: "Pricing" },
];

export function Navbar() {
  const { user, ready, signOut } = useAuth();
  const pathname = usePathname();
  const onHome = pathname === "/";
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [progress, setProgress] = useState(0);
  const [active, setActive] = useState<string | null>(null);

  // The bar floats lightly at the top and firms up once you scroll; a thin line tracks reading progress.
  useEffect(() => {
    const onScroll = () => {
      const y = window.scrollY, max = document.documentElement.scrollHeight - window.innerHeight;
      setScrolled(y > 12);
      setProgress(max > 0 ? Math.min(1, y / max) : 0);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => { window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); };
  }, []);

  // On the landing page, highlight the section currently in view.
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

  // Sliding highlight that follows the pointer between links, and rests on the current one.
  const navRef = useRef<HTMLDivElement>(null);
  const [pill, setPill] = useState<{ x: number; w: number } | null>(null);
  function moveTo(el: HTMLElement | null) {
    const box = navRef.current;
    if (!box || !el) return setPill(null);
    const a = box.getBoundingClientRect(), b = el.getBoundingClientRect();
    setPill({ x: b.left - a.left, w: b.width });
  }
  function rest() {
    moveTo(navRef.current?.querySelector<HTMLElement>("[aria-current='true']") ?? null);
  }
  useEffect(rest, [active, pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-40 px-3 pt-3 md:px-5 md:pt-4">
        <nav aria-label="Main"
             className={`relative mx-auto flex h-16 max-w-6xl items-center gap-3 rounded-2xl border px-3 backdrop-blur-xl backdrop-saturate-150 transition-all duration-500 ease-out md:gap-4 md:rounded-full md:pl-6 md:pr-2.5 ${
               scrolled
                 ? "border-line/80 bg-paper/80 shadow-[0_12px_40px_-18px_rgb(var(--shadow)/0.45)]"
                 : "border-line/50 bg-paper/40 shadow-[0_8px_30px_-22px_rgb(var(--shadow)/0.35)]"}`}>
          <Link href="/" aria-label="Yatro home" className="group mr-auto shrink-0 rounded-lg px-1"><Logo /></Link>

          <div ref={navRef} onMouseLeave={rest}
               className="relative hidden items-center gap-1 md:flex">
            <span aria-hidden
                  className={`absolute inset-y-0 left-0 rounded-full bg-haze-2 transition-all duration-300 ease-out ${pill ? "opacity-100" : "opacity-0"}`}
                  style={{ transform: `translateX(${pill?.x ?? 0}px)`, width: pill?.w ?? 0 }} />
            {links.map((l) => (
              <Link key={l.href} href={l.href} aria-current={l.current ? "true" : undefined}
                    onMouseEnter={(e) => moveTo(e.currentTarget)} onFocus={(e) => moveTo(e.currentTarget)}
                    className={`relative rounded-full px-4 py-2 text-[14px] transition-colors duration-200 ${
                      l.current ? "font-semibold text-ink" : "font-medium text-ink-soft hover:text-ink"}`}>
                {l.label}
              </Link>
            ))}
          </div>

          <div className="ml-2 hidden items-center gap-2 md:flex">
            <ThemeToggle className="!h-10 !w-10 !rounded-full" />
            <span aria-hidden className="h-6 w-px bg-line" />
            {ready && (user
              ? <UserMenu />
              : <Link href="/login" className="rounded-full px-3.5 py-2 text-[14px] font-medium text-ink-soft transition-colors hover:bg-haze-2 hover:text-ink">Sign in</Link>)}
            <Link href="/plan" className="btn-primary shine group/cta !h-11 !rounded-full !py-0 !pl-5 !pr-3.5 !text-[14px]">
              Build my trip
              <span className="grid h-6 w-6 place-items-center rounded-full bg-[rgb(var(--on-signal)/0.12)] transition-transform duration-300 group-hover/cta:translate-x-0.5 group-hover/cta:-rotate-45">
                <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </span>
            </Link>
          </div>

          <div className="flex items-center gap-1 md:hidden">
            <ThemeToggle className="!rounded-full" />
            <button className="relative inline-grid h-10 w-10 place-items-center rounded-full border border-line bg-paper/80 text-ink transition-colors hover:bg-haze-2"
                    onClick={() => setOpen(!open)} aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="mobile-menu">
              <Menu className={`absolute h-5 w-5 transition-all duration-300 ${open ? "rotate-90 scale-50 opacity-0" : "opacity-100"}`} aria-hidden />
              <X className={`absolute h-5 w-5 transition-all duration-300 ${open ? "opacity-100" : "-rotate-90 scale-50 opacity-0"}`} aria-hidden />
            </button>
          </div>

          {/* Reading progress, drawn along the bottom edge of the bar once it has lifted off the page. */}
          <span aria-hidden className={`pointer-events-none absolute inset-x-8 -bottom-px h-[2px] overflow-hidden rounded-full transition-opacity duration-300 ${scrolled ? "opacity-100" : "opacity-0"}`}>
            <span className="block h-full origin-left bg-gradient-to-r from-signal via-signal to-spark" style={{ transform: `scaleX(${progress})` }} />
          </span>
        </nav>

        {open && (
          <div id="mobile-menu" className="sheet-in mx-auto mt-2 max-w-6xl overflow-hidden rounded-2xl border border-line bg-paper/95 p-2 shadow-[0_24px_60px_-20px_rgb(var(--shadow)/0.5)] backdrop-blur-xl md:hidden">
            <div className="flex flex-col">
              {links.map((l, i) => (
                <Link key={l.href} href={l.href} onClick={() => setOpen(false)} style={{ ["--d" as string]: `${i * 45}ms` }}
                      className={`fade-up flex items-center justify-between rounded-xl px-4 py-3.5 text-[18px] font-semibold transition-colors hover:bg-haze ${l.current ? "bg-haze text-ink" : "text-ink"}`}>
                  {l.label}
                  <ArrowRight className="h-4 w-4 text-ink-mute" aria-hidden />
                </Link>
              ))}
              <div className="my-2 h-px bg-line" />
              {user ? (
                <>
                  <div className="flex items-center gap-3 px-4 py-2">
                    <span className="grid h-9 w-9 place-items-center rounded-full bg-signal text-[13px] font-bold text-on-signal">{user.name[0]?.toUpperCase()}</span>
                    <div className="min-w-0">
                      <p className="truncate font-semibold text-ink">{user.name}</p>
                      <p className="truncate text-[14px] text-ink-mute">{user.email}</p>
                    </div>
                  </div>
                  <Link href="/account" onClick={() => setOpen(false)} className="rounded-xl px-4 py-3 font-medium text-ink hover:bg-haze">My account</Link>
                  {user.is_admin && <Link href="/admin" onClick={() => setOpen(false)} className="rounded-xl px-4 py-3 font-semibold text-signal-deep hover:bg-signal-soft/60">Admin console</Link>}
                  <button onClick={() => { setOpen(false); signOut(); }} className="rounded-xl px-4 py-3 text-left font-medium text-ink hover:bg-haze">Sign out</button>
                </>
              ) : (
                <Link href="/login" onClick={() => setOpen(false)} className="rounded-xl px-4 py-3 font-medium text-ink hover:bg-haze">Sign in</Link>
              )}
              <Link href="/plan" onClick={() => setOpen(false)} className="btn-primary mt-2 !rounded-xl !py-3.5">
                Build my trip <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
            </div>
          </div>
        )}
      </header>
      {open && <div className="fixed inset-0 z-30 bg-haze/60 backdrop-blur-sm md:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <div className="h-[76px] md:h-[84px]" aria-hidden />
    </>
  );
}
