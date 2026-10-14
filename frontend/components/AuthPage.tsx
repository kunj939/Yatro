"use client";

import Link from "next/link";
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, BadgeCheck, Clock3, CreditCard, Lock, Route } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { brand } from "@/lib/brand";
import { AuthForm } from "./AuthForm";
import { Logo } from "./Logo";
import { LiveBoard } from "./landing/LiveBoard";

type Mode = "login" | "register";

/** Only same-site relative paths are accepted as a post-login destination. */
export function safeNext(next: string | null, fallback = "/trips"): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}

const COPY: Record<Mode, { title: string; lead: string }> = {
  login: { title: "Welcome back", lead: "Pick up where you left off. Sign in with your email or mobile number." },
  register: { title: "Create your account",
              lead: `Make an account. You get ${brand.freePlansText} every month, and we'll confirm your email with a quick code.` },
};

function Inner({ mode }: { mode: Mode }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const raw = useSearchParams().get("next");
  const next = safeNext(raw);
  useEffect(() => { if (ready && user) router.replace(next); }, [ready, user, next, router]);
  // The tabs change the address too, so the heading, the form and the URL always agree (and ?next= is kept).
  const go = (m: Mode) => router.replace(`/${m}${raw ? `?next=${encodeURIComponent(raw)}` : ""}`, { scroll: false });

  return (
    <main className="relative min-h-screen">
      <header className="container-x flex items-center justify-between py-5">
        <Link href="/" aria-label={`${brand.name} home`} className="rounded-lg px-1"><Logo /></Link>
        <Link href="/" className="inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-[14px] font-medium text-ink-soft transition-colors hover:bg-haze-2 hover:text-ink">
          <ArrowLeft className="h-4 w-4" aria-hidden />Back to home
        </Link>
      </header>

      <div className="container-x grid items-start gap-14 pb-16 pt-2 lg:grid-cols-[minmax(0,1fr)_440px] lg:pt-6">
        {/* What you're signing up for: the same live trip board as the landing page. */}
        <section className="hidden min-w-0 lg:block" aria-label={`About ${brand.name}`}>
          <span className="eyebrow"><Route className="h-3.5 w-3.5 text-signal" aria-hidden />No guesswork · Goa to Geneva</span>
          <h2 className="mt-5 max-w-[15ch] text-title font-bold">Plan the whole trip <span className="accent highlight">in under a minute.</span></h2>
          <p className="mt-4 max-w-[44ch] text-lg leading-relaxed text-ink-soft">
            Travel, stay, sights and meals. Checked timings. Prices that fit your budget.
          </p>
          <ul className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-[14px] text-ink-mute">
            <li className="inline-flex items-center gap-1.5"><BadgeCheck className="h-4 w-4 text-teal" aria-hidden />{brand.freePlansText} a month</li>
            <li className="inline-flex items-center gap-1.5"><Clock3 className="h-4 w-4 text-teal" aria-hidden />Done in under a minute</li>
            <li className="inline-flex items-center gap-1.5"><CreditCard className="h-4 w-4 text-teal" aria-hidden />No card. No catch.</li>
          </ul>
          <div className="mt-8 max-w-[520px]"><LiveBoard /></div>
        </section>

        <div className="mx-auto w-full max-w-[440px]">
          <div className="spotlight overflow-hidden rounded-3xl border border-line bg-paper/85 p-6 shadow-[0_40px_100px_-40px_rgb(0_0_0/0.85)] backdrop-blur-xl sm:p-8">
            <span aria-hidden className="pointer-events-none absolute inset-x-10 top-0 h-px bg-gradient-to-r from-transparent via-signal/70 to-transparent" />
            <div role="tablist" aria-label="Sign in or create an account" className="grid grid-cols-2 gap-1 rounded-xl border border-line bg-haze/70 p-1">
              {(["login", "register"] as Mode[]).map((m) => (
                <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => mode !== m && go(m)}
                        className={`rounded-lg py-2.5 text-[14px] font-semibold transition-all duration-200 ${
                          mode === m ? "bg-paper text-ink shadow-[0_6px_16px_-8px_rgb(0_0_0/0.7)] ring-1 ring-line" : "text-ink-mute hover:text-ink"}`}>
                  {m === "login" ? "Sign in" : "Create account"}
                </button>
              ))}
            </div>
            <h1 className="mt-6 text-[28px] font-bold leading-tight">{COPY[mode].title}</h1>
            <p className="mb-6 mt-1.5 text-[15px] leading-relaxed text-ink-soft">{COPY[mode].lead}</p>
            <AuthForm key={mode} initial={mode} onSwitch={go} onDone={() => router.replace(next)} />
          </div>
          <p className="mt-4 flex items-center justify-center gap-1.5 text-[13px] text-ink-mute">
            <Lock className="h-3.5 w-3.5" aria-hidden />Your trips stay private. Always.
          </p>
        </div>
      </div>
    </main>
  );
}

export function AuthPage({ mode }: { mode: Mode }) {
  return <Suspense><Inner mode={mode} /></Suspense>;
}

/** Wraps pages that need a signed-in user. */
export function RequireAuth({ children, note }: { children: React.ReactNode; note?: string }) {
  const { user, ready } = useAuth();
  if (!ready) return <div className="grid min-h-[50vh] place-items-center text-ink-mute" aria-busy="true">Loading…</div>;
  if (!user) {
    return (
      <div className="mx-auto max-w-[440px] px-5 py-16">
        <div className="rounded-3xl border border-line bg-paper/85 p-6 shadow-[0_40px_100px_-40px_rgb(0_0_0/0.85)] backdrop-blur-xl sm:p-8">
          <h1 className="mb-2 text-2xl font-bold">Sign in to continue</h1>
          <p className="mb-6 text-ink-soft">{note ?? "Your trips are private to your account."}</p>
          <AuthForm initial="login" />
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
