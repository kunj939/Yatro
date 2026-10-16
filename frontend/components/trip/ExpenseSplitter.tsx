"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, BedDouble, Car, Check, Copy, HandCoins, MoreHorizontal, Plus, Receipt, ShoppingBag, Smartphone, Ticket, TrainFront, Trash2, Utensils } from "lucide-react";
import type { Trip } from "@/lib/types";
import { ApiError, endpoints } from "@/lib/api";
import { currencySymbol, money, niceDate } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import { onMobile, upiLink, validUpi } from "@/lib/upi";
import { Avatar, avatarHex } from "./GroupMembers";

// Each with the Budget tab's colour for the same kind of spend (lib/budgetViz.ts), so food looks like food everywhere.
const CATEGORIES = [
  ["food", "Food", Utensils, "#2a78d6"], ["transport", "Transport", TrainFront, "#b07800"], ["local", "Local travel", Car, "#6250d6"],
  ["activities", "Tickets", Ticket, "#eb6834"], ["stay", "Hotel", BedDouble, "#659600"], ["shopping", "Shopping", ShoppingBag, "#c2457a"],
  ["other", "Other", MoreHorizontal, "#666d68"],
] as const;
const SPLITS = [
  ["equal", "Split equally"], ["selected", "Selected people"], ["custom", "Custom amounts"], ["one_person", "One person"],
] as const;
const SHORT: Record<string, string> = { equal: "Equally", selected: "Some people", custom: "Custom", one_person: "One person" };

type Settlement = Trip["expense_summary"]["settlements"][number];

/** The payment helpers under one "A pays B" row (ADR 0006). Yaitro never touches the money, and never keeps anyone's UPI ID
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
              ? <a className="btn-primary !px-4 !py-1.5" href={upiLink({ payee: upi, name: x.to_name, amount: x.amount, note: `Yaitro: ${trip.title}`.slice(0, 60) })}>Open UPI app</a>
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

  const cat = (k: string) => CATEGORIES.find(([c]) => c === k) ?? CATEGORIES[CATEGORIES.length - 1];
  // spends grouped by the day they were logged, newest first
  const byDay = useMemo(() => {
    const groups = new Map<string, Trip["expenses"]>();
    for (const x of [...trip.expenses].sort((a, b) => b.created_at.localeCompare(a.created_at))) {
      const k = (x.created_at || "").slice(0, 10);
      groups.set(k, [...(groups.get(k) ?? []), x]);
    }
    return [...groups.entries()];
  }, [trip.expenses]);

  return (
    <div className="space-y-5">
      <section className="card p-5 md:p-6" aria-labelledby="settle-title">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 id="settle-title" className="text-lg font-bold">Who pays whom</h2>
            <p className="text-[13px] text-ink-mute">The fewest payments that make everyone square</p>
          </div>
          {s.settlements.length > 0
            ? <span className="num rounded-full bg-[#eb6834]/10 px-2.5 py-1 text-[12px] font-semibold text-[#b84a1c]">{s.settlements.length} to settle</span>
            : s.total > 0 && <span className="inline-flex items-center gap-1 rounded-full bg-signal-soft px-2.5 py-1 text-[12px] font-semibold text-signal-deep"><Check className="h-3.5 w-3.5" aria-hidden />All square</span>}
        </div>
        {s.settlements.length === 0 ? (
          <div className="mt-4 flex items-center gap-3 rounded-2xl border border-dashed border-line bg-haze/40 px-4 py-5">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-signal-soft text-signal-deep"><HandCoins className="h-5 w-5" aria-hidden /></span>
            <p className="text-[15px] text-ink-soft">{s.total > 0 ? "Everyone is square. Nothing to settle." : "Log the first shared spend and we work out who owes whom."}</p>
          </div>
        ) : (
          <ul className="mt-4 space-y-2.5">
            {s.settlements.map((x, i) => (
              <li key={i} className="relative overflow-hidden rounded-2xl border border-line bg-gradient-to-r from-paper to-haze/50 px-4 py-3.5">
                <div className="flex items-center gap-3">
                  <span className="flex shrink-0 items-center">
                    <Avatar trip={trip} id={x.from_member_id} name={x.from_name} size="h-9 w-9 text-[13px] sm:h-10 sm:w-10 sm:text-[14px]" />
                    <span className="mx-1.5 flex items-center gap-0.5 text-ink-mute" aria-hidden>
                      <span className="hidden w-6 border-t-2 border-dotted border-ink/25 sm:block" /><ArrowRight className="h-4 w-4" />
                    </span>
                    <Avatar trip={trip} id={x.to_member_id} name={x.to_name} size="h-9 w-9 text-[13px] sm:h-10 sm:w-10 sm:text-[14px]" />
                  </span>
                  <p className="min-w-0 flex-1 text-[14px] leading-snug text-ink-soft"><span className="font-semibold text-ink">{x.from_name}</span> pays <span className="font-semibold text-ink">{x.to_name}</span></p>
                  <span className="num shrink-0 rounded-xl bg-paper px-3 py-1.5 text-[18px] font-extrabold text-ink shadow-[0_1px_2px_rgb(var(--shadow)/0.06),0_6px_16px_-10px_rgb(var(--shadow)/0.35)] ring-1 ring-line">{money(x.amount, trip.currency)}</span>
                </div>
                {!readOnly && <div className="sm:pl-[7.25rem]"><PayHelp x={x} trip={trip} ownerId={owner?.id} /></div>}
              </li>
            ))}
          </ul>
        )}
        {s.total > 0 && <Balances trip={trip} />}
      </section>

      {!readOnly && (
        <form onSubmit={add} className="card overflow-hidden" aria-labelledby="add-exp">
          <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4 md:px-6">
            <div>
              <h2 id="add-exp" className="text-lg font-bold">Log a spend</h2>
              <p className="text-[13px] text-ink-mute">Takes ten seconds. The maths is on us.</p>
            </div>
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-signal-soft text-signal-deep"><Receipt className="h-5 w-5" aria-hidden /></span>
          </div>
          <div className="space-y-5 p-5 md:p-6">
            {/* the amount first and big, the way payment apps do it */}
            <div className="rounded-2xl border border-line bg-gradient-to-b from-haze/70 to-paper px-4 py-5 text-center">
              <label htmlFor="e-amt" className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-mute">Amount</label>
              <div className="mt-1 flex items-baseline justify-center gap-1">
                <span className="text-[26px] font-bold text-ink-mute">{currencySymbol(trip.currency)}</span>
                <input id="e-amt" className="num max-w-[14rem] bg-transparent text-left text-[42px] font-extrabold leading-tight text-ink outline-none placeholder:text-ink/20"
                       style={{ width: `${Math.max(1, amount.length) + 0.6}ch` }}
                       inputMode="decimal" placeholder="0" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} />
              </div>
              <p className="num mt-1 h-5 text-[13px] text-ink-soft">
                {perHead > 0 ? <>That&apos;s <span className="font-semibold text-ink">{money(Math.round(perHead * 100) / 100, trip.currency)}</span> each</> : ""}
              </p>
            </div>
            <div>
              <label htmlFor="e-desc" className="mb-1.5 block text-sm font-semibold">What was it for?</label>
              <input id="e-desc" className="field" placeholder="Lunch at LMB" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={200} />
            </div>
            <fieldset>
              <legend className="mb-2 text-sm font-semibold">Category</legend>
              <div className="flex flex-wrap gap-1.5">
                {CATEGORIES.map(([k, l, I, hex]) => (
                  <button type="button" key={k} onClick={() => setCategory(k)} aria-pressed={category === k}
                          className={`inline-flex items-center gap-1.5 rounded-full border py-1.5 pl-1.5 pr-3 text-[13px] font-medium transition-all ${
                            category === k ? "border-ink/80 bg-ink text-paper shadow-sm" : "border-line bg-paper text-ink-soft hover:border-ink/30 hover:text-ink"}`}>
                    <span className="grid h-5 w-5 place-items-center rounded-full" style={{ background: category === k ? "rgb(255 255 255 / 0.15)" : `${hex}1f`, color: category === k ? "#fff" : hex }}>
                      <I className="h-3 w-3" aria-hidden />
                    </span>{l}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-2 text-sm font-semibold">Paid by</legend>
              <div className="flex flex-wrap gap-2">
                {members.map((m) => (
                  <button type="button" key={m.id} onClick={() => setPaidBy(m.id)} aria-pressed={paidBy === m.id}
                          className={`inline-flex items-center gap-2 rounded-full border py-1 pl-1 pr-3.5 text-[13px] font-semibold transition-all ${
                            paidBy === m.id ? "border-signal bg-signal-soft text-ink ring-2 ring-signal/20" : "border-line bg-paper text-ink-soft hover:border-ink/30"}`}>
                    <Avatar trip={trip} id={m.id} name={m.name} size="h-7 w-7 text-[11px]" />{m.name}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-2 text-sm font-semibold">Split</legend>
              <div className="grid grid-cols-2 gap-1 rounded-xl bg-haze p-1 sm:grid-cols-4">
                {SPLITS.map(([k]) => (
                  <button type="button" key={k} onClick={() => setSplit(k)} aria-pressed={split === k}
                          className={`whitespace-nowrap rounded-lg px-2 py-2 text-[13px] font-semibold transition-all ${split === k ? "bg-paper text-ink shadow-sm" : "text-ink-mute hover:text-ink"}`}>{SHORT[k]}</button>
                ))}
              </div>
            </fieldset>
            {split === "selected" && (
              <div className="flex flex-wrap gap-2">
                {members.map((m) => {
                  const on = selected.includes(m.id);
                  return (
                    <label key={m.id} className={`inline-flex cursor-pointer items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-[13px] font-medium transition-all ${
                      on ? "border-signal bg-signal-soft text-ink" : "border-line text-ink-mute opacity-70"}`}>
                      <input type="checkbox" className="sr-only" checked={on}
                             onChange={() => setSelected(on ? selected.filter((x) => x !== m.id) : [...selected, m.id])} />
                      <Avatar trip={trip} id={m.id} name={m.name} size="h-6 w-6 text-[11px]" />{m.name}
                    </label>
                  );
                })}
              </div>
            )}
            {split === "custom" && (
              <div className="space-y-2 rounded-2xl bg-haze/50 p-3">
                {members.map((m) => (
                  <div key={m.id} className="flex items-center gap-3">
                    <Avatar trip={trip} id={m.id} name={m.name} size="h-7 w-7 text-[11px]" />
                    <label htmlFor={`c-${m.id}`} className="w-28 truncate text-[15px]">{m.name}</label>
                    <input id={`c-${m.id}`} className="field num max-w-[160px] !py-2" inputMode="decimal" placeholder="0" value={custom[m.id] ?? ""}
                           onChange={(e) => setCustom({ ...custom, [m.id]: e.target.value.replace(/[^\d.]/g, "") })} />
                  </div>
                ))}
                <p className={`num text-sm ${Math.abs(customTotal - amt) < 0.01 ? "text-teal-deep" : "text-ink-mute"}`}>
                  {money(customTotal, trip.currency)} of {money(amt, trip.currency)} assigned
                </p>
              </div>
            )}
            {split === "one_person" && (
              <div className="flex flex-wrap gap-2">
                {members.map((m) => (
                  <button type="button" key={m.id} onClick={() => setOnePerson(m.id)} aria-pressed={onePerson === m.id}
                          className={`inline-flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-[13px] font-medium transition-all ${
                            onePerson === m.id ? "border-signal bg-signal-soft text-ink" : "border-line text-ink-mute"}`}>
                    <Avatar trip={trip} id={m.id} name={m.name} size="h-6 w-6 text-[11px]" />For {m.name}
                  </button>
                ))}
              </div>
            )}
            {error && <p role="alert" className="text-sm font-medium text-danger">{error}</p>}
            <button className="btn-primary shine w-full !py-3 !text-[15px]" disabled={busy}><Plus className="h-4 w-4" aria-hidden />{busy ? "Saving…" : "Add spend"}</button>
          </div>
        </form>
      )}

      <section className="card overflow-hidden" aria-labelledby="exp-list">
        <div className="flex items-baseline justify-between gap-3 px-5 pt-5 md:px-6">
          <div>
            <h2 id="exp-list" className="text-lg font-bold">Spends</h2>
            <p className="text-[13px] text-ink-mute">{trip.expenses.length} logged</p>
          </div>
          <span className="num text-[18px] font-extrabold text-ink">{money(s.total, trip.currency)}</span>
        </div>
        {trip.expenses.length === 0 ? (
          <p className="px-5 pb-5 pt-2 text-[15px] text-ink-mute md:px-6">Nothing logged yet.</p>
        ) : (
          <div className="mt-3">
            {byDay.map(([day, list]) => (
              <div key={day}>
                <p className="border-y border-line bg-haze/60 px-5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-mute md:px-6">
                  {day ? niceDate(day, { weekday: "short", day: "numeric", month: "short" }) : "Earlier"}
                </p>
                <ul className="divide-y divide-line">
                  {list.map((x) => {
                    const [, label, I, hex] = cat(x.category);
                    return (
                      <li key={x.id} className="group/x flex items-center gap-3 px-5 py-3 transition-colors hover:bg-haze/40 md:px-6">
                        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl" style={{ background: `${hex}1a`, color: hex }}><I className="h-[18px] w-[18px]" aria-hidden /></span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-semibold">{x.description}</p>
                          <p className="flex min-w-0 items-center gap-1.5 text-[13px] text-ink-mute">
                            <Avatar trip={trip} id={x.paid_by_member_id} name={nameOf[x.paid_by_member_id] ?? "?"} size="h-4 w-4 text-[8px] !ring-0" />
                            <span className="truncate">{nameOf[x.paid_by_member_id] ?? "Someone"} paid · {label} · {SHORT[x.split_type]?.toLowerCase() ?? x.split_type}</span>
                          </p>
                        </div>
                        <span className="num font-bold">{money(x.amount, trip.currency)}</span>
                        {!readOnly && (
                          <button onClick={() => remove(x.id)} className="rounded-full p-2 text-ink-mute opacity-60 transition-opacity hover:bg-haze hover:text-danger group-hover/x:opacity-100" aria-label={`Delete ${x.description}`}>
                            <Trash2 className="h-4 w-4" />
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

/** Who's up and who's down: a bar either side of zero, olive for money to get back, orange for money owed. */
function Balances({ trip }: { trip: Trip }) {
  const rows = trip.expense_summary.members;
  const max = Math.max(1, ...rows.map((m) => Math.abs(m.net)));
  return (
    <div className="mt-5 border-t border-line pt-4">
      <p className="mb-3 grid grid-cols-[minmax(0,7rem)_1fr_5.5rem] gap-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-mute">
        <span>Balances</span><span className="hidden justify-between sm:flex"><span>Owes</span><span>Gets back</span></span><span />
      </p>
      <ul className="num space-y-2.5">
        {rows.map((m) => {
          const w = (Math.abs(m.net) / max) * 50;
          return (
            <li key={m.member_id} className="grid grid-cols-[minmax(0,7rem)_1fr_5.5rem] items-center gap-3 text-[13px]">
              <span className="flex min-w-0 items-center gap-2">
                <Avatar trip={trip} id={m.member_id} name={m.name} size="h-6 w-6 text-[10px]" />
                <span className="truncate font-medium text-ink">{m.name}</span>
              </span>
              <span className="relative h-7">
                <span className="absolute inset-y-0 left-1/2 w-px bg-line" aria-hidden />
                {m.net !== 0 && (
                  <span className={`absolute top-1/2 h-2.5 -translate-y-1/2 ${m.net > 0 ? "left-1/2 rounded-r-[4px] bg-[#659600]" : "right-1/2 rounded-l-[4px] bg-[#eb6834]"}`}
                        style={{ width: `${w}%` }} aria-hidden />
                )}
              </span>
              <span className="whitespace-nowrap text-right text-[12.5px] font-semibold text-ink">
                {m.net > 0 ? `gets ${money(m.net, trip.currency)}` : m.net < 0 ? `owes ${money(-m.net, trip.currency)}` : "square"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The top of Settle Up: the one number (spent together), and a bar of who has paid how much, in each person's colour. */
export function SettleUpHeader({ trip }: { trip: Trip }) {
  const s = trip.expense_summary;
  const people = Math.max(1, trip.members.length);
  const payers = s.members.filter((m) => m.paid > 0).sort((a, b) => b.paid - a.paid);
  return (
    <section className="card relative overflow-hidden p-5 md:p-7">
      <div aria-hidden className="pointer-events-none absolute -right-24 -top-28 h-72 w-72 rounded-full bg-signal/12 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute -bottom-32 left-1/3 h-56 w-72 rounded-full bg-spark/8 blur-3xl" />
      <div className="relative flex flex-wrap items-start justify-between gap-5">
        <div className="min-w-0">
          <p className="inline-flex items-center gap-1.5 rounded-full border border-signal/25 bg-signal-soft/70 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.14em] text-signal-deep">
            <HandCoins className="h-3.5 w-3.5" aria-hidden />Settle Up
          </p>
          <p className="mt-3 flex flex-wrap items-baseline gap-x-2">
            <span className="headline-font num text-[40px] font-extrabold leading-none text-ink md:text-[48px]">{money(s.total, trip.currency)}</span>
            <span className="text-[15px] text-ink-mute">spent together</span>
          </p>
          <p className="mt-2 text-[14px] text-ink-soft">
            {people} {people === 1 ? "person" : "people"} · {trip.expenses.length} {trip.expenses.length === 1 ? "spend" : "spends"} · about {money(Math.round(s.total / people), trip.currency)} each
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex -space-x-2.5" aria-hidden>
            {trip.members.slice(0, 5).map((m) => <Avatar key={m.id} trip={trip} id={m.id} name={m.name} size="h-11 w-11 text-[15px]" />)}
            {trip.members.length > 5 && <span className="grid h-11 w-11 place-items-center rounded-full bg-haze-2 text-[13px] font-bold text-ink-soft ring-2 ring-paper">+{trip.members.length - 5}</span>}
          </div>
          <span className={`rounded-2xl px-3.5 py-2 text-center ${s.settlements.length ? "bg-[#eb6834]/10" : "bg-signal-soft"}`}>
            <span className={`num block text-[20px] font-extrabold leading-none ${s.settlements.length ? "text-[#b84a1c]" : "text-signal-deep"}`}>{s.settlements.length || <Check className="mx-auto h-5 w-5" aria-label="none" />}</span>
            <span className="mt-0.5 block text-[11px] font-semibold text-ink-soft">{s.settlements.length === 1 ? "payment left" : s.settlements.length ? "payments left" : "all square"}</span>
          </span>
        </div>
      </div>
      {payers.length > 0 && (
        <div className="relative mt-6">
          <p className="mb-2 text-[12px] font-semibold uppercase tracking-[0.12em] text-ink-mute">Who paid</p>
          <div className="flex h-3 gap-[2px] overflow-hidden rounded-full bg-haze-2" role="img" aria-label={payers.map((m) => `${m.name} paid ${money(m.paid, trip.currency)}`).join(", ")}>
            {payers.map((m) => <span key={m.member_id} className="h-full first:rounded-l-full last:rounded-r-full" style={{ width: `${(m.paid / s.total) * 100}%`, background: avatarHex(trip, m.member_id) }} title={`${m.name}: ${money(m.paid, trip.currency)}`} />)}
          </div>
          <ul className="num mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
            {payers.map((m) => (
              <li key={m.member_id} className="inline-flex items-center gap-1.5 text-ink-soft">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: avatarHex(trip, m.member_id) }} aria-hidden />{m.name}
                <span className="font-semibold text-ink">{money(m.paid, trip.currency)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
