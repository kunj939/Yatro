"use client";

import { useEffect, useRef, useState } from "react";
import { Check, LocateFixed, MapPin } from "lucide-react";
import { endpoints } from "@/lib/api";
import type { Suggestion } from "@/lib/types";

export interface LocationValue { name: string; place_id?: string | null; lat?: number | null; lng?: number | null }

export function LocationInput({ id, label, value, onChange, allowCurrent = false, placeholder }: {
  id: string; label: string; value: LocationValue; onChange: (v: LocationValue) => void; allowCurrent?: boolean; placeholder?: string;
}) {
  const [results, setResults] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [locating, setLocating] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  function type(text: string) {
    onChange({ name: text, place_id: null, lat: null, lng: null });
    clearTimeout(timer.current);
    if (text.trim().length < 2) { setResults([]); return; }
    timer.current = setTimeout(async () => {
      try {
        const r = await endpoints.autocomplete(text);
        setResults(r.results); setOpen(true); setActive(-1); setHint(r.message ?? null);
      } catch { setResults([]); }
    }, 220);
  }

  function pick(s: Suggestion) {
    onChange({ name: s.label, place_id: s.place_id, lat: s.lat, lng: s.lng });
    setOpen(false); setResults([]);
  }

  function useCurrent() {
    if (!navigator.geolocation) { setHint("Location isn't available in this browser. Type your city instead."); return; }
    setLocating(true); setHint(null);
    navigator.geolocation.getCurrentPosition(async (pos) => {
      try {
        const r = await endpoints.reverse(pos.coords.latitude, pos.coords.longitude);
        if (r.result) onChange({ name: r.result.label, place_id: r.result.place_id, lat: r.result.lat, lng: r.result.lng });
        else setHint("We couldn't name your location. Type your city instead.");
      } catch { setHint("We couldn't name your location. Type your city instead."); }
      setLocating(false);
    }, () => { setLocating(false); setHint("Location permission was denied. Type your city instead."); }, { timeout: 10000 });
  }

  return (
    <div className="relative">
      <div className="mb-1.5 flex items-center justify-between">
        <label htmlFor={id} className="text-sm font-semibold">{label}</label>
        {allowCurrent && (
          <button type="button" onClick={useCurrent} className="inline-flex items-center gap-1.5 text-sm font-semibold text-signal-deep hover:text-ink" disabled={locating}>
            <LocateFixed className="h-4 w-4" aria-hidden />{locating ? "Locating…" : "Use my location"}
          </button>
        )}
      </div>
      <div className="relative">
        <MapPin className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-mute" aria-hidden />
        <input id={id} className="field py-3.5 pl-11 pr-20" value={value.name} placeholder={placeholder} autoComplete="off"
               role="combobox" aria-expanded={open && results.length > 0} aria-controls={`${id}-list`} aria-autocomplete="list"
               onChange={(e) => type(e.target.value)} onFocus={() => results.length && setOpen(true)}
               onBlur={() => setTimeout(() => setOpen(false), 150)}
               onKeyDown={(e) => {
                 if (!open || !results.length) return;
                 if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(results.length - 1, a + 1)); }
                 if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
                 if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(results[active]); }
               }} />
        {value.place_id && (
          <span className="absolute right-3 top-1/2 inline-flex -translate-y-1/2 items-center gap-1 rounded-full bg-teal-soft px-2 py-0.5 text-xs font-semibold text-teal-deep">
            <Check className="h-3 w-3" aria-hidden />Found
          </span>
        )}
      </div>
      {open && results.length > 0 && (
        <ul id={`${id}-list`} role="listbox" className="sheet-in absolute z-30 mt-1.5 w-full overflow-hidden rounded-xl border border-line bg-paper p-1 shadow-[0_24px_60px_-20px_rgb(var(--shadow)/0.5)]">
          {results.map((s, i) => (
            <li key={s.place_id} role="option" aria-selected={i === active}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(s)}
                      className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${i === active ? "bg-haze" : "hover:bg-haze"}`}>
                <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-signal" aria-hidden />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{s.label}</span>
                  {s.secondary && <span className="block truncate text-sm text-ink-mute">{s.secondary}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {hint && <p className="mt-1.5 text-sm text-ink-mute">{hint}</p>}
    </div>
  );
}