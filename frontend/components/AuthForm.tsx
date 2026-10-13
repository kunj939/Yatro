"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, Check, Eye, EyeOff, Gift, Mail, ShieldCheck, Smartphone } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ApiError, endpoints, type SentCode } from "@/lib/api";
import { brand } from "@/lib/brand";
import { OtpInput } from "./OtpInput";

type Mode = "login" | "register";
type Channel = "email" | "phone";

// Indian mobile numbers only for now: texts to other countries are what SMS-pumping fraud abuses, and each one is paid for.
const COUNTRIES: [string, string][] = [["+91", "India"]];
const DETAIL_FIELDS = ["name", "email", "phone", "password", "password_confirm"];

/** How strong a new password is, 0-4, for the meter under it. */
function strength(pw: string): number {
  if (!pw) return 0;
  let s = pw.length >= 8 ? 1 : 0;
  if (/[a-zA-Z]/.test(pw) && /[\d\W_]/.test(pw)) s++;
  if (pw.length >= 12) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw) && /[\W_]/.test(pw)) s++;
  return s;
}
const STRENGTH = ["", "Weak", "Fair", "Good", "Strong"];

function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-semibold">{label}</label>
      {children}
      {error ? <p className="mt-1 text-sm text-danger" role="alert">{error}</p> : hint && <div className="mt-1 text-sm text-ink-mute">{hint}</div>}
    </div>
  );
}

function PasswordInput({ id, value, onChange, show, onToggle, autoComplete, invalid }: {
  id: string; value: string; onChange: (v: string) => void; show: boolean; onToggle: () => void; autoComplete: string; invalid?: boolean;
}) {
  return (
    <div className="relative">
      <input id={id} type={show ? "text" : "password"} className={`field pr-11 ${invalid ? "!border-danger" : ""}`} value={value}
             onChange={(e) => onChange(e.target.value)} autoComplete={autoComplete} required aria-invalid={invalid || undefined} />
      <button type="button" onClick={onToggle} aria-label={show ? "Hide password" : "Show password"}
              className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-ink-mute transition-colors hover:text-ink">
        {show ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
      </button>
    </div>
  );
}

export function AuthForm({ initial = "register", onDone, note, onSwitch }: {
  initial?: Mode; onDone?: () => void; note?: string;
  /** When given (the sign-in page, with its tabs), switching between sign-in and sign-up is left to the page. */
  onSwitch?: (m: Mode) => void;
}) {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<Mode>(initial);
  const [step, setStep] = useState<"details" | "verify">("details");
  const [ident, setIdent] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [cc, setCc] = useState("+91");
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [sent, setSent] = useState<Partial<Record<Channel, SentCode & { to: string }>>>({});
  const [codes, setCodes] = useState<Record<Channel, string>>({ email: "", phone: "" });
  const [wait, setWait] = useState<Record<Channel, number>>({ email: 0, phone: 0 });
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  // a friend's invite (ADR 0012): ?ref=CODE on the link they sent, kept if the person wanders off and comes back
  const [refCode, setRefCode] = useState("");
  useEffect(() => {
    try {
      const fromLink = new URLSearchParams(window.location.search).get("ref");
      if (fromLink) window.localStorage.setItem("yatro.ref", fromLink);
      setRefCode((fromLink || window.localStorage.getItem("yatro.ref") || "").toUpperCase());
    } catch { /* storage unavailable: the field still works */ }
  }, []);

  const phone = `${cc}${mobile.replace(/\D/g, "").replace(/^0+/, "")}`;
  const targets: Record<Channel, string> = { email: email.trim().toLowerCase(), phone };
  const counting = wait.email > 0 || wait.phone > 0;

  // the resend countdowns
  useEffect(() => {
    if (!counting) return;
    const t = setInterval(() => setWait((w) => ({ email: Math.max(0, w.email - 1), phone: Math.max(0, w.phone - 1) })), 1000);
    return () => clearInterval(t);
  }, [counting]);

  function reset() { setError(null); setFieldErrors({}); }

  function fail(err: unknown) {
    if (!(err instanceof ApiError)) { setError("Something went wrong. Please try again."); return; }
    const fe: Record<string, string> = {};
    if (Array.isArray(err.details)) for (const d of err.details) fe[d.field] = String(d.message).replace(/^Value error, /, "");
    setFieldErrors(fe);
    setError(Object.keys(fe).length ? null : err.message);
    if (Object.keys(fe).some((f) => DETAIL_FIELDS.includes(f))) setStep("details");
  }

  function checkDetails(): Record<string, string> {
    const fe: Record<string, string> = {};
    const digits = mobile.replace(/\D/g, "").replace(/^0+/, "");
    if (!name.trim()) fe.name = "Enter your name.";
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) fe.email = "Enter a valid email address.";
    if (!/^[6-9]\d{9}$/.test(digits)) fe.phone = "Enter your 10-digit Indian mobile number.";
    if (password.length < 8 || /^\d+$/.test(password) || /^[a-zA-Z]+$/.test(password))
      fe.password = "At least 8 characters, with a letter and a number or symbol.";
    if (confirm !== password) fe.password_confirm = "The two passwords don't match.";
    return fe;
  }

  /** Send a code to one channel, unless one already went to this same address less than the resend wait ago. */
  async function send(channel: Channel, force = false) {
    const prev = sent[channel];
    if (!force && prev && prev.to === targets[channel]) return;
    const r = await endpoints.sendCode(channel, targets[channel]);
    setSent((s) => ({ ...s, [channel]: { ...r, to: targets[channel] } }));
    setCodes((c) => ({ ...c, [channel]: "" }));
    setWait((w) => ({ ...w, [channel]: r.resend_in }));
  }

  async function sendBoth(e: React.FormEvent) {
    e.preventDefault();
    reset();
    const fe = checkDetails();
    if (Object.keys(fe).length) { setFieldErrors(fe); return; }
    setBusy(true);
    // Only the email is checked with a code. The mobile number is kept for WhatsApp trip sharing later; one number per account.
    try { await send("email"); setStep("verify"); } catch (err) { fail(err); } finally { setBusy(false); }
  }

  async function resend(channel: Channel) {
    reset();
    try { await send(channel, true); } catch (err) { fail(err); }
  }

  async function createAccount(e: React.FormEvent) {
    e.preventDefault();
    reset();
    const fe: Record<string, string> = {};
    if (codes.email.length < 6) fe.email_code = "Enter the 6-digit code from the email.";
    if (Object.keys(fe).length) { setFieldErrors(fe); return; }
    setBusy(true);
    try {
      await signUp({ name: name.trim(), email: email.trim(), phone, password, password_confirm: confirm,
                     email_code: codes.email, ...(refCode.trim() ? { referral_code: refCode.trim() } : {}) });
      try { window.localStorage.removeItem("yatro.ref"); } catch { /* fine */ }
      onDone?.();
    } catch (err) { fail(err); } finally { setBusy(false); }
  }

  async function login(e: React.FormEvent) {
    e.preventDefault();
    reset();
    const fe: Record<string, string> = {};
    if (!ident.trim()) fe.email = "Enter your email or mobile number.";
    if (!password) fe.password = "Enter your password.";
    if (Object.keys(fe).length) { setFieldErrors(fe); return; }
    setBusy(true);
    try { await signIn(ident.trim(), password); onDone?.(); } catch (err) { fail(err); } finally { setBusy(false); }
  }

  function switchMode() {
    if (onSwitch) { onSwitch(mode === "register" ? "login" : "register"); return; }
    setMode(mode === "register" ? "login" : "register");
    setStep("details");
    reset();
  }

  const switcher = onSwitch ? null : (
    <p className="text-center text-sm text-ink-soft">
      {mode === "register" ? "Already have an account? " : `New to ${brand.name}? `}
      <button type="button" className="font-semibold text-ink underline underline-offset-4" onClick={switchMode}>
        {mode === "register" ? "Sign in" : "Create an account"}
      </button>
    </p>
  );
  const banner = error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">{error}</p>;

  // ---------------------------------------------------------------- sign in
  if (mode === "login") {
    return (
      <form onSubmit={login} className="space-y-4" noValidate>
        {note && <p className="rounded-xl bg-signal-soft px-4 py-3 text-[16px] text-ink">{note}</p>}
        <Field id="auth-ident" label="Email or mobile number" error={fieldErrors.email}>
          <input id="auth-ident" className="field" value={ident} onChange={(e) => setIdent(e.target.value)}
                 autoComplete="username" placeholder="you@example.com or 98765 43210" required />
        </Field>
        <Field id="auth-password" label="Password" error={fieldErrors.password}>
          <PasswordInput id="auth-password" value={password} onChange={setPassword} show={showPw} onToggle={() => setShowPw(!showPw)}
                         autoComplete="current-password" invalid={!!fieldErrors.password} />
        </Field>
        {banner}
        <button type="submit" className="btn-primary w-full !py-3" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {switcher}
      </form>
    );
  }

  const steps = (
    <ol className="mb-5 flex items-center gap-2 text-[13px] font-semibold" aria-label="Sign-up steps">
      {[["details", "Your details"], ["verify", "Verify"]].map(([key, label], i) => {
        const on = step === key, done = step === "verify" && key === "details";
        return (
          <li key={key} className="flex items-center gap-2">
            {i > 0 && <span className="h-px w-6 bg-line" aria-hidden />}
            <span className={`grid h-6 w-6 place-items-center rounded-full text-[12px] ${on ? "bg-signal text-on-signal" : done ? "bg-teal-soft text-teal" : "bg-haze-2 text-ink-mute"}`}>
              {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : i + 1}
            </span>
            <span className={on ? "text-ink" : "text-ink-mute"} aria-current={on ? "step" : undefined}>{label}</span>
          </li>
        );
      })}
    </ol>
  );

  // ---------------------------------------------------------------- sign up, step 2: the codes
  if (step === "verify") {
    const dev = sent.email?.dev_code;
    const block = (channel: Channel) => {
      const s = sent[channel];
      const Icon = channel === "email" ? Mail : Smartphone;
      const err = fieldErrors[`${channel}_code`];
      return (
        <div className="rounded-2xl border border-line bg-haze/50 p-4">
          <div className="mb-3 flex items-start justify-between gap-3">
            <p className="flex min-w-0 items-start gap-2.5">
              <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-paper text-ink-soft"><Icon className="h-4 w-4" aria-hidden /></span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold">{channel === "email" ? "Email code" : "SMS code"}</span>
                <span className="block truncate text-[13px] text-ink-mute">Sent to {s?.sent_to}</span>
              </span>
            </p>
            <button type="button" onClick={() => resend(channel)} disabled={wait[channel] > 0}
                    className="num shrink-0 text-[13px] font-semibold text-ink underline-offset-4 hover:underline disabled:text-ink-mute disabled:no-underline">
              {wait[channel] > 0 ? `Resend in ${wait[channel]}s` : "Resend"}
            </button>
          </div>
          <OtpInput label={channel === "email" ? "Email code" : "SMS code"} value={codes[channel]} invalid={!!err}
                    onChange={(v) => setCodes((c) => ({ ...c, [channel]: v }))} autoFocus={channel === "email"} />
          {err && <p className="mt-2 text-sm text-danger" role="alert">{err}</p>}
        </div>
      );
    };
    return (
      <form onSubmit={createAccount} className="space-y-4" noValidate>
        {steps}
        <p className="text-[14px] text-ink-soft">Enter the 6-digit code we just emailed you, to confirm the address is yours.</p>
        {block("email")}
        {dev && (
          <div className="rounded-xl border border-dashed border-caution/50 bg-caution-soft/40 px-3.5 py-3 text-[13px] text-ink-soft">
            <p><span className="font-semibold text-caution">Test mode:</span> email sending isn&apos;t set up on this server yet, so the code is shown here.</p>
            <button type="button" className="mt-1.5 font-semibold text-ink underline underline-offset-4"
                    onClick={() => setCodes((c) => ({ ...c, email: sent.email?.dev_code ?? "" }))}>
              Fill in {sent.email?.dev_code}
            </button>
          </div>
        )}
        {banner}
        <button type="submit" className="btn-primary w-full !py-3" disabled={busy}>
          <ShieldCheck className="h-4 w-4" aria-hidden />{busy ? "Checking…" : "Verify and create account"}
        </button>
        <button type="button" onClick={() => { setStep("details"); reset(); }}
                className="mx-auto flex items-center gap-1.5 text-sm font-semibold text-ink-soft hover:text-ink">
          <ArrowLeft className="h-4 w-4" aria-hidden />Change details
        </button>
      </form>
    );
  }

  // ---------------------------------------------------------------- sign up, step 1: details
  const st = strength(password);
  return (
    <form onSubmit={sendBoth} className="space-y-4" noValidate>
      {note && <p className="rounded-xl bg-signal-soft px-4 py-3 text-[16px] text-ink">{note}</p>}
      {steps}
      <Field id="auth-name" label="Your name" error={fieldErrors.name}>
        <input id="auth-name" className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
      </Field>
      <Field id="auth-email" label="Email" error={fieldErrors.email}>
        <input id="auth-email" type="email" className="field" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
      </Field>
      <Field id="auth-phone" label="Mobile number" error={fieldErrors.phone} hint="Indian numbers only. We'll use it to share your trip plans on WhatsApp. One number per account.">
        <div className="flex gap-2">
          <select aria-label="Country code" className="field !w-[122px] shrink-0 !px-2.5" value={cc} onChange={(e) => setCc(e.target.value)}>
            {COUNTRIES.map(([code, country]) => <option key={code} value={code}>{code} {country}</option>)}
          </select>
          <input id="auth-phone" type="tel" inputMode="tel" className="field min-w-0 flex-1" value={mobile} onChange={(e) => setMobile(e.target.value)}
                 autoComplete="tel-national" placeholder={cc === "+91" ? "98765 43210" : "Mobile number"} required />
        </div>
      </Field>
      <Field id="auth-password" label="Password" error={fieldErrors.password}
             hint={password ? (
               <span className="flex items-center gap-2">
                 <span className="flex gap-1" aria-hidden>
                   {[1, 2, 3, 4].map((n) => <span key={n} className={`h-1 w-7 rounded-full ${n <= st ? (st <= 1 ? "bg-danger" : st === 2 ? "bg-caution" : "bg-teal") : "bg-haze-2"}`} />)}
                 </span>
                 {STRENGTH[st]}
               </span>
             ) : "At least 8 characters, with a number or symbol."}>
        <PasswordInput id="auth-password" value={password} onChange={setPassword} show={showPw} onToggle={() => setShowPw(!showPw)}
                       autoComplete="new-password" invalid={!!fieldErrors.password} />
      </Field>
      <Field id="auth-confirm" label="Confirm password" error={fieldErrors.password_confirm}
             hint={confirm && confirm === password ? <span className="flex items-center gap-1.5 text-teal-deep"><Check className="h-3.5 w-3.5" aria-hidden />Passwords match</span> : undefined}>
        <PasswordInput id="auth-confirm" value={confirm} onChange={setConfirm} show={showPw} onToggle={() => setShowPw(!showPw)}
                       autoComplete="new-password" invalid={!!fieldErrors.password_confirm} />
      </Field>
      <Field id="auth-ref" label="Invite code (optional)"
             hint={refCode ? <span className="flex items-center gap-1.5 text-teal-deep"><Gift className="h-3.5 w-3.5" aria-hidden />A friend invited you: extra free plans are added when you sign up.</span> : "Got a code from a friend? You both get free trip plans."}>
        <input id="auth-ref" className="field num uppercase tracking-widest" value={refCode} maxLength={16}
               onChange={(e) => setRefCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))} autoComplete="off" placeholder="e.g. PRIYA7K3Q" />
      </Field>
      {banner}
      <button type="submit" className="btn-primary w-full !py-3" disabled={busy}>
        {busy ? "Sending the code…" : "Continue: verify my email"}
      </button>
      {switcher}
    </form>
  );
}
