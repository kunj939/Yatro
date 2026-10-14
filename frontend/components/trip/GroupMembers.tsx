"use client";

import { useState } from "react";
import { Crown, Trash2, UserPlus } from "lucide-react";
import type { Trip } from "@/lib/types";
import { ApiError, endpoints } from "@/lib/api";
import { FOOD_LABEL, currencySymbol, money } from "@/lib/format";

export function GroupMembers({ trip, onChanged, readOnly }: { trip: Trip; onChanged: () => Promise<void> | void; readOnly?: boolean }) {
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [food, setFood] = useState("");
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
          <h2 id="group-title" className="text-lg font-bold">Who&apos;s going</h2>
          <p className="text-[13px] text-ink-mute">{people} {people === 1 ? "person" : "people"} on this trip</p>
        </div>
        {share !== null && (
          <div className="num rounded-xl bg-signal-soft px-3 py-1.5 text-right">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-signal-deep">Each pays about</p>
            <p className="text-[15px] font-bold text-ink">{money(Math.round(share), trip.currency)}</p>
          </div>
        )}
      </div>

      <ul className="mt-4 space-y-2">
        {trip.members.map((m) => {
          const short = share !== null && m.budget !== null && m.budget < share;
          return (
            <li key={m.id} className="flex items-center gap-3 rounded-xl border border-line bg-haze/50 px-3 py-2.5">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-signal text-[14px] font-bold text-on-signal">{m.name.slice(0, 1).toUpperCase()}</span>
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 truncate font-semibold">{m.name}{m.is_owner && <Crown className="h-4 w-4 shrink-0 text-signal-deep" aria-label="Trip owner" />}</p>
                <p className="num truncate text-[13px] text-ink-mute">
                  {m.budget !== null ? `Budget ${money(m.budget, trip.currency)}` : "No personal budget"}
                  {m.food_pref ? ` · ${FOOD_LABEL[m.food_pref] ?? m.food_pref}` : ""}
                </p>
                {short && <p className="text-[12px] font-medium text-caution">Below the planned share</p>}
              </div>
              {!readOnly && !m.is_owner && (
                <button onClick={() => remove(m.id)} className="shrink-0 rounded-full p-2 text-ink-mute hover:bg-paper hover:text-danger" aria-label={`Remove ${m.name}`}>
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {!readOnly && (
        <form onSubmit={add} className="mt-5 space-y-2.5 border-t border-line pt-5">
          <p className="text-[14px] font-semibold">Add someone</p>
          <label className="sr-only" htmlFor="m-name">Name</label>
          <input id="m-name" className="field" placeholder="Their name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="sr-only" htmlFor="m-budget">Personal budget (optional)</label>
              <input id="m-budget" className="field num" inputMode="numeric" placeholder={`Budget ${currencySymbol(trip.currency)}`} value={budget}
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
          <button className="btn-dark w-full" disabled={busy || !name.trim()}><UserPlus className="h-4 w-4" aria-hidden />{busy ? "Adding…" : "Add to the trip"}</button>
        </form>
      )}
      {error && <p role="alert" className="mt-3 text-sm font-medium text-danger">{error}</p>}
    </section>
  );
}
