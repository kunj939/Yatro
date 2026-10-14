import { API_URL } from "./api";
import type { SharedTrip } from "./types";

/** The public view of a shared trip, fetched on the server for the link preview (WhatsApp, Telegram, iMessage…).
 * A preview crawler waits only a few seconds, so a slow or sleeping backend gives `null` and the generic Yaitro card
 * instead of no card at all. */
export async function fetchShared(token: string): Promise<SharedTrip | null> {
  try {
    const res = await fetch(`${API_URL}/api/share/${encodeURIComponent(token)}`, {
      signal: AbortSignal.timeout(8000),
      next: { revalidate: 300 },
    });
    return res.ok ? ((await res.json()) as SharedTrip) : null;
  } catch {
    return null;
  }
}

export function shortMoney(n: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 0 }).format(n);
  } catch {
    return `${currency} ${Math.round(n).toLocaleString("en-IN")}`;
  }
}

function day(d: string, withDay = false): string {
  const x = new Date(`${d}T00:00:00Z`);  // a calendar day: read and printed in UTC, so no timezone shifts it
  return x.toLocaleDateString("en-IN", { ...(withDay ? { weekday: "short" } : {}), day: "numeric", month: "short", timeZone: "UTC" });
}

export function dateSpan(t: SharedTrip["trip"]): string {
  return t.start_date === t.end_date ? day(t.start_date, true) : `${day(t.start_date)} – ${day(t.end_date)}`;
}

export function dayDate(d: string): string {
  return day(d, true);
}

/** "17 Oct – 22 Oct · 2 travellers · ₹51,000 · 28 stops": the line under the title in every preview. */
export function summaryLine(v: SharedTrip): string {
  const stops = v.itinerary.days.reduce((n, d) => n + d.items.filter((i) => i.kind !== "free").length, 0);
  const parts = [dateSpan(v.trip), `${v.trip.travellers} traveller${v.trip.travellers === 1 ? "" : "s"}`];
  if (v.itinerary.total_cost) parts.push(shortMoney(v.itinerary.total_cost, v.trip.currency));
  parts.push(`${stops} stops`);
  return `${parts.join(" · ")}. Open the full day-by-day plan.`;
}
