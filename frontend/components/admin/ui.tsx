"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, RefreshCw, Search } from "lucide-react";
import { Modal } from "../Modal";
import { ApiError } from "@/lib/api";

/** Small pieces every admin page uses, in the same look as the rest of the app (panels, chips, lime accent). */

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.details?.[0]?.message ? `${e.message} ${e.details[0].message}` : e.message;
  return e instanceof Error ? e.message : "Something went wrong.";
}

/** Loads something, keeps the last good answer on screen while it refreshes, and says when it fails. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const seq = useRef(0);
  const run = useCallback(async () => {
    const mine = ++seq.current;
    setBusy(true);
    try { const d = await load(); if (mine === seq.current) { setData(d); setError(null); } }
    catch (e) { if (mine === seq.current) setError(errorText(e)); }
    finally { if (mine === seq.current) setBusy(false); }
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { run(); }, [run]);
  return { data, error, busy, reload: run, setData };
}

export function PageHeader({ title, text, actions }: { title: string; text?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[28px] font-extrabold leading-tight tracking-tight md:text-[34px]">{title}</h1>
        {text && <p className="mt-1 max-w-2xl text-[15px] text-ink-soft">{text}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function RefreshButton({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return (
    <button type="button" onClick={onClick} className="btn-ghost !px-3 !py-2" aria-label="Refresh" disabled={busy}>
      <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} aria-hidden />
    </button>
  );
}

export function ErrorBox({ text, retry }: { text: string; retry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-2xl border border-danger/30 bg-danger-soft px-4 py-3 text-[15px] font-medium text-danger">
      <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden /><span className="min-w-0 flex-1">{text}</span>
      {retry && <button className="btn-ghost !px-3 !py-1.5 !text-sm" onClick={retry}>Try again</button>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="flex items-center gap-2 py-10 text-ink-mute" aria-busy="true"><Loader2 className="h-4 w-4 animate-spin" aria-hidden />{label}</div>;
}

export function Card({ title, aside, children, className = "" }: { title?: string; aside?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel min-w-0 p-5 md:p-6 ${className}`}>
      {(title || aside) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-lg font-bold">{title}</h2>}{aside}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES = {
  neutral: "bg-haze-2 text-ink-soft", good: "bg-teal-soft text-teal-deep", warn: "bg-caution-soft text-caution",
  bad: "bg-danger-soft text-danger", brand: "bg-signal-soft text-signal-deep",
} as const;
export function Pill({ tone = "neutral", children }: { tone?: keyof typeof TONES; children: React.ReactNode }) {
  return <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-semibold ${TONES[tone]}`}>{children}</span>;
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "bad" | "good" }) {
  return (
    <div className="panel min-w-0 p-4 md:p-5">
      <p className="text-[13px] font-medium text-ink-mute">{label}</p>
      <p className={`num mt-1 text-[30px] font-extrabold leading-none tracking-tight ${tone === "bad" ? "text-danger" : ""}`}>{value}</p>
      {sub && <p className="mt-2 text-[13px] text-ink-soft">{sub}</p>}
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="relative block min-w-[200px] flex-1">
      <span className="sr-only">{placeholder}</span>
      <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-mute" aria-hidden />
      <input className="field !py-2.5 pl-10" placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function Select({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: [string, string][]; label: string }) {
  return (
    <label className="block">
      <span className="sr-only">{label}</span>
      <select className="field !w-auto !py-2.5 pr-8" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

export function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total ? (page - 1) * size + 1 : 0;
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-[14px] text-ink-soft">
      <span className="num">{from}–{Math.min(total, page * size)} of {total}</span>
      <div className="flex items-center gap-2">
        <button className="btn-ghost !px-3 !py-1.5" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></button>
        <span className="num">Page {page} of {pages}</span>
        <button className="btn-ghost !px-3 !py-1.5" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></button>
      </div>
    </div>
  );
}

/** A confirmation that can ask for a reason (kept in the audit trail) and, for the dangerous ones, the thing to type. */
export function ConfirmDialog({ open, title, children, confirmLabel, danger = false, typeToConfirm, askReason = true, onClose, onConfirm }: {
  open: boolean; title: string; children: React.ReactNode; confirmLabel: string; danger?: boolean; typeToConfirm?: string; askReason?: boolean;
  onClose: () => void; onConfirm: (reason: string, typed: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open) { setReason(""); setTyped(""); setError(null); setBusy(false); } }, [open]);
  const ok = !typeToConfirm || typed.trim().toLowerCase() === typeToConfirm.toLowerCase();
  async function go(e: React.FormEvent) {
    e.preventDefault();
    if (!ok || busy) return;
    setBusy(true); setError(null);
    try { await onConfirm(reason.trim(), typed.trim()); onClose(); }
    catch (err) { setError(errorText(err)); setBusy(false); }
  }
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <form onSubmit={go} className="space-y-4">
        <div className="text-[15px] text-ink-soft">{children}</div>
        {typeToConfirm && (
          <label className="block">
            <span className="mb-1.5 block text-sm font-semibold">Type <span className="rounded bg-haze-2 px-1.5 py-0.5 font-mono text-[13px]">{typeToConfirm}</span> to confirm</span>
            <input className="field" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
          </label>
        )}
        {askReason && (
          <label className="block">
            <span className="mb-1.5 block text-sm font-semibold">Reason <span className="font-normal text-ink-mute">(optional, kept in the audit log)</span></span>
            <input className="field" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="e.g. support ticket 123" />
          </label>
        )}
        {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-sm font-medium text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className={danger ? "btn bg-danger text-white hover:brightness-110" : "btn-primary"} disabled={!ok || busy}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

/** Debounce a fast-changing value (a search box) so the server is asked once the typing pauses. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
