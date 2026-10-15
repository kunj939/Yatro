import type { ItineraryItem, Trip } from "./types";

/** A map for each day of the PDF: Google Static Maps with the day's stops numbered in order and the routes between them
 *  drawn. Built from the owner's own trip (the public view carries no coordinates), only from Google content that is
 *  still current (I6: an expired stop has no coordinates and is left off). Each image is fetched here first, so a key
 *  without the Static Maps API, or no network, just means a PDF without maps, never a failed PDF. */
const KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY || "";
const STATIC = "https://maps.googleapis.com/maps/api/staticmap";
const MAPPED = new Set(["activity", "meal", "sunset", "stay"]);
const LABELS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const MAX_URL = 8000;

export type PdfMaps = { maps: Record<number, string>; numbers: Record<string, number> };

const point = (i: ItineraryItem) =>
  typeof i.meta?.lat === "number" && typeof i.meta?.lng === "number" && !i.meta?.google_expired
    ? `${i.meta.lat.toFixed(5)},${i.meta.lng.toFixed(5)}` : null;

function dayUrl(items: ItineraryItem[], numbers: Record<string, number>, key: string): string | null {
  const stops: { item: ItineraryItem; at: string }[] = [];
  for (const item of items) {
    const at = MAPPED.has(item.kind) ? point(item) : null;
    if (at && stops[stops.length - 1]?.at !== at) stops.push({ item, at });  // the hotel at both ends of a day once
  }
  if (!stops.length) return null;
  const base = new URLSearchParams({ size: "640x300", scale: "2", maptype: "roadmap", key });
  base.append("style", "feature:poi|visibility:off");
  base.append("style", "feature:transit|visibility:simplified");
  stops.forEach(({ item, at }, n) => {
    numbers[item.id] = n + 1;
    base.append("markers", `size:mid|color:0x659600|label:${LABELS[n] ?? ""}|${at}`);
  });
  const roads = new URLSearchParams(base);
  for (const item of items) {
    if (item.polyline && MAPPED.has(item.kind)) roads.append("path", `weight:4|color:0x4e7400cc|enc:${item.polyline}`);
  }
  const withRoads = `${STATIC}?${roads}`;
  if (withRoads.length <= MAX_URL && roads.has("path")) return withRoads;
  if (stops.length > 1) base.append("path", `weight:3|color:0x4e740099|${stops.map((s) => s.at).join("|")}`);  // straight lines
  return `${STATIC}?${base}`;
}

async function asDataUrl(url: string): Promise<string | null> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    if (!r.ok || !(r.headers.get("content-type") || "").startsWith("image/")) return null;
    const blob = await r.blob();
    return await new Promise((resolve) => {
      const fr = new FileReader();
      fr.onload = () => resolve(typeof fr.result === "string" ? fr.result : null);
      fr.onerror = () => resolve(null);
      fr.readAsDataURL(blob);
    });
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export async function pdfDayMaps(trip: Trip | undefined, fetchImage: (url: string) => Promise<string | null> = asDataUrl,
                                  key: string = KEY): Promise<PdfMaps> {
  const out: PdfMaps = { maps: {}, numbers: {} };
  const days = trip?.itinerary?.days ?? [];
  if (!key || !days.length) return out;
  const urls = days.map((d) => [d.index, dayUrl(d.items, out.numbers, key)] as const);
  const images = await Promise.all(urls.map(([, u]) => (u ? fetchImage(u) : Promise.resolve(null))));
  urls.forEach(([index], n) => { if (images[n]) out.maps[index] = images[n]!; });
  if (!Object.keys(out.maps).length) out.numbers = {};  // no maps: no numbers pointing at them
  return out;
}
