"use client";

import Link from "next/link";
import { notFound, usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LayoutDashboard, Map as MapIcon, ScrollText, ShieldCheck, SlidersHorizontal, Users, Workflow } from "lucide-react";
import { Navbar } from "../Navbar";
import { useAuth } from "@/lib/auth";
import { admin } from "@/lib/admin";
import { Loading } from "./ui";

const NAV = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/trips", label: "Trips", icon: MapIcon },
  { href: "/admin/plans", label: "Plans", icon: Workflow },
  { href: "/admin/controls", label: "Controls", icon: SlidersHorizontal },
  { href: "/admin/audit", label: "Audit log", icon: ScrollText },
];

export type AdminMe = { id: string; name: string; email: string; role: "owner" | "admin" };

/** The admin console's frame. The server is what enforces access (anyone else is answered with a 404); this only decides what to
 *  draw: signed out goes to sign-in, and anyone the server turns away sees the same "page not found" as for any other address. */
export function AdminShell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<AdminMe | null>(null);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    if (!ready) return;
    if (!user) { router.replace(`/login?next=${encodeURIComponent(pathname)}`); return; }
    let live = true;
    admin.me().then((m) => { if (live) setMe(m); }).catch(() => { if (live) setDenied(true); });
    return () => { live = false; };
  }, [ready, user, pathname, router]);

  if (denied) notFound();

  return (
    <main className="min-h-screen">
      <Navbar />
      <div className="container-x pb-20 pt-4 md:pt-6">
        {!me ? <Loading label="Checking access…" /> : (
          <div className="grid gap-6 lg:grid-cols-[210px_minmax(0,1fr)] lg:gap-8">
            <aside className="min-w-0">
              <p className="mb-3 hidden items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-mute lg:flex">
                <ShieldCheck className="h-4 w-4 text-signal" aria-hidden />Admin · {me.role}
              </p>
              <nav aria-label="Admin" className="grid grid-cols-3 gap-2 lg:grid-cols-1 lg:gap-1">
                {NAV.map(({ href, label, icon: Icon, exact }) => {
                  const on = exact ? pathname === href : pathname.startsWith(href);
                  return (
                    <Link key={href} href={href} aria-current={on ? "page" : undefined}
                          className={`flex min-w-0 items-center justify-center gap-2.5 rounded-xl border px-2 py-2.5 text-[14px] font-semibold transition-colors lg:justify-start lg:px-3.5 ${
                            on ? "border-signal bg-signal-soft text-ink" : "border-transparent text-ink-soft hover:bg-haze-2 hover:text-ink"}`}>
                      <Icon className={`h-4 w-4 shrink-0 ${on ? "text-signal-deep" : ""}`} aria-hidden /><span className="truncate">{label}</span>
                    </Link>
                  );
                })}
              </nav>
            </aside>
            <div className="min-w-0">{children}</div>
          </div>
        )}
      </div>
    </main>
  );
}

/** Pages read who is signed in here (the role decides which buttons they get). */
export function useAdminMe(): AdminMe | null {
  const { user } = useAuth();
  const [me, setMe] = useState<AdminMe | null>(null);
  useEffect(() => { if (user) admin.me().then(setMe).catch(() => setMe(null)); }, [user]);
  return me;
}
