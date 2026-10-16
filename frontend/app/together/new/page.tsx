"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, CalendarDays, Minus, Plus, Users, X } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { RequireAuth } from "@/components/AuthPage";
import { LocationInput, type LocationValue } from "@/components/wizard/LocationInput";
import { ApiError, endpoints } from "@/lib/api";

const STAYS = [["budget", "Budget"], ["tier2", "Comfort"], ["tier1", "Nice hotel"], ["premium", "Premium"], ["hostel", "Hostel"]] as const;
const iso = (d: Date) => d.toISOString().slice(0, 10);
const plusDays = (s: string, n: number) => { const d = new Date(`${s}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return iso(d); };

function NewGroup() {
  const router = useRouter();
  const today = iso(new Date());
  const [title, setTitle] = useState("");
  const [origin, setOrigin] = useState<LocationValue>({ name: "" });
  const [options, setOptions] = useState<LocationValue[]>([{ name: "" }, { name: "" }]);
  const [from, setFrom] = useState(plusDays(today, 7));
  const [to, setTo] = useState(plusDays(today, 30));
  const [days, setDays] = useState(2);
  const [budget, setBudget] = useState("");
  const [stay, setStay] = useState("tier2");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picked = options.filter((o) => o.name.trim().length >= 2);
  const span = Math.round((Date.parse(to) - Date.parse(from)) / 86400000) + 1;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (origin.name.trim().length < 2) { setError("Where is the group leaving from?"); return; }
    if (!picked.length) { setError("Add at least one place to vote on."); return; }
    if (span < days) { setError(`A ${days}-day trip doesn't fit between those dates.`); return; }
    setBusy(true);
    try {
      const out = await endpoints.createGroup({
        title: title.trim() || null, origin, options: picked, window_start: from, window_end: to, days,
        budget_hint: budget ? Number(budget) : null, stay, note: note.trim() || null,
      });
      router.push(`/together/${out.group.id}?new=1`);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't start the group plan."); setBusy(false); }
  }

  return (
    <div className="container-x max-w-3xl pb-16 pt-4 md:pt-8">
      <span className="eyebrow"><Users className="h-3.5 w-3.5 text-signal" aria-hidden />Plan together</span>
      <h1 className="mt-4 text-title font-bold">One link. <span className="accent highlight">The whole gang decides.</span></h1>
      <p className="mt-3 max-w-xl text-[17px] text-ink-soft">Pick a few places and a date window. Drop the link in the group chat; everyone adds their budget, dates and veg / non-veg, and votes. No sign-up for them.</p>

      <form onSubmit={submit} className="mt-8 space-y-5">
        <section className="card space-y-4 p-5 md:p-6">
          <h2 className="font-bold">Where to?</h2>
          <LocationInput id="g-origin" label="Leaving from" value={origin} onChange={setOrigin} allowCurrent placeholder="City, area or landmark" />
          <div className="space-y-3">
            <p className="text-sm font-semibold">Places to vote on <span className="font-normal text-ink-mute">(1 to 4)</span></p>
            {options.map((o, n) => (
              <div key={n} className="flex items-end gap-2">
                <div className="min-w-0 flex-1">
                  <LocationInput id={`g-opt-${n}`} label={`Option ${n + 1}`} value={o} onChange={(v) => setOptions((xs) => xs.map((x, i) => (i === n ? v : x)))}
                                 placeholder={n === 0 ? "e.g. Rishikesh" : n === 1 ? "e.g. Jaipur" : "Another place"} />
                </div>
                {options.length > 1 && (
                  <button type="button" onClick={() => setOptions((xs) => xs.filter((_, i) => i !== n))} aria-label={`Remove option ${n + 1}`}
                          className="mb-1.5 grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line text-ink-mute hover:border-danger/40 hover:text-danger"><X className="h-4 w-4" /></button>
                )}
              </div>
            ))}
            {options.length < 4 && (
              <button type="button" onClick={() => setOptions((xs) => [...xs, { name: "" }])}
                      className="flex w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-line py-3 text-[14px] font-semibold text-ink-soft hover:border-signal/50 hover:bg-signal-soft/40 hover:text-ink">
                <Plus className="h-4 w-4" aria-hidden />Add another place
              </button>
            )}
          </div>
        </section>

        <section className="card space-y-4 p-5 md:p-6">
          <h2 className="flex items-center gap-2 font-bold"><CalendarDays className="h-4 w-4 text-signal" aria-hidden />When could it be?</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm font-semibold">Any time from
              <input type="date" className="field mt-1.5" min={today} value={from} onChange={(e) => { setFrom(e.target.value); if (e.target.value > to) setTo(plusDays(e.target.value, 14)); }} />
            </label>
            <label className="block text-sm font-semibold">Until
              <input type="date" className="field mt-1.5" min={from} max={plusDays(from, 90)} value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-haze/60 px-4 py-3">
            <span className="text-sm font-semibold">How long is the trip?</span>
            <span className="flex items-center gap-2">
              <button type="button" onClick={() => setDays((d) => Math.max(1, d - 1))} aria-label="Fewer days" className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper hover:border-ink/30"><Minus className="h-4 w-4" /></button>
              <span className="num w-20 text-center text-[17px] font-extrabold">{days} {days === 1 ? "day" : "days"}</span>
              <button type="button" onClick={() => setDays((d) => Math.min(14, d + 1))} aria-label="More days" className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper hover:border-ink/30"><Plus className="h-4 w-4" /></button>
            </span>
          </div>
          <p className="text-[13px] text-ink-mute">Friends tap the days they&apos;re free; we find the {days}-day stretch that works for the most people.</p>
        </section>

        <section className="card space-y-4 p-5 md:p-6">
          <h2 className="font-bold">The details</h2>
          <label className="block text-sm font-semibold">Name it <span className="font-normal text-ink-mute">(optional)</span>
            <input className="field mt-1.5" maxLength={120} placeholder="Goa before the monsoon" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="block text-sm font-semibold">Suggested budget per person <span className="font-normal text-ink-mute">(optional)</span>
            <input className="field num mt-1.5" inputMode="numeric" placeholder="e.g. 8000" value={budget} onChange={(e) => setBudget(e.target.value.replace(/\D/g, ""))} />
            <span className="mt-1 block text-[12.5px] font-normal text-ink-mute">The plan uses the lowest budget anyone gives, so nobody is priced out. This is the fallback.</span>
          </label>
          <fieldset>
            <legend className="mb-2 text-sm font-semibold">Where you&apos;d stay</legend>
            <div className="flex flex-wrap gap-1.5">
              {STAYS.map(([k, l]) => (
                <button type="button" key={k} onClick={() => setStay(k)} aria-pressed={stay === k} className={stay === k ? "chip-on" : "chip-off"}>{l}</button>
              ))}
            </div>
          </fieldset>
          <label className="block text-sm font-semibold">A note for the group <span className="font-normal text-ink-mute">(optional)</span>
            <input className="field mt-1.5" maxLength={300} placeholder="Long weekend, let's finally do this" value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
        </section>

        {error && <p role="alert" className="rounded-xl bg-danger-soft px-4 py-3 text-[15px] font-medium text-danger">{error}</p>}
        <button className="btn-primary shine w-full !rounded-full !py-3.5 !text-[16px]" disabled={busy}>
          {busy ? "Making your link…" : <>Get the group link<ArrowRight className="h-4 w-4" aria-hidden /></>}
        </button>
        <p className="text-center text-[13px] text-ink-mute">Collecting answers is free. Making the plan at the end uses one plan.</p>
      </form>
    </div>
  );
}

export default function NewGroupPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <RequireAuth note="Sign in to start a group plan. Your friends won't need an account."><NewGroup /></RequireAuth>
    </main>
  );
}
