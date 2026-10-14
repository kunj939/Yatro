import type { Itinerary, ItineraryItem, Trip } from "./types";
import { niceDate } from "./format";

/** Links that hand the plan to the apps people already use on the trip (ADR 0015): Google Maps for getting around,
 *  Google My Maps for the whole trip on one map. Built from the plan's own coordinates; nothing is sent anywhere. */

type Pt = { lat: number; lng: number; item: ItineraryItem };
const PLACE_KINDS = new Set(["activity", "meal", "sunset", "stay"]);

function point(item: ItineraryItem): Pt | null {
  const { lat, lng } = item.meta ?? {};
  return typeof lat === "number" && typeof lng === "number" ? { lat, lng, item } : null;
}

/** Turn-by-turn to one stop, from wherever the phone is. */
export function directionsUrl(item: ItineraryItem): string | null {
  const p = point(item);
  return p ? `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lng}` : null;
}

/** The day's stops in order as one Google Maps route. Maps takes at most 9 stops between the start and the end, so a
 *  very full day is cut to its first 11 places (the rest are still one tap away on each stop). */
export function dayRouteUrl(items: ItineraryItem[]): string | null {
  const pts = items.filter((i) => PLACE_KINDS.has(i.kind)).map(point).filter((p): p is Pt => !!p);
  const seen = new Set<string>();
  const uniq = pts.filter((p) => { const k = `${p.lat.toFixed(5)},${p.lng.toFixed(5)}`; return seen.has(k) ? false : (seen.add(k), true); }).slice(0, 11);
  if (uniq.length < 2) return null;
  const at = (p: Pt) => `${p.lat},${p.lng}`;
  const middle = uniq.slice(1, -1).map(at).join("|");
  return `https://www.google.com/maps/dir/?api=1&origin=${at(uniq[0])}&destination=${at(uniq[uniq.length - 1])}`
    + (middle ? `&waypoints=${encodeURIComponent(middle)}` : "");
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// One colour per day, repeating (KML colours are aabbggrr)
const DAY_COLOURS = ["ff00a165", "ffe6872f", "ff3a87e8", "ffa04ae0", "ff2a9ad8", "ff4c4cd6", "ff8e8e14"];

/** The whole plan as a KML file for Google My Maps: a folder per day, a pin per stop, a line through each day's stops. */
export function tripKml(trip: Trip, it: Itinerary): string {
  const styles = DAY_COLOURS.map((c, i) =>
    `<Style id="d${i}"><IconStyle><color>${c}</color></IconStyle><LineStyle><color>${c}</color><width>4</width></LineStyle></Style>`).join("");
  const folders = it.days.map((d, n) => {
    const pts = d.items.filter((i) => PLACE_KINDS.has(i.kind)).map(point).filter((p): p is Pt => !!p);
    if (!pts.length) return "";
    const style = `#d${n % DAY_COLOURS.length}`;
    const pins = pts.map((p, i) => `<Placemark><name>${esc(`${i + 1}. ${p.item.title}`)}</name><styleUrl>${style}</styleUrl>`
      + `<description>${esc([`Day ${n + 1}${p.item.time ? `, ${p.item.time}` : ""}`, p.item.subtitle, p.item.meta?.address].filter(Boolean).join("\n"))}</description>`
      + `<Point><coordinates>${p.lng},${p.lat},0</coordinates></Point></Placemark>`).join("");
    const line = pts.length > 1
      ? `<Placemark><name>${esc(`Day ${n + 1} route`)}</name><styleUrl>${style}</styleUrl><LineString><tessellate>1</tessellate>`
        + `<coordinates>${pts.map((p) => `${p.lng},${p.lat},0`).join(" ")}</coordinates></LineString></Placemark>` : "";
    return `<Folder><name>${esc(`Day ${n + 1} · ${niceDate(d.date)}`)}</name>${pins}${line}</Folder>`;
  }).join("");
  return `<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>`
    + `<name>${esc(`${it.title || trip.title} (Yaitro)`)}</name>${styles}${folders}</Document></kml>`;
}

export function saveFile(name: string, body: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
