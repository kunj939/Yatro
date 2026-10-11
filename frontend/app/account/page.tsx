"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Check } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Modal } from "@/components/Modal";
import { RequireAuth, safeNext } from "@/components/AuthPage";
import { ApiError, endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import { validUpi } from "@/lib/upi";
import { inr, plural } from "@/lib/format";

type Product = "plus" | "one_trip";

/** The person's OWN UPI ID, for payment requests in settle-up. Nobody else's is ever stored (ADR 0006). */
function UpiSection() {
  const { user, refresh } = useAuth();
  const [value, setValue] = useState(user?.upi_id ?? "");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setValue(user?.upi_id ?? ""), [user?.upi_id]);

  async function save(next: string | null) {
    setBusy(true); setMsg(null);
    try { await endpoints.setUpi(next); await refresh(); setMsg({ ok: true, text: next ? "Saved." : "Removed." }); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? (e.details?.[0]?.message ?? e.message) : "Couldn't save that." }); }
    finally { setBusy(false); }
  }
  const bad = value.trim() !== "" && !validUpi(value);
  return (
    <section id="upi" className="panel mt-6 p-6" aria-labelledby="upi-title">
      <h2 id="upi-title" className="text-lg font-bold">Your UPI ID</h2>
      <p className="mt-1 text-[15px] text-ink-soft">
        Optional. Lets you copy a payment request in a trip&apos;s settle-up. Only you can add it, it is never shown on a shared page,
        and Yatro never stores anyone else&apos;s UPI ID.
      </p>
      <form className="mt-4 flex flex-wrap items-start gap-3" onSubmit={(e) => { e.preventDefault(); if (!bad) save(value.trim() || null); }}>
        <div className="min-w-[220px] flex-1">
          <label htmlFor="upi-id" className="sr-only">Your UPI ID</label>
          <input id="upi-id" className="field" placeholder="name@bank" value={value} onChange={(e) => setValue(e.target.value)}
                 autoComplete="off" autoCapitalize="none" spellCheck={false} aria-invalid={bad} />
          {bad && <p className="mt-1 text-sm text-danger">That doesn&apos;t look like a UPI ID (like name@bank).</p>}
        </div>
        <button className="btn-dark" disabled={busy || bad || value.trim() === (user?.upi_id ?? "")}>Save</button>
        {user?.upi_id && <button type="button" className="btn-ghost" disabled={busy} onClick={() => save(null)}>Remove</button>}
      </form>
      {msg && <p role={msg.ok ? "status" : "alert"} className={`mt-3 text-sm font-medium ${msg.ok ? "text-teal-deep" : "text-danger"}`}>{msg.text}</p>}
    </section>
  );
}

/** Email me my plan when a new one is ready: on by default, the person's own choice (it only ever goes to their own verified address). */
function PlanEmailSection() {
  const { user, refresh } = useAuth();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const flags = useFlags();
  if (!user || !flags.planEmails) return null;  // no mail server (or an admin stopped plan emails): nothing to switch
  const on = user.email_plans !== false;
  async function toggle() {
    setBusy(true); setMsg(null);
    try { await endpoints.setNotifications(!on); await refresh(); }
    catch (e) { setMsg(e instanceof ApiError ? e.message : "Couldn't save that."); }
    finally { setBusy(false); }
  }
  return (
    <section id="plan-emails" className="panel mt-6 p-6" aria-labelledby="plan-emails-title">
      <h2 id="plan-emails-title" className="text-lg font-bold">Email my plans</h2>
      <p className="mt-1 text-[15px] text-ink-soft">
        When a new plan is ready we email it to <span className="font-semibold text-ink">{user.email}</span>, with every stop, the cost and a button to open it
        or send it to your WhatsApp. Only to you.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" role="switch" aria-checked={on} onClick={toggle} disabled={busy}
                className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors ${on ? "bg-signal" : "bg-haze-2"} disabled:opacity-60`}>
          <span className={`inline-block h-5 w-5 rounded-full bg-paper shadow transition-transform ${on ? "translate-x-6" : "translate-x-1"}`} />
          <span className="sr-only">Email me my plans</span>
        </button>
        <span className="text-[15px] font-semibold">{on ? "On" : "Off"}</span>
      </div>
      {msg && <p role="alert" className="mt-3 text-sm font-medium text-danger">{msg}</p>}
    </section>
  );
}

function Account() {
  const { user, quota, setQuota, signOut } = useAuth();
  const flags = useFlags();
  const params = useSearchParams();
  const router = useRouter();
  const [checkout, setCheckout] = useState<Product | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const next = params.get("next");

  useEffect(() => {
    const u = params.get("upgrade");
    if (u === "plus" || u === "one_trip") setCheckout(u);
  }, [params]);

  if (!user || !quota) return null;
  const price = (p: Product) => (p === "plus" ? quota.prices.plus_monthly_inr : quota.prices.one_trip_inr);

  async function pay(p: Product) {
    setBusy(true); setError(null);
    try {
      setQuota(await endpoints.upgrade(p));
      setCheckout(null);
      if (next) { router.push(safeNext(next)); return; }
      setMessage(p === "plus" ? "Plus is active. Plan as many trips as you like." : "One trip credit added.");
      router.replace("/account", { scroll: false });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Payment couldn't be completed.");
    } finally { setBusy(false); }
  }

  async function cancel() {
    if (!window.confirm("Cancel Plus? You keep trips you've already planned.")) return;
    try { setQuota(await endpoints.cancel()); setMessage("Plus cancelled."); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Couldn't cancel right now."); }
  }

  const plus = quota.plan === "plus";
  return (
    <div className="mx-auto max-w-3xl px-5 py-10 md:px-8">
      <h1 className="text-title font-bold">Account</h1>
      <p className="mt-1 text-ink-soft">{user.name}, {user.email}</p>
      {message && <p role="status" className="mt-5 rounded-xl bg-teal-soft px-4 py-3 font-medium text-teal-deep">{message}</p>}
      {error && !checkout && <p role="alert" className="mt-5 rounded-xl bg-danger-soft px-4 py-3 text-danger">{error}</p>}

      <section className="panel mt-8 p-6" aria-labelledby="plan-title">
        <h2 id="plan-title" className="text-lg font-bold">Your plan</h2>
        <div className="num mt-4 grid gap-4 sm:grid-cols-3">
          <div><p className="text-sm text-ink-mute">Plan</p><p className="text-xl font-bold">{plus ? "Plus" : "Free"}</p></div>
          <div><p className="text-sm text-ink-mute">Free plans left</p><p className="text-xl font-bold">{quota.free_plans_left} of {quota.free_plans_total}</p></div>
          <div><p className="text-sm text-ink-mute">Trip credits</p><p className="text-xl font-bold">{quota.trip_credits}</p></div>
        </div>
        {plus && quota.current_period_end && <p className="mt-4 text-sm text-ink-soft">Renews on {new Date(quota.current_period_end).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" })}.</p>}
        <p className="mt-4 text-sm text-ink-mute">Free plans are for life, not monthly. Tweaking a trip you&apos;ve already planned never uses one up.</p>
      </section>

      <section className="mt-6 grid gap-4 sm:grid-cols-2" aria-label="Upgrade options">
        <div className="panel flex flex-col p-6">
          <p className="font-bold">One trip</p>
          <p className="num mt-1 text-3xl font-bold">{inr(price("one_trip"))}</p>
          <p className="mt-2 flex-1 text-ink-soft">Plan one more trip, with unlimited changes to it.</p>
          <button className="btn-ghost mt-5" onClick={() => setCheckout("one_trip")}>Buy one trip</button>
        </div>
        <div className="flex flex-col rounded-2xl border-2 border-signal bg-paper p-6">
          <p className="font-bold">Plus</p>
          <p className="num mt-1 text-3xl font-bold">{inr(price("plus"))}<span className="text-base font-medium text-ink-mute"> /month</span></p>
          <ul className="mt-2 flex-1 space-y-1 text-ink-soft">
            {["Unlimited trip plans", "Group expense splitting", "Cancel any time"].map((f) => (
              <li key={f} className="flex items-center gap-2"><Check className="h-4 w-4 text-teal" aria-hidden />{f}</li>
            ))}
          </ul>
          {plus
            ? <button className="btn-ghost mt-5" onClick={cancel}>Cancel Plus</button>
            : <button className="btn-primary mt-5" onClick={() => setCheckout("plus")}>Upgrade to Plus</button>}
        </div>
      </section>

      <PlanEmailSection />

      {flags.upi && <UpiSection />}

      <button className="mt-10 text-sm font-semibold text-ink-soft underline underline-offset-4" onClick={signOut}>Sign out</button>

      <Modal open={!!checkout} onClose={() => { setCheckout(null); setError(null); }} title={checkout === "plus" ? "Upgrade to Plus" : "Buy one trip"}>
        {checkout && (
          <div>
            <p className="num text-3xl font-bold">{inr(price(checkout))}{checkout === "plus" && <span className="text-base font-medium text-ink-mute"> /month</span>}</p>
            <p className="mt-2 text-ink-soft">{checkout === "plus" ? "Unlimited trip plans while your subscription is active." : `Adds ${plural(1, "trip credit")} to your account.`}</p>
            {quota.payments_mode === "mock" && (
              <p className="mt-4 rounded-xl bg-demo-soft px-4 py-3 text-sm text-ink">Test checkout: payments run in mock mode, so no money is charged.</p>
            )}
            {error && <p role="alert" className="mt-4 text-sm text-danger">{error}</p>}
            <button className="btn-dark mt-6 w-full py-3" disabled={busy} onClick={() => pay(checkout)}>
              {busy ? "Processing…" : `Pay ${inr(price(checkout))}`}
            </button>
          </div>
        )}
      </Modal>
    </div>
  );
}

export default function AccountPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <RequireAuth note="Sign in to manage your plan."><Suspense><Account /></Suspense></RequireAuth>
    </main>
  );
}