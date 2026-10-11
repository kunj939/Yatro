"use client";

import { useState } from "react";
import { Eye, Power, RotateCcw } from "lucide-react";
import { admin, type Controls, type NumberInfo, type SwitchInfo } from "@/lib/admin";
import { Card, ConfirmDialog, ErrorBox, Loading, PageHeader, Pill, RefreshButton, ago, errorText, useLoad } from "@/components/admin/ui";

const SOURCE: Record<string, string> = { admin: "Set here", env: "Set on the server", default: "Server default" };

function SwitchCard({ s, onAsk, onClear }: { s: SwitchInfo; onAsk: (s: SwitchInfo) => void; onClear: (s: SwitchInfo) => void }) {
  return (
    <li className={`rounded-2xl border p-4 md:p-5 ${s.on ? "border-caution/50 bg-caution-soft/60" : "border-line bg-paper"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-[17px] font-bold">{s.label}<Pill tone={s.on ? "warn" : "good"}>{s.on ? "Stopped" : "Running"}</Pill></p>
          <p className="mt-1 text-[14px] text-ink-soft">{s.effect}</p>
          <p className="mt-2 flex items-start gap-1.5 text-[13px] text-ink-mute"><Eye className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>People see: {s.people_see}</span></p>
          <p className="mt-2 text-[12.5px] text-ink-mute">
            {SOURCE[s.source]}{s.source === "admin" && s.by ? ` by ${s.by} ${ago(s.at)}` : ""}{s.reason ? ` · “${s.reason}”` : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button className={s.on ? "btn-primary" : "btn bg-danger text-white hover:brightness-110"} onClick={() => onAsk(s)}>
            <Power className="h-4 w-4" aria-hidden />{s.on ? "Turn back on" : "Stop this"}
          </button>
          {s.source === "admin" && <button className="btn-ghost" onClick={() => onClear(s)} title="Go back to the server's own setting"><RotateCcw className="h-4 w-4" aria-hidden />Reset</button>}
        </div>
      </div>
    </li>
  );
}

function NumberCard({ n, onSave, onClear }: { n: NumberInfo; onSave: (n: NumberInfo, v: number, reason: string) => Promise<void>; onClear: (n: NumberInfo) => void }) {
  const [v, setV] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const shown = v ?? String(n.value);
  const valid = shown !== "" && Number(shown) >= n.low && Number(shown) <= n.high;
  return (
    <li className="rounded-2xl border border-line bg-paper p-4 md:p-5">
      <p className="flex flex-wrap items-center gap-2 text-[17px] font-bold">{n.label}<Pill tone={n.source === "admin" ? "brand" : "neutral"}>{n.source === "admin" ? "Set here" : "Server default"}</Pill></p>
      <p className="mt-1 text-[14px] text-ink-soft">{n.hint}</p>
      <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={async (e) => { e.preventDefault(); setErr(null); try { await onSave(n, Number(shown), reason); setV(null); setReason(""); } catch (x) { setErr(errorText(x)); } }}>
        <input className="field num !w-24 !py-2 text-center" inputMode="numeric" aria-label={n.label} value={shown} onChange={(e) => setV(e.target.value.replace(/\D/g, ""))} />
        <input className="field !w-auto min-w-[150px] flex-1 !py-2" placeholder="Reason (optional)" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" />
        <button className="btn-primary !py-2" disabled={!valid || Number(shown) === n.value}>Save</button>
        {n.source === "admin" && <button type="button" className="btn-ghost !py-2" onClick={() => onClear(n)}><RotateCcw className="h-4 w-4" aria-hidden />Use {n.default}</button>}
      </form>
      {!valid && <p className="mt-2 text-[13px] text-danger">Use a whole number from {n.low} to {n.high}.</p>}
      {err && <p role="alert" className="mt-2 text-[13px] text-danger">{err}</p>}
      {n.source === "admin" && n.by && <p className="mt-2 text-[12.5px] text-ink-mute">Set by {n.by} {ago(n.at)}{n.reason ? ` · “${n.reason}”` : ""} · server default is {n.default}</p>}
    </li>
  );
}

export default function AdminControls() {
  const { data, error, busy, reload, setData } = useLoad(() => admin.controls(), []);
  const [ask, setAsk] = useState<SwitchInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const apply = (c: Controls, text: string) => { setData(c); setNotice(text); setProblem(null); };

  async function clear(key: string, text: string) {
    try { apply(await admin.clear(key), text); } catch (e) { setProblem(errorText(e)); }
  }

  return (
    <>
      <PageHeader title="Controls" text="Stop or change parts of the service right now, with no restart. Every change is attributed to you in the audit log, and reaches every server within a few seconds."
                  actions={<RefreshButton onClick={reload} busy={busy} />} />
      {error && <ErrorBox text={error} retry={reload} />}
      {problem && <div className="mb-4"><ErrorBox text={problem} /></div>}
      {notice && <p role="status" className="mb-4 rounded-xl bg-teal-soft px-4 py-3 text-[15px] font-medium text-teal-deep">{notice}</p>}
      {!data && !error && <Loading />}
      {data && (
        <div className="space-y-8">
          <Card title="Switches" aside={<span className="text-sm text-ink-mute">{data.switches.filter((s) => s.on).length} stopped</span>}>
            <ul className="space-y-3">
              {data.switches.map((s) => <SwitchCard key={s.name} s={s} onAsk={setAsk} onClear={(x) => clear(`switch:${x.name}`, `${x.label}: back to the server's own setting.`)} />)}
            </ul>
          </Card>
          <Card title="Numbers">
            <ul className="space-y-3">
              {data.numbers.map((n) => (
                <NumberCard key={n.key} n={n}
                            onSave={async (x, value, reason) => { apply(await admin.setNumber(x.key, value, reason || undefined), `${x.label} is now ${value}.`); }}
                            onClear={(x) => clear(x.key, `${x.label}: back to the server default (${x.default}).`)} />
              ))}
            </ul>
          </Card>
        </div>
      )}

      <ConfirmDialog open={!!ask} onClose={() => setAsk(null)} danger={!ask?.on} confirmLabel={ask?.on ? "Turn back on" : "Stop it"}
                     title={ask ? (ask.on ? `Turn ${ask.label.toLowerCase()} back on?` : `Stop ${ask.label.toLowerCase()}?`) : ""}
                     onConfirm={async (reason) => { if (!ask) return; apply(await admin.setSwitch(ask.name, !ask.on, reason || undefined), ask.on ? `${ask.label} is running again.` : `${ask.label} stopped.`); }}>
        {ask && (ask.on ? "It will start working again straight away." : <>{ask.effect} <span className="mt-2 block text-ink-mute">People see: {ask.people_see}</span></>)}
      </ConfirmDialog>
    </>
  );
}
