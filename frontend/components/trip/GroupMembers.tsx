"use client";

import { useState } from "react";
import { Crown, Trash2, UserPlus } from "lucide-react";
import type { Trip } from "@/lib/types";
import { ApiError, endpoints } from "@/lib/api";
import { FOOD_LABEL, money } from "@/lib/format";

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

  return (
    <section className="panel p-5 md:p-6" aria-labelledby="group-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="group-title" className="text-lg font-bold">Who's going</h2>
        {share !== null && <p className="num text-sm text-ink-mute">Planned share: about {money(Math.round(share), trip.currency)} each</p>}
      </div>
      <ul className="mt-4 divide-y divide-line">
        {trip.members.map((m) => {
          const short = share !== null && m.budget !== null && m.budget < share;
          return (
            <li key={m.id} className="flex items-center justify-between gap-3 py-3">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-full bg-haze font-bold text-ink">{m.name.slice(0, 1).toUpperCase()}</span>
                <div>
                  <p className="flex items-center gap-1.5 font-semibold">{m.name}{m.is_owner && <Crown className="h-4 w-4 text-signal-deep" aria-label="Trip owner" />}</p>
                  <p className="num text-[13px] text-ink-mute">
                    {m.budget !== null ? `Budget ${money(m.budget, trip.currency)}` : "No personal budget"}
                    {m.food_pref ? `, ${FOOD_LABEL[m.food_pref] ?? m.food_pref}` : ""}
                  </p>
                  {short && <p className="text-[13px] font-medium text-caution">Their budget is below the planned share.</p>}
                </div>
              </div>
              {!readOnly && !m.is_owner && (
                <button onClick={() => remove(m.id)} className="rounded-full p-2 text-ink-mute hover:bg-haze hover:text-danger" aria-label={`Remove ${m.name}`}>
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {!readOnly && (
        <form onSubmit={add} className="mt-4 grid gap-2 md:grid-cols-[1fr_140px_150px_auto]">
          <label className="sr-only" htmlFor="m-name">Name</label>
          <input id="m-name" className="field" placeholder="Add a person" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          <label className="sr-only" htmlFor="m-budget">Personal budget (optional)</label>
          <input id="m-budget" className="field num" inputMode="numeric" placeholder="Budget ₹" value={budget}
                 onChange={(e) => setBudget(e.target.value.replace(/[^\d]/g, ""))} />
          <label className="sr-only" htmlFor="m-food">Food preference</label>
          <select id="m-food" className="field" value={food} onChange={(e) => setFood(e.target.value)}>
            <option value="">Food: any</option>
            <option value="veg">Vegetarian</option>
            <option value="non_veg">Non-veg</option>
            <option value="both">Both</option>
          </select>
          <button className="btn-dark" disabled={busy || !name.trim()}><UserPlus className="h-4 w-4" aria-hidden />Add</button>
        </form>
      )}
      {error && <p role="alert" className="mt-3 text-sm font-medium text-danger">{error}</p>}
    </section>
  );
}