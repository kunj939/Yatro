import type { Itinerary, ItineraryItem } from "./types";

/** Where a stop's details came from, in words a traveller understands, with the figure that backs it. */
export function placeSource(item: ItineraryItem): { label: string; detail: string | null } | null {
  const m = item.meta ?? {};
  if (m.source === "google") {
    const count = typeof m.rating_count === "number" ? ` · ${compact(m.rating_count)} reviews` : "";
    return { label: "Google Maps", detail: typeof m.rating === "number" ? `★ ${m.rating}${count}` : null };
  }
  if (m.source === "wikidata") {
    return { label: "Wikipedia", detail: m.wiki_links ? `on Wikipedia in ${m.wiki_links} language${m.wiki_links === 1 ? "" : "s"}` : null };
  }
  if (m.source === "demo" || m.source === "sim") return { label: "Sample data", detail: null };
  return null;
}

function compact(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "")}k` : String(n);
}

/** The sources one plan actually used, for the "Where this data comes from" card. */
export function planSources(it: Itinerary): { what: string; from: string }[] {
  const items = it.days.flatMap((d) => d.items);
  const has = (src: string) => items.some((i) => i.meta?.source === src || (src === "wikidata" && i.meta?.wiki_links));
  const out: { what: string; from: string }[] = [];
  if (has("google")) out.push({ what: "Places, ratings and opening hours", from: "Google Maps" });
  if (has("wikidata")) out.push({ what: "Famous sights and how well known they are", from: "Wikidata (Wikipedia)" });
  const train = items.find((i) => i.kind === "transport" && i.meta?.mode === "train");
  if (train) out.push({ what: "Train times", from: train.meta?.provenance_label ?? "Indian Railways timetable data, labelled on each train" });
  if (it.weather) out.push({ what: "Weather forecast", from: "Open-Meteo.com" });
  out.push({ what: "Costs", from: "Yatro estimates, unless marked Verified" });
  return out;
}
