"use client";

import { useRef } from "react";

/** Six boxes for a one-time code. Typing moves on, Backspace moves back, and pasting or the phone's own code
 * autofill ("one-time-code") fills every box at once. The value is a string of up to six digits, never with gaps. */
export function OtpInput({ value, onChange, invalid, label, autoFocus }: {
  value: string; onChange: (v: string) => void; invalid?: boolean; label: string; autoFocus?: boolean;
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const focus = (i: number) => refs.current[Math.max(0, Math.min(5, i))]?.focus();

  function put(i: number, typed: string) {
    const digits = typed.replace(/\D/g, "");
    if (!digits) return;
    const next = (value.slice(0, i) + digits).slice(0, 6);
    onChange(next);
    focus(next.length);
  }

  return (
    <div className="flex gap-2" role="group" aria-label={label}>
      {Array.from({ length: 6 }, (_, i) => (
        <input key={i} ref={(el) => { refs.current[i] = el; }} value={value[i] ?? ""} inputMode="numeric" pattern="[0-9]*"
               autoComplete={i === 0 ? "one-time-code" : "off"} autoFocus={autoFocus && i === 0} aria-label={`${label}, digit ${i + 1}`}
               aria-invalid={invalid || undefined}
               className={`num h-12 w-11 rounded-lg border bg-paper text-center text-[22px] font-bold text-ink transition-colors focus:outline-none focus:ring-2 ${
                 invalid ? "border-danger focus:ring-danger/25" : "border-line focus:border-signal focus:ring-signal/25"}`}
               onFocus={(e) => { if (i > value.length) focus(value.length); else e.target.select(); }}
               onChange={(e) => put(i, e.target.value)}
               onPaste={(e) => { e.preventDefault(); onChange(e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6)); focus(5); }}
               onKeyDown={(e) => {
                 if (e.key === "Backspace") {
                   e.preventDefault();
                   if (value[i]) onChange(value.slice(0, i) + value.slice(i + 1));
                   else if (i > 0) { onChange(value.slice(0, i - 1)); focus(i - 1); }
                 } else if (e.key === "ArrowLeft") focus(i - 1);
                 else if (e.key === "ArrowRight") focus(i + 1);
               }} />
      ))}
    </div>
  );
}
