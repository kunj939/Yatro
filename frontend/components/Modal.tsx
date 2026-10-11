"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";

export function Modal({ open, onClose, title, children, side = false }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode; side?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    setTimeout(() => ref.current?.querySelector<HTMLElement>("input,button,textarea,select")?.focus(), 30);
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; prev?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true" aria-label={title}>
      <button className="absolute inset-0 bg-night/55" aria-label="Close" onClick={onClose} />
      <div ref={ref} className={side
        ? "relative ml-auto flex h-full w-full max-w-lg flex-col overflow-y-auto bg-paper shadow-2xl"
        : "relative m-auto w-[calc(100%-2rem)] max-w-md rounded-2xl bg-paper p-6 shadow-2xl md:p-8"}>
        <div className={`flex items-start justify-between gap-4 ${side ? "sticky top-0 z-10 border-b border-line bg-paper px-6 py-5" : "mb-5"}`}>
          <h2 className="text-xl font-bold">{title}</h2>
          <button onClick={onClose} className="rounded-full p-1 text-ink-mute hover:bg-haze hover:text-ink" aria-label="Close"><X className="h-5 w-5" /></button>
        </div>
        <div className={side ? "flex-1 px-6 py-5" : ""}>{children}</div>
      </div>
    </div>
  );
}