"use client";

import { useState } from "react";
import { Crown, Leaf, Plus, Trash2, UserPlus, X } from "lucide-react";
import type { Trip } from "@/lib/types";
import { ApiError, endpoints } from "@/lib/api";
import { FOOD_LABEL, currencySymbol, money } from "@/lib/format";

// One colour per person, by their place in the group, so the same face shows up the same everywhere in Settle Up.
const TONES = ["bg-[#659600] text-white", "bg-[#2a78d6] text-white", "bg-[#eb6834] text-white", "bg-[#6250d6] text-white",
  "bg-[#eda100] text-[#101311]", "bg-ink text-paper"];
const HEX = ["#659600", "#2a78d6", "#eb6834", "#6250d6", "#eda100", "#101311"];
const place = (trip: Trip, id: string) => Math.max(0, trip.members.findIndex((m) => m.id === id));
export function avatarTone(trip: Trip, memberId: string): string { return TONES[place(trip, memberId) % TONES.length]; }
export function avatarHex(trip: Trip, memberId: string): string { return HEX[place(trip, memberId) % HEX.length]; }

export function Avatar({ trip, id, name, size = "h-9 w-9 text-[14px]" }: { trip: Trip; id: string; name: string; size?: string }) {
  return <span className={`grid shrink-0 place-items-center rounded-full font-bold ring-2 ring-paper ${size} ${avatarTone(trip, id)}`} aria-hidden>{name.slice(0, 1).toUpperCase()}</span>;
}

export function GroupMembers({ trip, onChanged, readOnly }: { trip: Trip; onChanged: () => Promise<void> | void; readOnly?: boolean }) {
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [food, setFood] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const share = trip.itinerary ? trip.itinerary.total_cost / Math.max(1, trip.members.length || trip.travellers) : null;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true); setError(null);
    try {
      await endpoints.addMember(trip.id, { name: name.trim(), budget: budget ? Number(budget) : null, food_pref: food || null });
      setName(""); setBudget(""); setFood("");
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add this person.");
    } finally { setBusy(false); }
  }

  async function remove(id: string) {
    setError(null);
    try { await endpoints.removeMember(trip.id, id); await onChanged(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't remove this person."); }
  }

  const people = trip.members.length || 1;
  return (
    <section className="card p-5 md:p-6" aria-labelledby="group-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="group-title" className="text-lg font-bold">The squad</h2>
          <p className="text-[13px] text-ink-mute">{people} {people === 1 ? "person" : "people"} on this trip</p>
        </div>
        <div className="flex -space-x-2" aria-hidden>
          {trip.members.slice(0, 5).map((m) => <Avatar key={m.id} trip={trip} id={m.id} name={m.name} size="h-8 w-8 text-[12px]" />)}
        </div>
      </div>

      {share !== null && (
        <div className="num mt-4 flex items-center justify-between rounded-2xl border border-signal/20 bg-signal-soft/60 px-4 py-3">
          <span className="text-[13px] font-semibold text-signal-deep">Each person&apos;s share of the plan</span>
          <span className="text-[17px] font-extrabold text-ink">{money(Math.round(share), trip.currency)}</span>
        </div>
      )}

      <ul className="mt-3 space-y-2">
        {trip.members.map((m) => {
          const short = share !== null && m.budget !== null && m.budget < share;
          const net = trip.expense_summary.members.find((x) => x.member_id === m.id)?.net ?? 0;
          const fill = share !== null && m.budget ? Math.min(100, (share / m.budget) * 100) : 0;
          return (
            <li key={m.id} className="group/m rounded-2xl border border-line bg-paper px-3.5 py-3 transition-colors hover:border-ink/20">
              <div className="flex items-center gap-3">
                <Avatar trip={trip} id={m.id} name={m.name} />
                <div className="min-w-0 flex-1">
                  <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 font-semibold">
                    <span className="truncate">{m.name}</span>{m.is_owner && <Crown className="h-4 w-4 shrink-0 text-[#eda100]" aria-label="Trip owner" />}
                    {trip.expense_summary.total > 0 && (
                      <span className={`num rounded-full px-2 py-px text-[11.5px] font-semibold ${
                        net > 0.005 ? "bg-signal-soft text-signal-deep" : net < -0.005 ? "bg-[#eb6834]/10 text-[#b84a1c]" : "bg-haze-2 text-ink-mute"}`}>
                        {net > 0.005 ? `gets ${money(net, trip.currency)}` : net < -0.005 ? `owes ${money(-net, trip.currency)}` : "square"}
                      </span>
                    )}
                  </p>
                  <p className="num flex flex-wrap items-center gap-x-2 text-[13px] text-ink-mute">
                    <span>{m.budget !== null ? `Budget ${money(m.budget, trip.currency)}` : "No personal budget"}</span>
                    {m.food_pref && (
                      <span className="inline-flex items-center gap-1">
                        {m.food_pref === "veg" && <Leaf className="h-3 w-3 text-signal" aria-hidden />}{FOOD_LABEL[m.food_pref] ?? m.food_pref}
                      </span>
                    )}
                  </p>
                </div>
                {!readOnly && !m.is_owner && (
                  <button onClick={() => remove(m.id)} className="shrink-0 rounded-full p-2 text-ink-mute opacity-60 transition-opacity hover:bg-haze hover:text-danger group-hover/m:opacity-100" aria-label={`Remove ${m.name}`}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
              {m.budget !== null && share !== null && (
                <div className="mt-2.5 pl-12">
                  <div className="h-1.5 overflow-hidden rounded-full bg-haze-2" aria-hidden>
                    <div className={`h-full rounded-full ${short ? "bg-caution" : "bg-signal"}`} style={{ width: `${fill}%` }} />
                  </div>
                  <p className={`num mt-1 text-[12px] ${short ? "font-semibold text-caution" : "text-ink-mute"}`}>
                    {short ? `Share is ${money(Math.round(share - m.budget), trip.currency)} over their budget` : `Share uses ${Math.round(fill)}% of their budget`}
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {!readOnly && (adding ? (
        <form onSubmit={add} className="fade-up mt-4 space-y-2.5 rounded-2xl border border-line bg-haze/50 p-4">
          <div className="flex items-center justify-between">
            <p className="text-[14px] font-semibold">Add someone</p>
            <button type="button" onClick={() => setAdding(false)} className="rounded-full p-1 text-ink-mute hover:text-ink" aria-label="Close"><X className="h-4 w-4" /></button>
          </div>
          <label className="sr-only" htmlFor="m-name">Name</label>
          <input id="m-name" className="field" placeholder="Their name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus />
          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="sr-only" htmlFor="m-budget">Personal budget (optional)</label>
              <input id="m-budget" className="field num" inputMode="numeric" placeholder={`Budget ${currencySymbol(trip.currency)} (optional)`} value={budget}
                     onChange={(e) => setBudget(e.target.value.replace(/[^\d]/g, ""))} />
            </div>
            <div>
              <label className="sr-only" htmlFor="m-food">Food preference</label>
              <select id="m-food" className="field" value={food} onChange={(e) => setFood(e.target.value)}>
                <option value="">Food: any</option>
                <option value="veg">Vegetarian</option>
                <option value="non_veg">Non-veg</option>
                <option value="both">Both</option>
              </select>
            </div>
          </div>
          <button className="btn-primary w-full" disabled={busy || !name.trim()}><UserPlus className="h-4 w-4" aria-hidden />{busy ? "Adding…" : "Add to the trip"}</button>
        </form>
      ) : (
        <button type="button" onClick={() => setAdding(true)}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-line py-3 text-[14px] font-semibold text-ink-soft transition-colors hover:border-signal/50 hover:bg-signal-soft/40 hover:text-ink">
          <Plus className="h-4 w-4" aria-hidden />Add someone
        </button>
      ))}
      {error && <p role="alert" className="mt-3 text-sm font-medium text-danger">{error}</p>}
    </section>
  );
}
