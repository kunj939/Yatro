"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, Copy, Plus, Smartphone, Trash2 } from "lucide-react";
import type { Trip } from "@/lib/types";
import { ApiError, endpoints } from "@/lib/api";
import { money } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import { onMobile, upiLink, validUpi } from "@/lib/upi";

const CATEGORIES = [
  ["food", "Food"], ["transport", "Transport"], ["local", "Local travel"], ["activities", "Tickets"],
  ["stay", "Hotel"], ["shopping", "Shopping"], ["other", "Other"],
] as const;
const SPLITS = [
  ["equal", "Split equally"], ["selected", "Selected people"], ["custom", "Custom amounts"], ["one_person", "One person"],
] as const;

type Settlement = Trip["expense_summary"]["settlements"][number];

/** The payment helpers under one "A pays B" row (ADR 0006). Yatro never touches the money, and never keeps anyone's UPI ID
 *  but their own: a payee's ID typed here lives in this component's state and goes nowhere. */
function PayHelp({ x, trip, ownerId }: { x: Settlement; trip: Trip; ownerId: string | undefined }) {
  const { user } = useAuth();
  const flags = useFlags();
  const [open, setOpen] = useState(false);
  const [upi, setUpi] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [phone, setPhone] = useState(false);
  useEffect(() => setPhone(onMobile()), []);
  if (trip.currency !== "INR" || !flags.upi) return null;

  async function copy(what: string, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(what); setTimeout(() => setCopied(null), 1800); } catch { /* clipboard blocked */ }
  }
  const amountText = x.amount.toFixed(2);
  const CopyBtn = ({ what, text, label }: { what: string; text: string; label: string }) => (
    <button type="button" onClick={() => copy(what, text)} className="inline-flex items-center gap-1 rounded-full border border-line px-3 py-1 text-[13px] font-semibold hover:border-ink/30">
      {copied === what ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}{copied === what ? "Copied" : label}
    </button>
  );

  // I am the one being paid: share my own UPI ID, if I saved it
  if (x.to_member_id === ownerId) {
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
        {user?.upi_id
          ? <CopyBtn what="req" label="Copy payment request" text={`Please pay ${x.from_name} ${money(x.amount, "INR")} to ${user.upi_id} for ${trip.title}.`} />
          : <Link href="/account#upi" className="font-semibold text-signal-deep underline underline-offset-4">Add your UPI ID to ask for this</Link>}
        <CopyBtn what="amt" label="Copy amount" text={amountText} />
      </div>
    );
  }
  // I pay: a payee's UPI ID is typed here, used in this browser only
  if (x.from_member_id === ownerId) {
    return (
      <div className="mt-2 space-y-2 text-[13px]">
        <div className="flex flex-wrap items-center gap-2">
          {phone && <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1 rounded-full bg-ink px-3 py-1 font-semibold text-paper"><Smartphone className="h-3.5 w-3.5" aria-hidden />Pay via UPI</button>}
          <CopyBtn what="amt" label="Copy amount" text={amountText} />
          {!open && <span className="text-ink-mute">Ask {x.to_name} for their UPI ID</span>}
        </div>
        {open && phone && (
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={`upi-${x.to_member_id}`} className="sr-only">{x.to_name}&apos;s UPI ID</label>
            <input id={`upi-${x.to_member_id}`} className="field max-w-[240px] !py-1.5" placeholder={`${x.to_name}'s UPI ID (name@bank)`} value={upi}
                   onChange={(e) => setUpi(e.target.value)} autoComplete="off" autoCapitalize="none" spellCheck={false} />
            {validUpi(upi)
              ? <a className="btn-primary !px-4 !py-1.5" href={upiLink({ payee: upi, name: x.to_name, amount: x.amount, note: `Yatro: ${trip.title}`.slice(0, 60) })}>Open UPI app</a>
              : <span className="text-ink-mute">Not saved or sent anywhere.</span>}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
      <span className="text-ink-mute">Ask {x.to_name} for their UPI ID</span>
      <CopyBtn what="amt" label="Copy amount" text={amountText} />
    </div>
  );
}

export function ExpenseSplitter({ trip, onChanged, readOnly }: { trip: Trip; onChanged: () => Promise<void> | void; readOnly?: boolean }) {
  const members = trip.members;
  const owner = members.find((m) => m.is_owner) ?? members[0];
  const nameOf = useMemo(() => Object.fromEntries(members.map((m) => [m.id, m.name])), [members]);
  const [desc, setDesc] = useState("");
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("food");
  const [paidBy, setPaidBy] = useState(owner?.id ?? "");
  const [split, setSplit] = useState<string>("equal");
  const [selected, setSelected] = useState<string[]>(members.map((m) => m.id));
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [onePerson, setOnePerson] = useState(owner?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // keep the form's member choices valid when people are added or removed
  useEffect(() => {
    setSelected(members.map((m) => m.id));
    setPaidBy((p) => (members.some((m) => m.id === p) ? p : owner?.id ?? ""));
    setOnePerson((p) => (members.some((m) => m.id === p) ? p : owner?.id ?? ""));
  }, [members, owner]);

  const amt = Number(amount) || 0;
  const customTotal = Object.values(custom).reduce((a, v) => a + (Number(v) || 0), 0);
  const perHead = split === "equal" ? amt / Math.max(1, members.length) : split === "selected" ? amt / Math.max(1, selected.length) : 0;
  const s = trip.expense_summary;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!desc.trim() || amt <= 0) { setError("Add what it was for and the amount."); return; }
    if (split === "custom" && Math.abs(customTotal - amt) > 0.009) { setError(`Custom shares add up to ${money(customTotal, trip.currency)}, not ${money(amt, trip.currency)}.`); return; }
    setBusy(true);
    try {
      await endpoints.addExpense(trip.id, {
        description: desc.trim(), amount: amt, category, paid_by_member_id: paidBy, split_type: split,
        member_ids: split === "selected" ? selected : split === "one_person" ? [onePerson] : null,
        custom: split === "custom" ? members.map((m) => ({ member_id: m.id, amount: Number(custom[m.id]) || 0 })) : null,
      });
      setDesc(""); setAmount(""); setCustom({});
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save this expense.");
    } finally { setBusy(false); }
  }

  async function remove(id: string) {
    try { await endpoints.deleteExpense(trip.id, id); await onChanged(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't delete this expense."); }
  }

  return (
    <div className="space-y-6">
      <section className="panel p-5 md:p-6" aria-labelledby="settle-title">
        <h2 id="settle-title" className="text-lg font-bold">Who owes whom</h2>
        {s.settlements.length === 0 ? (
          <p className="mt-2 text-[16px] text-ink-soft">{s.total > 0 ? "Everyone is square." : "Log a shared expense and the balance appears here."}</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {s.settlements.map((x, i) => (
              <li key={i} className="rounded-xl bg-haze px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 font-semibold">{x.from_name}<ArrowRight className="h-4 w-4 text-ink-mute" aria-label="pays" />{x.to_name}</span>
                  <span className="num text-lg font-bold">{money(x.amount, trip.currency)}</span>
                </div>
                {!readOnly && <PayHelp x={x} trip={trip} ownerId={owner?.id} />}
              </li>
            ))}
          </ul>
        )}
        {s.total > 0 && (
          <div className="mt-4 overflow-x-auto">
            <table className="num w-full min-w-[420px] text-left text-[14px]">
              <thead className="text-ink-mute">
                <tr><th className="py-2 font-medium">Person</th><th className="py-2 text-right font-medium">Paid</th><th className="py-2 text-right font-medium">Share</th><th className="py-2 text-right font-medium">Balance</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {s.members.map((m) => (
                  <tr key={m.member_id}>
                    <td className="py-2 font-semibold">{m.name}</td>
                    <td className="py-2 text-right">{money(m.paid, trip.currency)}</td>
                    <td className="py-2 text-right">{money(m.share, trip.currency)}</td>
                    <td className={`py-2 text-right font-semibold ${m.net > 0 ? "text-teal-deep" : m.net < 0 ? "text-caution" : "text-ink-mute"}`}>
                      {m.net > 0 ? `gets ${money(m.net, trip.currency)}` : m.net < 0 ? `owes ${money(-m.net, trip.currency)}` : "square"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {!readOnly && (
        <form onSubmit={add} className="panel space-y-4 p-5 md:p-6" aria-labelledby="add-exp">
          <h2 id="add-exp" className="text-lg font-bold">Add an expense</h2>
          <div className="grid gap-3 md:grid-cols-[1fr_160px]">
            <div>
              <label htmlFor="e-desc" className="mb-1 block text-sm font-medium">What was it for?</label>
              <input id="e-desc" className="field" placeholder="Lunch at LMB" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={200} />
            </div>
            <div>
              <label htmlFor="e-amt" className="mb-1 block text-sm font-medium">Amount (₹)</label>
              <input id="e-amt" className="field num" inputMode="decimal" placeholder="1450" value={amount}
                     onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} />
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label htmlFor="e-paid" className="mb-1 block text-sm font-medium">Paid by</label>
              <select id="e-paid" className="field" value={paidBy} onChange={(e) => setPaidBy(e.target.value)}>
                {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="e-cat" className="mb-1 block text-sm font-medium">Category</label>
              <select id="e-cat" className="field" value={category} onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
          </div>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">How to split</legend>
            <div className="flex flex-wrap gap-2">
              {SPLITS.map(([k, l]) => (
                <button type="button" key={k} onClick={() => setSplit(k)} aria-pressed={split === k} className={split === k ? "chip-on" : "chip-off"}>{l}</button>
              ))}
            </div>
          </fieldset>
          {split === "selected" && (
            <div className="flex flex-wrap gap-2">
              {members.map((m) => {
                const on = selected.includes(m.id);
                return (
                  <label key={m.id} className={`${on ? "chip-on" : "chip-off"} cursor-pointer`}>
                    <input type="checkbox" className="sr-only" checked={on}
                           onChange={() => setSelected(on ? selected.filter((x) => x !== m.id) : [...selected, m.id])} />
                    {m.name}
                  </label>
                );
              })}
            </div>
          )}
          {split === "custom" && (
            <div className="space-y-2">
              {members.map((m) => (
                <div key={m.id} className="flex items-center gap-3">
                  <label htmlFor={`c-${m.id}`} className="w-32 truncate text-[16px]">{m.name}</label>
                  <input id={`c-${m.id}`} className="field num max-w-[160px]" inputMode="decimal" placeholder="0" value={custom[m.id] ?? ""}
                         onChange={(e) => setCustom({ ...custom, [m.id]: e.target.value.replace(/[^\d.]/g, "") })} />
                </div>
              ))}
              <p className={`num text-sm ${Math.abs(customTotal - amt) < 0.01 ? "text-teal-deep" : "text-ink-mute"}`}>
                {money(customTotal, trip.currency)} of {money(amt, trip.currency)} assigned
              </p>
            </div>
          )}
          {split === "one_person" && (
            <div>
              <label htmlFor="e-one" className="mb-1 block text-sm font-medium">Who is this for?</label>
              <select id="e-one" className="field max-w-xs" value={onePerson} onChange={(e) => setOnePerson(e.target.value)}>
                {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
          )}
          {perHead > 0 && <p className="num text-sm text-ink-soft">That is {money(Math.round(perHead * 100) / 100, trip.currency)} each.</p>}
          {error && <p role="alert" className="text-sm font-medium text-danger">{error}</p>}
          <button className="btn-dark" disabled={busy}><Plus className="h-4 w-4" aria-hidden />Add expense</button>
        </form>
      )}

      <section className="panel" aria-labelledby="exp-list">
        <h2 id="exp-list" className="px-5 pt-5 text-lg font-bold md:px-6">Expenses <span className="num font-normal text-ink-mute">{money(s.total, trip.currency)}</span></h2>
        {trip.expenses.length === 0 ? (
          <p className="px-5 pb-5 pt-2 text-[16px] text-ink-soft md:px-6">No expenses yet.</p>
        ) : (
          <ul className="mt-2 divide-y divide-line">
            {trip.expenses.map((x) => (
              <li key={x.id} className="flex items-center justify-between gap-3 px-5 py-3 md:px-6">
                <div className="min-w-0">
                  <p className="truncate font-semibold">{x.description}</p>
                  <p className="text-[13px] text-ink-mute">
                    Paid by {nameOf[x.paid_by_member_id] ?? "someone"}, {SPLITS.find(([k]) => k === x.split_type)?.[1].toLowerCase() ?? x.split_type}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="num font-bold">{money(x.amount, trip.currency)}</span>
                  {!readOnly && (
                    <button onClick={() => remove(x.id)} className="rounded-full p-2 text-ink-mute hover:bg-haze hover:text-danger" aria-label={`Delete ${x.description}`}>
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}