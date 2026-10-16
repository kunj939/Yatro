// The numbers behind the budget charts, shared by the Budget tab and the trip PDF so both tell the same story.
// Category colours: one fixed order (never re-ranked), checked for colour-blind separation with the dataviz validator:
// light = the site's white cards, dark = the PDF's ink pages. Text never wears these colours; a swatch beside it does.
import type { BudgetBreakdown } from "./types";

export const CATS = [
  { key: "stay", label: "Hotel", light: "#659600", dark: "#78a61a" },
  { key: "food", label: "Food", light: "#2a78d6", dark: "#3987e5" },
  { key: "activities", label: "Activities", light: "#eb6834", dark: "#d95926" },
  { key: "local", label: "Local travel", light: "#6250d6", dark: "#9085e9" },
  { key: "transport", label: "Transport", light: "#eda100", dark: "#c98500" },
] as const;
export type CatKey = (typeof CATS)[number]["key"];
export const BUFFER = { key: "buffer", label: "Safety buffer", light: "#c9cdc6", dark: "#3a423d" };

export interface Slice { key: string; label: string; amount: number; light: string; dark: string }

/** Where the money goes, from the plan's own budget breakdown. Fixed category order; the buffer last. */
export function slicesFromBudget(b: BudgetBreakdown): Slice[] {
  const by = Object.fromEntries(b.categories.map((c) => [c.key, c.amount]));
  const out: Slice[] = CATS.map((c) => ({ ...c, amount: by[c.key] ?? 0 }));
  const buffer = by.buffer ?? Math.max(0, b.planned - b.subtotal);
  return [...out, { ...BUFFER, amount: buffer }].filter((s) => s.amount > 0);
}

type DayLike = { index: number; date: string; label: string; items: { kind: string; cost: number | null; travel_cost: number | null }[] };

const KIND_CAT: Record<string, CatKey> = { transport: "transport", stay: "stay", meal: "food", activity: "activities", sunset: "activities" };

/** What each day costs, by category, from the plan's own rows. The hotel is spread over the nights it covers
 *  (paid once, used every night), and the cab or walk to each stop counts as local travel. */
export function dailySpend(days: DayLike[]) {
  const nights = days.filter((d, n) => n < days.length - 1 && d.items.some((i) => i.kind === "stay")).map((d) => d.index);
  const stayTotal = days.reduce((a, d) => a + d.items.filter((i) => i.kind === "stay").reduce((s, i) => s + (i.cost || 0), 0), 0);
  return days.map((d) => {
    const parts: Record<CatKey, number> = { stay: 0, food: 0, activities: 0, local: 0, transport: 0 };
    for (const i of d.items) {
      const k = KIND_CAT[i.kind];
      if (k && k !== "stay") parts[k] += i.cost || 0;
      parts[i.kind === "transport" || i.kind === "arrival" ? "transport" : "local"] += i.travel_cost || 0;
    }
    if (nights.length) parts.stay = nights.includes(d.index) ? stayTotal / nights.length : 0;
    else parts.stay = d.items.filter((i) => i.kind === "stay").reduce((s, i) => s + (i.cost || 0), 0);
    const total = Object.values(parts).reduce((a, v) => a + v, 0);
    return { index: d.index, date: d.date, label: d.label, parts, total };
  });
}

/** Where the money goes, worked out from the rows when the full breakdown isn't to hand (a shared plan's PDF). */
export function slicesFromDays(days: DayLike[]): Slice[] {
  const sum: Record<string, number> = {};
  for (const d of dailySpend(days)) for (const [k, v] of Object.entries(d.parts)) sum[k] = (sum[k] ?? 0) + v;
  return CATS.map((c) => ({ ...c, amount: Math.round(sum[c.key] ?? 0) })).filter((s) => s.amount > 0);
}

/** An SVG path for one ring segment from angle a0 to a1 (radians, 0 = 12 o'clock, clockwise). */
export function arc(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const p = (r: number, a: number) => `${(cx + r * Math.sin(a)).toFixed(2)} ${(cy - r * Math.cos(a)).toFixed(2)}`;
  const big = a1 - a0 > Math.PI ? 1 : 0;
  return `M${p(r1, a0)}A${r1} ${r1} 0 ${big} 1 ${p(r1, a1)}L${p(r0, a1)}A${r0} ${r0} 0 ${big} 0 ${p(r0, a0)}Z`;
}

/** Ring segments for slices, with a gap of `gap` (in the ring's own units, along the outer edge) between them. */
export function rings(slices: Slice[], cx: number, cy: number, r0: number, r1: number, gap = 2) {
  const total = slices.reduce((a, s) => a + s.amount, 0) || 1;
  const pad = slices.length > 1 ? gap / r1 / 2 : 0;
  let a = 0;
  return slices.map((s) => {
    const span = (s.amount / total) * Math.PI * 2;
    const seg = { ...s, share: s.amount / total, d: arc(cx, cy, r0, r1, a + pad, Math.max(a + pad + 0.001, a + span - pad)) };
    a += span;
    return seg;
  });
}

/** Clean axis steps for a column chart: 0 and up to four round ticks covering `max`. */
export function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 4, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step);
}

/** Short axis numbers: 0 / 5K / 1.2L in rupees (lakhs, as people say them), 0 / 5K / 1.2M elsewhere. */
export function compact(v: number, currency = "INR"): string {
  const one = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(1).replace(/\.0$/, ""));
  if (currency === "INR" && v >= 100000) return `${one(v / 100000)}L`;
  if (currency !== "INR" && v >= 1000000) return `${one(v / 1000000)}M`;
  if (v >= 1000) return `${one(v / 1000)}K`;
  return String(Math.round(v));
}
