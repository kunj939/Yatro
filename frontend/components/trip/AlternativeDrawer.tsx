"use client";

import { Check, Clock, Route, X } from "lucide-react";
import type { ItineraryItem } from "@/lib/types";
import { km, minutes, money, signedMoney } from "@/lib/format";
import { Modal } from "../Modal";

const CHECK_LABEL: Record<string, string> = {
  within_budget: "Within budget", open_at_visit: "Open at visit time", fits_schedule: "Fits the schedule", nearby: "Nearby",
  matches_preferences: "Matches your preferences", fits_time_window: "Fits your time window", keeps_current_plan: "Keeps the rest of the day",
};

export function AlternativeDrawer({ item, onClose, onUse, readOnly, busy, currency }: {
  item: ItineraryItem | null; onClose: () => void; onUse: (altId: string) => void; readOnly?: boolean; busy?: boolean; currency?: string;
}) {
  return (
    <Modal open={!!item} onClose={onClose} title={item ? `Instead of ${item.title}` : ""} side>
      {item && (
        <div>
          <p className="text-[16px] text-ink-soft">Each option below was tested in your actual day {item.time ? `at ${item.time}` : `in the ${String(item.meta?.when ?? "day").toLowerCase()}`}: open hours, the time before your next stop, and what is left of your budget.</p>
          <ul className="mt-5 space-y-4">
            {item.alternatives.map((a) => (
              <li key={a.id} className="rounded-2xl border border-line p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[18px] font-bold">{a.title}</p>
                    {a.reason && <p className="mt-1 text-[14px] text-ink-soft">{a.reason}</p>}
                  </div>
                  <div className="num shrink-0 text-right">
                    <p className="font-bold">{money(a.cost, currency)}</p>
                    <p className={`text-sm font-semibold ${a.cost_delta && a.cost_delta < 0 ? "text-teal-deep" : a.cost_delta ? "text-caution" : "text-ink-mute"}`}>{signedMoney(a.cost_delta, currency)}</p>
                  </div>
                </div>
                <div className="num mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[14px] text-ink-mute">
                  {a.distance_from_prev_m !== null && a.kind === "place" && <span className="inline-flex items-center gap-1"><Route className="h-3.5 w-3.5" aria-hidden />{km(a.distance_from_prev_m)} from previous stop</span>}
                  {!!a.time_delta_minutes && <span className="inline-flex items-center gap-1"><Clock className="h-3.5 w-3.5" aria-hidden />{a.time_delta_minutes > 0 ? "+" : "−"}{minutes(Math.abs(a.time_delta_minutes))}{a.kind === "transport" ? "" : " in the day"}</span>}
                  {a.meta?.visit_start && <span>Visit {a.meta.visit_start}–{a.meta.visit_end}</span>}
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {Object.entries(a.checks).map(([k, ok]) => (
                    <span key={k} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${ok ? "bg-teal-soft text-teal-deep" : "bg-haze-2 text-ink-mute"}`}>
                      {ok ? <Check className="h-3 w-3" aria-hidden /> : <X className="h-3 w-3" aria-hidden />}{CHECK_LABEL[k] ?? k}
                    </span>
                  ))}
                </div>
                <button type="button" className="btn-dark mt-4 w-full" disabled={busy} onClick={() => onUse(a.id)}>
                  {readOnly ? "Sign in to use this" : `Use ${a.title}`}
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-5 text-sm text-ink-mute">Swapping re-plans the day around your choice. Other stops may shift slightly to keep the timing right.</p>
        </div>
      )}
    </Modal>
  );
}