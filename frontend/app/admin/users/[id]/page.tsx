"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Ban, CheckCircle2, ShieldCheck, Trash2 } from "lucide-react";
import { admin, type AdminUserDetail, type UserEdit } from "@/lib/admin";
import { useAdminMe } from "@/components/admin/AdminShell";
import { UserBadges } from "@/components/admin/UserBadges";
import { Card, ConfirmDialog, ErrorBox, Loading, Pill, ago, errorText, useLoad, when } from "@/components/admin/ui";

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5 text-[15px]"><dt className="text-ink-mute">{k}</dt><dd className="min-w-0 font-medium">{children}</dd></div>;
}

export default function AdminUserPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const me = useAdminMe();
  const { data: u, error, reload, setData } = useLoad(() => admin.user(id), [id]);
  const [dialog, setDialog] = useState<null | "suspend" | "delete" | "admin">(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [days, setDays] = useState("30");
  const [used, setUsed] = useState<string | null>(null);
  const [credits, setCredits] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);

  async function edit(body: UserEdit, done: string) {
    setMsg(null);
    try { const next = await admin.editUser(id, body); setData(next); setMsg({ ok: true, text: done }); setUsed(null); setCredits(null); setName(null); }
    catch (e) { setMsg({ ok: false, text: errorText(e) }); }
  }

  if (error) return <><Link href="/admin/users" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft hover:text-ink"><ArrowLeft className="h-4 w-4" />Users</Link><ErrorBox text={error} retry={reload} /></>;
  if (!u) return <Loading />;
  const owner = me?.role === "owner";
  const locked = u.is_owner;  // an owner's access is set on the server
  const usedNow = used ?? String(u.free_plans_used), creditsNow = credits ?? String(u.trip_credits), nameNow = name ?? u.name;

  return (
    <>
      <Link href="/admin/users" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft hover:text-ink"><ArrowLeft className="h-4 w-4" aria-hidden />Users</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-[28px] font-extrabold leading-tight tracking-tight md:text-[34px]"><span className="min-w-0 break-words">{u.name}</span><UserBadges u={u} /></h1>
          <p className="mt-1 break-all text-ink-soft">{u.email}{u.phone ? ` · ${u.phone}` : ""}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {u.is_active
            ? <button className="btn-ghost !text-danger" disabled={locked} onClick={() => setDialog("suspend")}><Ban className="h-4 w-4" aria-hidden />Suspend</button>
            : <button className="btn-primary" onClick={() => edit({ is_active: true }, "Account reactivated.")}><CheckCircle2 className="h-4 w-4" aria-hidden />Reactivate</button>}
        </div>
      </div>
      {msg && <p role={msg.ok ? "status" : "alert"} className={`mb-4 rounded-xl px-4 py-3 text-[15px] font-medium ${msg.ok ? "bg-teal-soft text-teal-deep" : "bg-danger-soft text-danger"}`}>{msg.text}</p>}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_1fr]">
        <div className="space-y-6">
          <Card title="Account">
            <dl className="divide-y divide-line">
              <Row k="Joined">{when(u.created_at)} <span className="text-ink-mute">({ago(u.created_at)})</span></Row>
              <Row k="Plan">{u.plan === "plus" ? <Pill tone="good">Plus{u.plan_ends ? ` until ${when(u.plan_ends)}` : ""}</Pill> : <Pill>Free</Pill>}</Row>
              <Row k="Free plans left"><span className="num">{u.free_plans_left}</span> <span className="text-ink-mute">({u.free_plans_used} used)</span></Row>
              <Row k="One-trip credits"><span className="num">{u.trip_credits}</span></Row>
              <Row k="Trips"><span className="num">{u.trips}</span> <span className="text-ink-mute">· last {ago(u.last_trip_at)}</span></Row>
              <Row k="Email"><button className="inline-flex items-center gap-1.5" onClick={() => edit({ email_verified: !u.email_verified }, u.email_verified ? "Email marked unverified." : "Email marked verified.")}>
                {u.email_verified ? <Pill tone="good">Verified</Pill> : <Pill tone="warn">Not verified</Pill>}<span className="text-xs text-ink-mute underline">change</span></button></Row>
              <Row k="Mobile"><span className="inline-flex items-center gap-1.5">{u.phone ?? "none"}{u.phone_verified ? <Pill tone="good">Code-verified</Pill> : <Pill>On file</Pill>}</span></Row>
            </dl>
          </Card>

          <Card title="Plan and plans">
            <div className="space-y-5">
              <div>
                <p className="mb-2 text-sm font-semibold">Plan</p>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2 text-[14px]">Plus for
                    <input className="field !w-20 !py-2 text-center" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} aria-label="Days of Plus" /> days</label>
                  <button className="btn-primary !py-2" disabled={!Number(days)} onClick={() => edit({ plan: "plus", plus_days: Math.min(3650, Number(days)) }, `Plus given for ${days} days.`)}>Give Plus</button>
                  {u.plan === "plus" && <button className="btn-ghost !py-2" onClick={() => edit({ plan: "free" }, "Back to the free plan.")}>End Plus</button>}
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="used" className="mb-1.5 block text-sm font-semibold">Free plans used</label>
                  <div className="flex gap-2">
                    <input id="used" className="field num !py-2" inputMode="numeric" value={usedNow} onChange={(e) => setUsed(e.target.value.replace(/\D/g, ""))} />
                    <button className="btn-ghost !py-2" disabled={usedNow === "" || Number(usedNow) === u.free_plans_used} onClick={() => edit({ free_plans_used: Number(usedNow) }, "Free plans updated.")}>Save</button>
                  </div>
                  <button className="mt-1.5 text-[13px] font-semibold text-signal-deep underline-offset-4 hover:underline" disabled={u.free_plans_used === 0} onClick={() => edit({ free_plans_used: 0 }, "All free plans given back.")}>Give all free plans back</button>
                </div>
                <div>
                  <label htmlFor="credits" className="mb-1.5 block text-sm font-semibold">One-trip credits</label>
                  <div className="flex gap-2">
                    <input id="credits" className="field num !py-2" inputMode="numeric" value={creditsNow} onChange={(e) => setCredits(e.target.value.replace(/\D/g, ""))} />
                    <button className="btn-ghost !py-2" disabled={creditsNow === "" || Number(creditsNow) === u.trip_credits} onClick={() => edit({ trip_credits: Number(creditsNow) }, "Credits updated.")}>Save</button>
                  </div>
                </div>
              </div>
              <div>
                <label htmlFor="uname" className="mb-1.5 block text-sm font-semibold">Name</label>
                <div className="flex gap-2">
                  <input id="uname" className="field !py-2" maxLength={80} value={nameNow} onChange={(e) => setName(e.target.value)} />
                  <button className="btn-ghost !py-2" disabled={!nameNow.trim() || nameNow.trim() === u.name} onClick={() => edit({ name: nameNow.trim() }, "Name updated.")}>Save</button>
                </div>
              </div>
            </div>
          </Card>

          <Card title="Access and danger zone">
            <div className="space-y-4">
              {owner && !locked && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-haze/60 p-4">
                  <div className="min-w-0"><p className="font-semibold">Admin access</p><p className="text-[14px] text-ink-soft">Admins can use this console, but can&apos;t make other admins.</p></div>
                  <button className="btn-ghost" onClick={() => setDialog("admin")}><ShieldCheck className="h-4 w-4" aria-hidden />{u.is_admin ? "Remove admin" : "Make admin"}</button>
                </div>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-danger/30 bg-danger-soft/50 p-4">
                <div className="min-w-0"><p className="font-semibold text-danger">Delete account</p><p className="text-[14px] text-ink-soft">Removes the account and all {u.trips} trip{u.trips === 1 ? "" : "s"}. This can&apos;t be undone.</p></div>
                <button className="btn bg-danger text-white hover:brightness-110 disabled:opacity-40" disabled={locked} onClick={() => setDialog("delete")}><Trash2 className="h-4 w-4" aria-hidden />Delete</button>
              </div>
              {locked && <p className="text-[13px] text-ink-mute">This is an owner (ADMIN_EMAILS on the server). Owners can&apos;t be suspended or deleted from here.</p>}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Recent trips">
            {u.recent_trips.length === 0 ? <p className="text-ink-mute">No trips yet.</p> : (
              <ul className="divide-y divide-line">
                {u.recent_trips.map((t) => (
                  <li key={t.id}>
                    <Link href={`/admin/trips/${t.id}`} className="flex items-center justify-between gap-3 py-3 hover:text-signal-deep">
                      <span className="min-w-0"><span className="block truncate font-semibold">{t.title}</span><span className="block truncate text-[13px] text-ink-mute">{t.start_date} · {t.plan_count} plan{t.plan_count === 1 ? "" : "s"} · {ago(t.created_at)}</span></span>
                      <Pill tone={t.status === "ready" ? "good" : "neutral"}>{t.status}</Pill>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Recent plan runs">
            {u.recent_plans.length === 0 ? <p className="text-ink-mute">No plans yet.</p> : (
              <ul className="divide-y divide-line text-[14px]">
                {u.recent_plans.map((j) => (
                  <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                    <span><Pill tone={j.state === "done" ? "good" : j.state === "refunded" || j.state === "failed" ? "bad" : "neutral"}>{j.state}</Pill> <span className="ml-1 text-ink-soft">{j.kind}{j.result ? ` · ${j.result}` : ""}</span></span>
                    <span className="num text-ink-mute">{j.seconds ? `${j.seconds}s · ` : ""}{ago(j.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <ConfirmDialog open={dialog === "suspend"} title="Suspend this account?" confirmLabel="Suspend" danger onClose={() => setDialog(null)}
                     onConfirm={async (reason) => { const next = await admin.editUser(id, { is_active: false, reason }); setData(next); setMsg({ ok: true, text: "Account suspended. They are signed out of every device." }); }}>
        {u.name} won&apos;t be able to sign in or use the app until you reactivate them. Their trips are kept.
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "admin"} title={u.is_admin ? "Remove admin access?" : "Make this person an admin?"} confirmLabel={u.is_admin ? "Remove admin" : "Make admin"} onClose={() => setDialog(null)}
                     onConfirm={async (reason) => { const next = await admin.editUser(id, { is_admin: !u.is_admin, reason }); setData(next); setMsg({ ok: true, text: u.is_admin ? "Admin access removed." : "They are now an admin." }); }}>
        {u.is_admin ? "They will no longer be able to open the admin console." : "They will be able to see every account and trip, change plans, and stop or restart parts of the service."}
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "delete"} title="Delete this account?" confirmLabel="Delete forever" danger typeToConfirm={u.email} onClose={() => setDialog(null)}
                     onConfirm={async (reason, typed) => { await admin.deleteUser(id, typed, reason); router.replace("/admin/users"); }}>
        This permanently deletes {u.name}&apos;s account, trips, plans and share links.
      </ConfirmDialog>
    </>
  );
}
