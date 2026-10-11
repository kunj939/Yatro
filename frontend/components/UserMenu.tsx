"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, LogOut, Settings, ShieldCheck, Ticket } from "lucide-react";
import { useAuth } from "@/lib/auth";

/** Who you're signed in as, what's left on your plan, and where to change it — rather than just a first name. */
export function UserMenu({ dark = false }: { dark?: boolean }) {
  const { user, quota, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  if (!user) return null;
  const initials = user.name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const plan = quota?.plan === "plus" ? "Plus" : "Free";
  const left = quota
    ? quota.plan === "plus" ? "Unlimited plans"
      : `${quota.free_plans_left} of ${quota.free_plans_total} free plans left${quota.trip_credits ? ` · ${quota.trip_credits} bought` : ""}`
    : null;

  return (
    <div className="relative" ref={box}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="menu"
              className={`flex items-center gap-2 rounded-full py-1 pl-1 pr-2.5 transition-colors ${
                dark ? "text-white/80 hover:bg-white/10" : "text-ink-soft hover:bg-haze-2"}`}>
        <span className="grid h-7 w-7 place-items-center rounded-full bg-signal text-[12px] font-bold text-on-signal ring-2 ring-signal/25">
          {initials}
        </span>
        <span className="hidden text-[14px] font-medium lg:inline">{user.name.split(" ")[0]}</span>
        <ChevronDown className={`h-4 w-4 transition-transform duration-200 ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>

      {open && (
        <div role="menu" className="sheet-in absolute right-0 z-40 mt-2 w-72 origin-top-right rounded-2xl border border-line bg-paper/95 p-1.5 shadow-[0_24px_60px_-20px_rgb(var(--shadow)/0.5)] backdrop-blur-xl">
          <div className="px-3 py-2.5">
            <p className="truncate font-semibold text-ink">{user.name}</p>
            <p className="truncate text-[14px] text-ink-mute">{user.email}</p>
            <p className="mt-2.5 inline-flex items-center gap-1.5 rounded-full bg-signal-soft px-2.5 py-1 text-[13px] font-semibold text-signal">
              {plan} plan
            </p>
            {left && <p className="num mt-2 text-[13px] text-ink-soft">{left}</p>}
          </div>
          <div className="my-1 h-px bg-line" />
          <Link href="/trips" role="menuitem" onClick={() => setOpen(false)} className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[14px] font-medium text-ink hover:bg-haze">
            <Ticket className="h-4 w-4 text-ink-mute" aria-hidden />My trips
          </Link>
          <Link href="/account" role="menuitem" onClick={() => setOpen(false)} className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[14px] font-medium text-ink hover:bg-haze">
            <Settings className="h-4 w-4 text-ink-mute" aria-hidden />My account
          </Link>
          {user.is_admin && (
            <Link href="/admin" role="menuitem" onClick={() => setOpen(false)} className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[14px] font-semibold text-signal-deep hover:bg-signal-soft/60">
              <ShieldCheck className="h-4 w-4" aria-hidden />Admin console
            </Link>
          )}
          <button role="menuitem" onClick={() => { setOpen(false); signOut(); }}
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-[14px] font-medium text-ink hover:bg-haze">
            <LogOut className="h-4 w-4 text-ink-mute" aria-hidden />Sign out
          </button>
        </div>
      )}
    </div>
  );
}