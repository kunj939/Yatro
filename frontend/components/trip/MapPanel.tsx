"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, MapPinned } from "lucide-react";
import type { ItineraryItem, Trip } from "@/lib/types";
import { decodePolyline } from "@/lib/polyline";
import { MODE_LABEL, km, minutes, niceDate } from "@/lib/format";

type LatLng = [number, number];
interface Stop { id: string; n: number; title: string; time: string; kind: string; at: LatLng; item: ItineraryItem }
interface Path { id: string; coords: LatLng[]; kind: "walk" | "road" | "intercity" }

const BROWSER_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY || "";

function ll(v: any): LatLng | null {
  if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number")) return [v[0], v[1]];
  return null;
}

/** The map's shapes for one day of the plan (`day`, its position in the list), or the whole trip when null. */
function buildGeometry(trip: Trip, day: number | null = null) {
  const days = trip.itinerary?.days ?? [];
  const items = (day === null ? days : days.slice(day, day + 1)).flatMap((d) => d.items);
  const stops: Stop[] = [];
  const cityPaths: Path[] = [];
  const journeyPaths: Path[] = [];
  const stations: { name: string; at: LatLng }[] = [];
  for (const item of items) {
    if (item.kind === "transport") {
      const from = ll(item.meta?.from), to = ll(item.meta?.to);
      if (from && to) {
        const route = decodePolyline(item.meta?.polyline);
        journeyPaths.push({ id: item.id, coords: route.length > 1 ? route : [from, to], kind: "intercity" });
        stations.push({ name: item.meta.from_name ?? "Departure", at: from }, { name: item.meta.to_name ?? "Arrival", at: to });
      }
    } else if (item.kind !== "arrival" && item.kind !== "free" && typeof item.meta?.lat === "number") {
      stops.push({ id: item.id, n: stops.length + 1, title: item.title, time: item.time ?? item.meta?.when, kind: item.kind, at: [item.meta.lat, item.meta.lng], item });
    }
    const leg = decodePolyline(item.polyline);
    if (leg.length > 1 && item.kind !== "arrival") {
      cityPaths.push({ id: `${item.id}-leg`, coords: leg, kind: item.travel_mode === "walk" ? "walk" : "road" });
    }
  }
  return { stops, cityPaths, journeyPaths, stations };
}

/** Fit lat/lng points into an SVG box using an equirectangular projection corrected for latitude. */
function projector(points: LatLng[], w: number, h: number, pad: number) {
  const lats = points.map((p) => p[0]), lngs = points.map((p) => p[1]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const k = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const spanX = Math.max((maxLng - minLng) * k, 0.004), spanY = Math.max(maxLat - minLat, 0.004);
  const s = Math.min((w - pad * 2) / spanX, (h - pad * 2) / spanY);
  const ox = (w - spanX * s) / 2, oy = (h - spanY * s) / 2;
  const cx = (minLng + maxLng) / 2, cy = (minLat + maxLat) / 2;
  return ([lat, lng]: LatLng): [number, number] => [
    ox + ((lng - cx) * k + spanX / 2) * s,
    oy + (spanY / 2 - (lat - cy)) * s,
  ];
}

function SvgMap({ trip, view, day, selected, onSelect }: { trip: Trip; view: "city" | "journey"; day: number | null; selected: string | null; onSelect: (id: string) => void }) {
  const W = 720, H = 460;
  const g = useMemo(() => buildGeometry(trip, view === "journey" ? null : day), [trip, view, day]);
  const origin = ll([trip.origin_detail.lat, trip.origin_detail.lng]);
  const dest = ll([trip.destination_detail.lat, trip.destination_detail.lng]);

  if (view === "journey") {
    const pts: LatLng[] = [...g.journeyPaths.flatMap((p) => p.coords), ...(origin ? [origin] : []), ...(dest ? [dest] : [])];
    if (pts.length < 2) return <Empty />;
    const P = projector(pts, W, H, 70);
    const labels = [
      ...(origin ? [{ name: trip.origin, at: origin, main: true }] : []),
      ...(dest ? [{ name: trip.destination, at: dest, main: true }] : []),
      ...dedupe(g.stations).map((s) => ({ ...s, main: false })),
    ];
    return (
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Journey from ${trip.origin} to ${trip.destination}`}>
        <MapBackdrop W={W} H={H} />
        {g.journeyPaths.map((p, i) => (
          <polyline key={p.id} points={p.coords.map((c) => P(c).join(",")).join(" ")} fill="none"
                    stroke={i === 0 ? "#101B36" : "#E8873A"} strokeWidth={i === 0 ? 6 : 3} strokeLinecap="round" strokeLinejoin="round"
                    strokeDasharray={i === 0 ? undefined : "10 8"} />
        ))}
        {labels.map((l, i) => {
          const [x, y] = P(l.at);
          return (
            <g key={i}>
              <circle cx={x} cy={y} r={l.main ? 9 : 5} fill={l.main ? "#E8873A" : "#fff"} stroke="#101B36" strokeWidth={2.5} />
              <text x={x + 14} y={y + (l.main ? -10 : 18)} fontSize={l.main ? 18 : 13} fontWeight={l.main ? 700 : 500} fill="#15213B"
                    paintOrder="stroke" stroke="#F2F5FA" strokeWidth={4}>{l.name}</text>
            </g>
          );
        })}
      </svg>
    );
  }

  const pts: LatLng[] = [...g.stops.map((s) => s.at), ...g.cityPaths.flatMap((p) => p.coords)];
  if (pts.length < 2) return <Empty />;
  const P = projector(pts, W, H, 44);
  const placed = spread(g.stops.map((s) => P(s.at)), 30);
  const near = (a: LatLng) => g.stops.some((s) => Math.abs(s.at[0] - a[0]) < 0.3 && Math.abs(s.at[1] - a[1]) < 0.3);
  const stations = dedupe(g.stations).filter((st) => near(st.at));
  const order = g.stops.map((s, i) => i).sort((a, b) => Number(g.stops[a].id === selected) - Number(g.stops[b].id === selected));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Stops in ${trip.destination}`}>
      <MapBackdrop W={W} H={H} />
      {g.cityPaths.map((p) => (
        <polyline key={p.id} points={p.coords.map((c) => P(c).join(",")).join(" ")} fill="none"
                  stroke={p.kind === "walk" ? "#0E8A6A" : "#101B36"} strokeWidth={p.kind === "walk" ? 3 : 3.5}
                  strokeDasharray={p.kind === "walk" ? "2 6" : undefined} strokeLinecap="round" strokeLinejoin="round" opacity={0.75} />
      ))}
      {stations.map((st) => {
        const [x, y] = P(st.at);
        return (
          <g key={st.name}>
            <rect x={x - 8} y={y - 8} width={16} height={16} rx={3} fill="#fff" stroke="#101B36" strokeWidth={2.5} />
            <text x={x} y={y + 26} textAnchor="middle" fontSize={13} fontWeight={600} fill="#5A6485" paintOrder="stroke" stroke="#F2F5FA" strokeWidth={4}>{st.name}</text>
          </g>
        );
      })}
      {order.map((i) => {
        const s = g.stops[i];
        const [tx, ty] = P(s.at);
        const [x, y] = placed[i];
        const on = selected === s.id;
        const right = x < W * 0.62;
        return (
          <g key={s.id} role="button" tabIndex={0} aria-label={`${s.n}. ${s.title} at ${s.time}`} className="cursor-pointer focus:outline-none"
             onClick={() => onSelect(s.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect(s.id); }}>
            {(Math.abs(x - tx) > 1 || Math.abs(y - ty) > 1) && <line x1={tx} y1={ty} x2={x} y2={y} stroke="#101B36" strokeWidth={1.2} opacity={0.5} />}
            <circle cx={x} cy={y} r={on ? 17 : 13} fill={s.kind === "sunset" ? "#E8873A" : s.kind === "meal" ? "#fff" : "#101B36"}
                    stroke={on ? "#E8873A" : "#101B36"} strokeWidth={on ? 4 : 2} />
            <text x={x} y={y + 4.5} textAnchor="middle" fontSize={13} fontWeight={700} fill={s.kind === "activity" || s.kind === "stay" ? "#fff" : "#101B36"}>{s.n}</text>
            {on && (
              <text x={right ? x + 24 : x - 24} y={y + 5} textAnchor={right ? "start" : "end"} fontSize={15} fontWeight={700} fill="#15213B"
                    paintOrder="stroke" stroke="#F2F5FA" strokeWidth={5}>{s.title}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** Nudge overlapping markers apart so clustered old-city stops stay readable. */
function spread(pts: [number, number][], min: number): [number, number][] {
  const out = pts.map(([x, y]) => [x, y] as [number, number]);
  for (let iter = 0; iter < 60; iter++) {
    let moved = false;
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const dx = out[j][0] - out[i][0], dy = out[j][1] - out[i][1];
        const d = Math.hypot(dx, dy);
        if (d < min) {
          const a = d > 0.01 ? Math.atan2(dy, dx) : (j * 2.39996);
          const push = (min - d) / 2;
          out[i][0] -= Math.cos(a) * push; out[i][1] -= Math.sin(a) * push;
          out[j][0] += Math.cos(a) * push; out[j][1] += Math.sin(a) * push;
          moved = true;
        }
      }
    }
    if (!moved) break;
  }
  return out;
}

function dedupe(s: { name: string; at: LatLng }[]) {
  const seen = new Set<string>();
  return s.filter((x) => (seen.has(x.name) ? false : (seen.add(x.name), true)));
}

function MapBackdrop({ W, H }: { W: number; H: number }) {
  return (
    <>
      <defs>
        <pattern id="grid" width="36" height="36" patternUnits="userSpaceOnUse">
          <path d="M36 0H0V36" fill="none" stroke="#DCE3EF" strokeWidth="0.8" />
        </pattern>
      </defs>
      <rect width={W} height={H} fill="#F2F5FA" />
      <rect width={W} height={H} fill="url(#grid)" />
    </>
  );
}

function Empty() {
  return (
    <div className="grid h-72 place-items-center text-center text-ink-mute">
      <p><MapPinned className="mx-auto mb-2 h-8 w-8" aria-hidden />No places to show on the map here.</p>
    </div>
  );
}

/** A numbered stop marker as an SVG picture: ink for sights, white for meals, orange for sunsets; the chosen one is
 *  bigger with a lime ring, so it reads like the list beside the map. */
function pin(n: number, kind: string, on: boolean): { url: string; size: number } {
  const size = on ? 44 : 32, r = on ? 15 : 12, c = size / 2;
  const fill = kind === "sunset" ? "#E8873A" : kind === "meal" ? "#ffffff" : "#101311";
  const ink = kind === "meal" ? "#101311" : "#ffffff";
  const ring = on ? `<circle cx="${c}" cy="${c}" r="${r + 5}" fill="#beff4a" opacity="0.9"/>` : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">${ring}`
    + `<circle cx="${c}" cy="${c}" r="${r}" fill="${fill}" stroke="#101311" stroke-width="2.5"/>`
    + `<text x="${c}" y="${c + 4.5}" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-size="${on ? 15 : 13}" font-weight="700" fill="${ink}">${n}</text></svg>`;
  return { url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`, size };
}

// A quiet base map: shops, businesses and transit icons off, so the plan's own stops are what you see.
const MAP_STYLE = [
  { featureType: "poi", elementType: "labels.icon", stylers: [{ visibility: "off" }] },
  { featureType: "poi.business", stylers: [{ visibility: "off" }] },
  { featureType: "transit", elementType: "labels.icon", stylers: [{ visibility: "off" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ saturation: -60 }] },
  { featureType: "water", elementType: "geometry.fill", stylers: [{ color: "#cfe4ef" }] },
  { featureType: "landscape", elementType: "geometry.fill", stylers: [{ color: "#f4f5f1" }] },
];

function loadGoogle(): Promise<any> {
  const w = window as any;
  if (w.google?.maps) return Promise.resolve(w.google.maps);
  return new Promise((resolve, reject) => {
    const id = "gmaps-js";
    let script = document.getElementById(id) as HTMLScriptElement | null;
    if (!script) {
      script = document.createElement("script");
      script.id = id; script.async = true;
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(BROWSER_KEY)}`;
      document.head.appendChild(script);
    }
    script.addEventListener("load", () => (w.google?.maps ? resolve(w.google.maps) : reject(new Error("no maps"))));
    script.addEventListener("error", () => reject(new Error("maps script failed")));
  });
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

/** The real Google map, used when a browser key is configured: the day's stops and legs, or the whole journey. */
function GoogleMap({ trip, view, day, selected, onSelect }: { trip: Trip; view: "city" | "journey"; day: number | null; selected: string | null; onSelect: (id: string) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const live = useRef<{ map: any; markers: Map<string, any>; info: any } | null>(null);
  const [failed, setFailed] = useState(false);
  const g = useMemo(() => buildGeometry(trip, view === "journey" ? null : day), [trip, view, day]);
  const pick = useRef(onSelect);
  pick.current = onSelect;

  useEffect(() => {
    let cancelled = false;
    // Google calls this when the key is wrong or not allowed on this site: show the route sketch instead of its error page
    (window as any).gm_authFailure = () => !cancelled && setFailed(true);
    loadGoogle().then((gm) => {
      if (cancelled || !el.current) return;
      const map = new gm.Map(el.current, {
        disableDefaultUI: true, zoomControl: true, fullscreenControl: true, clickableIcons: false,
        gestureHandling: "cooperative", styles: MAP_STYLE,
      });
      const bounds = new gm.LatLngBounds();
      const markers = new Map<string, any>();
      const at = ([lat, lng]: LatLng) => ({ lat, lng });
      if (view === "journey") {
        g.journeyPaths.forEach((p, i) => {
          p.coords.forEach((c) => bounds.extend(at(c)));
          new gm.Polyline({
            map, path: p.coords.map(at), strokeColor: i === 0 ? "#101311" : "#659600", strokeOpacity: i === 0 ? 0.9 : 0, strokeWeight: 5,
            icons: i === 0 ? [] : [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 1, strokeColor: "#659600", scale: 4 }, offset: "0", repeat: "16px" }],
          });
        });
        const ends = [
          [trip.origin, ll([trip.origin_detail.lat, trip.origin_detail.lng])],
          [trip.destination, ll([trip.destination_detail.lat, trip.destination_detail.lng])],
        ] as [string, LatLng | null][];
        ends.forEach(([name, p], i) => {
          if (!p) return;
          bounds.extend(at(p));
          const icon = pin(i + 1, i ? "sunset" : "activity", false);
          new gm.Marker({ map, position: at(p), title: name, icon: { url: icon.url, scaledSize: new gm.Size(icon.size, icon.size), anchor: new gm.Point(icon.size / 2, icon.size / 2) } });
        });
      } else {
        g.cityPaths.forEach((p) => new gm.Polyline({
          map, path: p.coords.map(at), strokeColor: p.kind === "walk" ? "#0E8A6A" : "#101311",
          strokeOpacity: p.kind === "walk" ? 0 : 0.75, strokeWeight: 4,
          icons: p.kind === "walk" ? [{ icon: { path: gm.SymbolPath.CIRCLE, fillOpacity: 1, fillColor: "#0E8A6A", strokeOpacity: 0, scale: 2.5 }, offset: "0", repeat: "11px" }] : [],
        }));
        g.stops.forEach((s) => {
          const pos = at(s.at);
          bounds.extend(pos);
          const icon = pin(s.n, s.kind, false);
          const m = new gm.Marker({ map, position: pos, title: `${s.n}. ${s.title}`, zIndex: s.n,
            icon: { url: icon.url, scaledSize: new gm.Size(icon.size, icon.size), anchor: new gm.Point(icon.size / 2, icon.size / 2) } });
          m.addListener("click", () => pick.current(s.id));
          markers.set(s.id, m);
        });
      }
      if (!bounds.isEmpty()) map.fitBounds(bounds, 56);
      live.current = { map, markers, info: new gm.InfoWindow({ disableAutoPan: false }) };
    }).catch(() => !cancelled && setFailed(true));
    return () => { cancelled = true; live.current = null; };
  }, [g, view, trip]);

  // The chosen stop: a bigger marker, a small card with its time and address, and a link to it in Google Maps.
  useEffect(() => {
    const L = live.current, gm = (window as any).google?.maps;
    if (!L || !gm || view !== "city") return;
    g.stops.forEach((s) => {
      const m = L.markers.get(s.id);
      if (!m) return;
      const icon = pin(s.n, s.kind, s.id === selected);
      m.setIcon({ url: icon.url, scaledSize: new gm.Size(icon.size, icon.size), anchor: new gm.Point(icon.size / 2, icon.size / 2) });
      m.setZIndex(s.id === selected ? 999 : s.n);
    });
    const s = g.stops.find((x) => x.id === selected);
    const m = s && L.markers.get(s.id);
    if (!s || !m) { L.info.close(); return; }
    const link = s.item.meta?.maps_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${s.title}, ${trip.destination}`)}${s.item.place_id ? `&query_place_id=${encodeURIComponent(s.item.place_id)}` : ""}`;
    L.info.setContent(`<div style="font-family:Inter,system-ui,sans-serif;max-width:220px;padding:2px 2px 0">
      <div style="font-weight:700;font-size:14px;color:#101311">${s.n}. ${esc(s.title)}</div>
      <div style="font-size:12px;color:#666d68;margin-top:2px">${esc(s.time || "")}${s.item.meta?.address ? ` · ${esc(String(s.item.meta.address))}` : ""}</div>
      <a href="${esc(link)}" target="_blank" rel="noopener noreferrer" style="display:inline-block;margin-top:8px;font-size:12px;font-weight:700;color:#4e7400;text-decoration:none">Open in Google Maps &rarr;</a></div>`);
    L.info.open({ map: L.map, anchor: m });
    L.map.panTo(m.getPosition());
  }, [selected, g, view, trip.destination]);

  if (failed) return <SvgMap trip={trip} view={view} day={day} selected={selected} onSelect={onSelect} />;
  return <div ref={el} className="h-[480px] w-full bg-haze" aria-label={view === "journey" ? `Journey from ${trip.origin} to ${trip.destination}` : `Map of stops in ${trip.destination}`} />;
}

export function MapPanel({ trip }: { trip: Trip }) {
  // One day at a time: a long trip's hundred stops on one sketch can't be read. Starts on the first day with places.
  const days = trip.itinerary?.days ?? [];
  const multi = days.length > 1;
  const [day, setDay] = useState(() => Math.max(0, days.findIndex((d) => d.items.some((i) => i.kind === "activity" || i.kind === "meal"))));
  const g = useMemo(() => buildGeometry(trip, multi ? day : null), [trip, multi, day]);
  const [view, setView] = useState<"city" | "journey">("city");
  const [selected, setSelected] = useState<string | null>(g.stops[0]?.id ?? null);
  useEffect(() => { setSelected(g.stops[0]?.id ?? null); }, [g]);
  const go = (n: number) => { setDay(Math.min(days.length - 1, Math.max(0, n))); setView("city"); };
  const sel = g.stops.find((s) => s.id === selected);
  const hasJourney = g.journeyPaths.length > 0;
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
      <div className="panel overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex gap-1.5" role="tablist" aria-label="Map view">
            <button role="tab" aria-selected={view === "city"} onClick={() => setView("city")} className={view === "city" ? "chip-on" : "chip-off"}>In {trip.destination}</button>
            {hasJourney && <button role="tab" aria-selected={view === "journey"} onClick={() => setView("journey")} className={view === "journey" ? "chip-on" : "chip-off"}>Whole journey</button>}
          </div>
          {multi && view === "city" ? (
            <div className="flex items-center gap-1.5" role="group" aria-label="Day on the map">
              <button type="button" onClick={() => go(day - 1)} disabled={day === 0} aria-label="Previous day"
                      className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper text-ink transition-colors hover:border-ink/30 disabled:opacity-40"><ChevronLeft className="h-4 w-4" /></button>
              <label className="sr-only" htmlFor="map-day">Day</label>
              <select id="map-day" value={day} onChange={(e) => go(Number(e.target.value))}
                      className="h-9 rounded-full border border-line bg-paper px-3 text-[14px] font-semibold text-ink focus:border-signal focus:outline-none">
                {days.map((d, n) => <option key={d.index} value={n}>Day {n + 1} · {niceDate(d.date)}</option>)}
              </select>
              <button type="button" onClick={() => go(day + 1)} disabled={day === days.length - 1} aria-label="Next day"
                      className="grid h-9 w-9 place-items-center rounded-full border border-line bg-paper text-ink transition-colors hover:border-ink/30 disabled:opacity-40"><ChevronRight className="h-4 w-4" /></button>
            </div>
          ) : (
            <p className="text-[13px] text-ink-mute">{BROWSER_KEY ? "Google Maps" : "Route sketch from the plan's coordinates"}</p>
          )}
        </div>
        {BROWSER_KEY
          ? <GoogleMap trip={trip} view={view} day={multi ? day : null} selected={selected} onSelect={setSelected} />
          : <SvgMap trip={trip} view={view} day={multi ? day : null} selected={selected} onSelect={setSelected} />}
        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-line px-4 py-3 text-[13px] text-ink-soft">
          {view === "journey" ? (
            <>
              <span className="inline-flex items-center gap-2"><span className="h-1 w-6 rounded bg-night" />Outbound</span>
              <span className="inline-flex items-center gap-2"><span className="w-6 border-t-2 border-dashed border-signal" />Return</span>
            </>
          ) : (
            <>
              <span className="inline-flex items-center gap-2"><span className="h-0.5 w-6 bg-night" />By road</span>
              <span className="inline-flex items-center gap-2"><span className="w-6 border-t-2 border-dotted border-teal" />On foot</span>
              <span className="inline-flex items-center gap-2"><span className="h-3 w-3 rounded-sm border-2 border-night bg-paper" />Station</span>
              <span className="inline-flex items-center gap-2"><span className="h-3 w-3 rounded-full bg-[#E8873A]" />Sunset</span>
              <span className="inline-flex items-center gap-2"><span className="h-3 w-3 rounded-full border-2 border-night bg-paper" />Meal</span>
            </>
          )}
        </div>
      </div>
      <ol className="panel max-h-[560px] divide-y divide-line overflow-y-auto" aria-label="Stops in order">
        {multi && (
          <li className="sticky top-0 z-10 bg-paper/95 px-4 py-2.5 backdrop-blur">
            <p className="text-[12px] font-bold uppercase tracking-[0.12em] text-signal-deep">Day {day + 1} of {days.length}</p>
            <p className="text-[14px] font-semibold">{niceDate(days[day].date, { weekday: "long", day: "numeric", month: "long" })} · {g.stops.length} stops</p>
          </li>
        )}
        {g.stops.map((s) => (
          <li key={s.id}>
            <button onClick={() => { setSelected(s.id); setView("city"); }} aria-current={s.id === selected}
                    className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors ${s.id === selected ? "bg-signal-soft/60" : "hover:bg-haze"}`}>
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-night text-sm font-bold text-white">{s.n}</span>
              <span className="min-w-0">
                <span className="block font-semibold leading-snug">{s.title}</span>
                <span className="num block text-[13px] text-ink-mute">
                  {s.time}
                  {s.item.travel_minutes_from_prev ? `, ${minutes(s.item.travel_minutes_from_prev)} ${s.item.travel_mode === "walk" ? "on foot" : `by ${(MODE_LABEL[s.item.travel_mode ?? ""] ?? "road").toLowerCase()}`}` : ""}
                  {s.item.distance_from_prev_m ? ` (${km(s.item.distance_from_prev_m)})` : ""}
                </span>
              </span>
            </button>
          </li>
        ))}
        {sel?.item.meta?.address && (
          <li className="px-4 py-3 text-[13px] text-ink-soft">
            <span className="font-semibold text-ink">{sel.title}</span><br />{sel.item.meta.address}
          </li>
        )}
      </ol>
    </div>
  );
}