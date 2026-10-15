/** A public-transport leg as the plan stores it (ADR 0019): what to take, the line, where to get on and off. */
export type TransitStep = { vehicle: string; line: string | null; from: string | null; to: string | null; stops: number | null; headsign: string | null };
export type Transit = { steps: TransitStep[]; fare: number | null; currency: string | null };

const VEHICLE: Record<string, string> = {
  metro: "Metro", bus: "Bus", "local train": "Local train", train: "Train", tram: "Tram", ferry: "Ferry", "shared taxi": "Shared taxi",
};

export const vehicleLabel = (v: string) => VEHICLE[v] ?? "Bus";

/** "Metro Yellow Line" / "Bus 507" / "Local train". */
export function stepName(s: TransitStep): string {
  return s.line ? `${vehicleLabel(s.vehicle)} ${s.line}` : vehicleLabel(s.vehicle);
}

/** "Rajiv Chowk → Hauz Khas · 7 stops", leaving out what the timetable didn't name. */
export function stepRoute(s: TransitStep): string {
  const where = s.from && s.to ? `${s.from} → ${s.to}` : s.to ? `to ${s.to}` : s.from ? `from ${s.from}` : "";
  const stops = s.stops ? `${s.stops} stop${s.stops === 1 ? "" : "s"}` : "";
  return [where, stops].filter(Boolean).join(" · ");
}

/** One line for a compact row: "Metro Yellow Line from Rajiv Chowk, then Bus 507". */
export function transitSummary(t: Transit | null | undefined): string | null {
  if (!t?.steps?.length) return null;
  const [first, ...rest] = t.steps;
  return `${stepName(first)}${first.from ? ` from ${first.from}` : ""}${rest.length ? `, then ${rest.map(stepName).join(", then ")}` : ""}`;
}
