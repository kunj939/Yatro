"use client";

import { useState } from "react";
import { BedDouble, Binoculars, Car, Castle, ChevronDown, Church, Clock, Coffee, Droplets, ExternalLink, Flame, Flower2, Footprints, Home, Hotel, Landmark, Map as MapIcon, MessageCircle, Mountain, Palette, PawPrint, Plane, Bus, Repeat, ShoppingBag, Sparkles, Sunset, TrainFront, TramFront, TreePalm, Trees, UtensilsCrossed, Waves, Wine } from "lucide-react";
import type { ItineraryItem, TransportLeg } from "@/lib/types";
import { endpoints } from "@/lib/api";
import { MODE_LABEL, km, minutes, money } from "@/lib/format";
import { directionsUrl } from "@/lib/tripLinks";
import { VerificationBadge } from "../VerificationBadge";
import { placeSource } from "@/lib/sources";
import { askAboutStop } from "./TripChat";

const KIND_ICON: Record<string, any> = { activity: Landmark, meal: UtensilsCrossed, sunset: Sunset, stay: Hotel, arrival: Home, free: Coffee };
// What kind of place a stop is, from its name first ("Baga Beach", "Amer Fort") and then its tags: a beach gets a palm,
// a fort a castle, a temple a diya flame. The first match wins, so the specific words come before the general ones.
const PLACE_ICON: [RegExp, string[], any][] = [
  [/\b(beach|bay|coast|shore)\b/i, ["beach"], TreePalm],
  [/\b(falls|waterfall)\b/i, ["waterfall"], Droplets],
  [/\b(lake|sagar|talab|dam|river|ghats?|backwaters?|lagoon)\b/i, ["lake"], Waves],
  [/\b(fort|forts|qila|castle|palace|mahal|haveli)\b/i, ["fort", "palace", "royal"], Castle],
  [/\b(church|cathedral|basilica|chapel)\b/i, [], Church],
  [/\b(temple|mandir|mosque|masjid|gurudwara|gurdwara|dargah|ashram|monastery|stupa)\b/i, ["temple", "spiritual"], Flame],
  [/\b(gallery|art)\b/i, ["gallery"], Palette],
  [/\b(market|bazaar|bazar|mall|emporium|haat)\b/i, ["market", "shopping"], ShoppingBag],
  [/\b(zoo|aquarium|safari|bird)\b/i, ["zoo"], PawPrint],
  [/\b(garden|gardens|bagh|botanical)\b/i, ["garden"], Flower2],
  [/\b(park|forest|sanctuary|wildlife|reserve|valley)\b/i, ["nature", "national_park"], Trees],
  [/\b(point|viewpoint|view|tower|lookout)\b/i, ["viewpoint", "observation"], Binoculars],
  [/\b(hill|hills|peak|mountain|trek)\b/i, ["mountain"], Mountain],
  [/\b(club|pub|bar|lounge)\b/i, ["nightlife"], Wine],
];
const MODE_ICON: Record<string, any> = { train: TrainFront, bus: Bus, flight: Plane, cab: Car, own_car: Car, walk: Footprints, transit: TramFront };
// Decorative tints so a glance down the day tells travel from sights from meals. None of them is a claim about
// the data — provenance is only ever shown by the VerificationBadge.
export const KIND_TONE: Record<string, string> = {
  transport: "bg-spark/15 text-spark", activity: "bg-signal-soft text-signal-deep", meal: "bg-haze-2 text-ink",
  sunset: "bg-signal text-on-signal", stay: "bg-haze-2 text-ink-soft", arrival: "bg-haze-2 text-ink-soft",
};
export const KIND_LABEL: Record<string, string> = { transport: "Travel", activity: "Visit", meal: "Meal", sunset: "Sunset", stay: "Stay", arrival: "Arrive" };

export function itemIcon(item: ItineraryItem) {
  if (item.kind === "transport") return MODE_ICON[item.meta?.mode] ?? TrainFront;
  if (item.kind === "stay") return item.meta?.stay === "hostel" || /\bhostel\b/i.test(item.title) ? BedDouble : Hotel;
  if (item.kind === "meal") return (item.meta?.tags ?? []).includes("cafe") || /\bcaf[eé]\b|coffee/i.test(item.title) ? Coffee : UtensilsCrossed;
  if (item.kind === "activity") {
    const tags: string[] = item.meta?.tags ?? [];
    const hit = PLACE_ICON.find(([re]) => re.test(item.title)) ?? PLACE_ICON.find(([, tg]) => tg.some((x) => tags.includes(x)));
    if (hit) return hit[2];
  }
  return KIND_ICON[item.kind] ?? Landmark;
}

function Rail({ first, last, dot, dotCls = "" }: { first?: boolean; last?: boolean; dot?: boolean; dotCls?: string }) {
  return (
    <div className="relative w-5 shrink-0 sm:w-6" aria-hidden>
      <span className={`absolute left-1/2 w-px -translate-x-1/2 bg-line ${first && dot ? "top-6" : "top-0"} ${last && dot ? "h-6" : "bottom-0"}`} />
      {dot && <span className={`absolute left-1/2 top-5 h-3 w-3 -translate-x-1/2 rounded-full border-2 border-paper ring-2 ${dotCls || "bg-signal ring-signal/30"}`} />}
    </div>
  );
}

function Leg({ item, currency }: { item: ItineraryItem; currency?: string }) {
  if (!item.travel_minutes_from_prev && !item.distance_from_prev_m) return null;
  const Icon = MODE_ICON[item.travel_mode ?? ""] ?? Car;
  return (
    <div className="flex gap-3 sm:gap-4">
      <div className="hidden w-[60px] shrink-0 sm:block md:w-[72px]" />
      <Rail />
      <div className="num flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-[13px] text-ink-mute">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-paper px-2.5 py-1 font-medium text-ink-soft">
          <Icon className="h-3.5 w-3.5" aria-hidden />{MODE_LABEL[item.travel_mode ?? ""] ?? "Travel"}
        </span>
        <span>{minutes(item.travel_minutes_from_prev)}</span>
        <span aria-hidden>·</span>
        <span>{km(item.distance_from_prev_m)}</span>
        {!!item.travel_cost && <><span aria-hidden>·</span><span>{money(item.travel_cost, currency)}</span></>}
        {item.leg_state && !["verified_provider", "demo"].includes(item.leg_state) && <VerificationBadge state={item.leg_state} />}
      </div>
    </div>
  );
}

/** "Jaipur Jn (JP)" -> { name: "Jaipur Jn", code: "JP" }; a name with no code stays whole. */
function stop(label: string | null | undefined): { name: string; code: string | null } {
  const m = /^(.*?)\s*\(([A-Z0-9]{2,5})\)$/.exec(label ?? "");
  return m ? { name: m[1], code: m[2] } : { name: label ?? "", code: null };
}

const dayOf = (iso: string) => iso.slice(0, 10);
const TICKET_MODES = new Set(["train", "bus", "flight"]);

function Segment({ dep, arr, from, to, mins, Icon, nextDay }: {
  dep: string; arr: string; from: string; to: string; mins: number; Icon: any; nextDay: number;
}) {
  const a = stop(from), b = stop(to);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-start gap-2 sm:gap-3">
      <div className="min-w-0">
        <p className="num text-[22px] font-bold leading-none sm:text-[24px]">{dep}</p>
        <p className="mt-1.5 truncate text-[13px] font-semibold text-ink" title={from}>{a.code ?? a.name}</p>
        {a.code && <p className="truncate text-[13px] text-ink-mute" title={a.name}>{a.name}</p>}
      </div>
      <div className="flex w-[84px] flex-col items-center pt-1 sm:w-[120px]" aria-hidden>
        <div className="flex w-full items-center gap-1.5 text-ink-mute">
          <span className="h-2 w-2 shrink-0 rounded-full border-2 border-ink-mute" />
          <span className="h-px flex-1 border-t border-dashed border-ink-mute/70" />
          <Icon className="h-4 w-4 shrink-0 text-spark" />
          <span className="h-px flex-1 border-t border-dashed border-ink-mute/70" />
          <span className="h-2 w-2 shrink-0 rounded-full bg-ink-mute" />
        </div>
        <p className="num mt-1.5 text-[12px] font-medium text-ink-mute">{minutes(mins)}</p>
      </div>
      <div className="min-w-0 text-right">
        <p className="num text-[22px] font-bold leading-none sm:text-[24px]">
          {arr}{nextDay > 0 && <sup className="ml-0.5 text-[12px] font-semibold text-caution">+{nextDay}</sup>}
        </p>
        <p className="mt-1.5 truncate text-[13px] font-semibold text-ink" title={to}>{b.code ?? b.name}</p>
        {b.code && <p className="truncate text-[13px] text-ink-mute" title={b.name}>{b.name}</p>}
      </div>
    </div>
  );
}

const daysBetween = (a: string, b: string) =>
  Math.round((new Date(dayOf(b)).getTime() - new Date(dayOf(a)).getTime()) / 86400000);

/** A journey drawn like a ticket: departure and arrival in large type, station codes under them, the ride between.
 * A journey with a change of train shows each train, and the change between them with the time there is to make it. */
function JourneyTicket({ item }: { item: ItineraryItem }) {
  const Icon = MODE_ICON[item.meta?.mode] ?? TrainFront;
  const legs: TransportLeg[] = Array.isArray(item.meta?.legs) ? item.meta.legs : [];
  const start = item.start_at;
  return (
    <div className="mt-3.5 rounded-xl border border-line bg-haze/50 p-3.5 sm:p-4" aria-label={`From ${item.meta?.from_name} to ${item.meta?.to_name}`}>
      {legs.length > 1 ? (
        <ol className="space-y-3">
          {legs.map((leg, i) => {
            const prev = legs[i - 1];
            const wait = prev ? Math.round((new Date(leg.depart_at).getTime() - new Date(prev.arrive_at).getTime()) / 60000) : 0;
            return (
              <li key={i}>
                {prev && (
                  <p className="num mb-3 flex items-center gap-2 text-[13px] font-semibold text-caution">
                    <span className="h-px flex-1 bg-caution/30" aria-hidden />
                    <Repeat className="h-3.5 w-3.5" aria-hidden />Change at {stop(leg.from_name).name} · {minutes(wait)}
                    <span className="h-px flex-1 bg-caution/30" aria-hidden />
                  </p>
                )}
                <p className="mb-2 truncate text-[13px] font-semibold text-ink-soft">
                  {leg.name}{leg.number ? ` ${leg.number}` : ""}{leg.service_class ? ` · ${leg.service_class}` : ""}
                </p>
                <Segment dep={leg.depart_at.slice(11, 16)} arr={leg.arrive_at.slice(11, 16)} from={leg.from_name} to={leg.to_name}
                         mins={leg.duration_minutes} Icon={Icon} nextDay={daysBetween(start, leg.arrive_at)} />
              </li>
            );
          })}
        </ol>
      ) : (
        <Segment dep={item.time ?? item.meta?.when ?? "—"} arr={item.end_time ?? "—"} from={item.meta?.from_name} to={item.meta?.to_name} mins={item.duration_minutes}
                 Icon={Icon} nextDay={daysBetween(item.start_at, item.end_at)} />
      )}
    </div>
  );
}

export function TimelineItem({ item, onChange, highlight, currency, near, first, last, tripId }: {
  item: ItineraryItem; onChange?: (item: ItineraryItem) => void; highlight?: boolean; currency?: string; near?: string; first?: boolean; last?: boolean;
  tripId?: string;
}) {
  const [why, setWhy] = useState(false);
  const Icon = itemIcon(item);
  if (item.kind === "free") {
    return (
      <li className="flex gap-3 sm:gap-4">
        <span className="num hidden w-[60px] shrink-0 pt-3 text-right text-sm text-ink-mute sm:block md:w-[72px]">{item.time}</span>
        <Rail first={first} last={last} dot dotCls="bg-haze-2 ring-line" />
        <div className="my-1.5 flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-dashed border-line px-4 py-2.5 text-sm text-ink-mute">
          <Coffee className="h-4 w-4 shrink-0" aria-hidden /><span className="num sm:hidden">{item.time}</span>{item.title}, {minutes(item.duration_minutes)}
        </div>
      </li>
    );
  }
  const hasAlts = item.alternatives.length > 0;
  // the explanation already says it: don't repeat the same sentence as the first tick
  const reasons = item.reasons.filter((r) => !(item.explanation ?? "").toLowerCase().includes(r.text.toLowerCase()));
  const major = ["activity", "meal", "sunset", "transport", "stay"].includes(item.kind) && !(item.kind === "stay" && !item.cost);
  const mappable = ["activity", "meal", "sunset", "stay"].includes(item.kind) && !item.meta?.google_expired; // an expired stop has its own link (I6)
  const mapsUrl = directionsUrl(item) ?? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(near ? `${item.title}, ${near}` : item.title)}`;
  const free = item.cost === 0 && (item.kind === "activity" || item.kind === "sunset");
  const unpublished = !!item.hours_note?.startsWith("Opening hours are not published") && !item.opening_time;
  const ticket = item.kind === "transport" && TICKET_MODES.has(item.meta?.mode) && !!item.meta?.from_name && !!item.meta?.to_name;
  const source = placeSource(item);
  const askable = !!tripId && ["activity", "sunset", "meal", "stay"].includes(item.kind);  // owner's trip: the chat can answer
  // On a ticket the route is drawn, so the subtitle keeps only what it adds ("including about 3 h at the airports").
  let subtitle = item.subtitle;
  if (ticket && subtitle) {
    const route = `${item.meta.from_name} to ${item.meta.to_name}`;
    const extra = subtitle.startsWith(route) ? subtitle.slice(route.length).replace(/^,\s*/, "") : subtitle;
    subtitle = item.meta?.legs?.length > 1 || !extra ? null : extra[0].toUpperCase() + extra.slice(1);
  }
  return (
    <li>
      <Leg item={item} currency={currency} />
      <div className="flex gap-3 sm:gap-4">
        <div className="hidden w-[60px] shrink-0 pt-4 text-right sm:block md:w-[72px]">
          <p className="num text-[18px] font-bold leading-none">{item.time ?? item.meta?.when}</p>
          {item.duration_minutes > 0 && item.end_time && <p className="num mt-1 text-xs text-ink-mute">to {item.end_time}</p>}
        </div>
        <Rail first={first && !item.travel_minutes_from_prev} last={last} dot
              dotCls={highlight ? "bg-signal ring-signal/50" : item.kind === "transport" ? "bg-spark ring-spark/30" : "bg-signal ring-signal/25"} />
        <article className={`group relative mb-1 min-w-0 flex-1 rounded-2xl border p-4 transition-all duration-300 md:p-5 ${
          highlight ? "border-signal shadow-glow ring-2 ring-signal/30" : "border-line hover:border-ink/20 hover:shadow-lift"} ${major ? "bg-paper" : "bg-haze/40"}`}>
          <div className="flex items-start gap-3">
            <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition-transform duration-300 group-hover:scale-105 sm:h-11 sm:w-11 ${KIND_TONE[item.kind] ?? "bg-haze text-ink"}`}>
              <Icon className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <p className="num mb-0.5 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-ink-mute">
                <span className="normal-case tracking-normal text-ink-soft sm:hidden">{item.time ? `${item.time}${item.duration_minutes > 0 ? `–${item.end_time}` : ""}` : item.meta?.when}</span>
                <span className="hidden sm:inline">{KIND_LABEL[item.kind] ?? item.kind}</span>
                {highlight && <span className="rounded-full bg-signal px-2 py-0.5 text-[12px] text-on-signal">New</span>}
              </p>
              <h3 className="break-words text-[16px] font-bold leading-snug sm:text-[18px]">{item.title}</h3>
              {subtitle && <p className="mt-0.5 text-[14px] text-ink-soft">{subtitle}</p>}
              {item.meta?.why_go && (
                <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-full bg-signal-soft px-2.5 py-0.5 text-[13px] font-semibold text-signal-deep">
                  <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden />{item.meta.why_go}
                </p>
              )}
              {item.meta?.google_expired && item.meta?.maps_url && (
                <a href={item.meta.maps_url} target="_blank" rel="noopener noreferrer"
                   className="mt-1 inline-flex items-center gap-1 text-[13px] font-semibold text-signal-deep underline underline-offset-4">
                  Open in Google Maps<ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
              {free && <span className="mt-1.5 inline-block rounded-full bg-teal-soft px-2.5 py-0.5 text-[13px] font-semibold text-teal-deep sm:hidden">Free entry</span>}
              {!!item.cost && (
                <p className="num mt-1.5 flex items-center gap-2 sm:hidden">
                  <span className="text-[16px] font-bold">{money(item.cost, currency)}</span><VerificationBadge state={item.cost_state} label={item.meta?.fare_label} />
                </p>
              )}
            </div>
            {!!item.cost && (
              <div className="hidden shrink-0 flex-col items-end gap-1 sm:flex">
                <span className="num text-[18px] font-bold">{money(item.cost, currency)}</span>
                <VerificationBadge state={item.cost_state} label={item.meta?.fare_label} />
              </div>
            )}
            {free && <span className="hidden shrink-0 rounded-full bg-teal-soft px-2.5 py-0.5 text-[13px] font-semibold text-teal-deep sm:inline">Free entry</span>}
          </div>

          {ticket && <JourneyTicket item={item} />}
          {ticket && item.meta?.provenance_label && (
            <p className="mt-2 text-[13px] text-ink-mute">{item.meta.provenance_label}</p>
          )}
          {ticket && item.meta?.brownout && (
            <p className="mt-1 text-[13px] text-ink-mute">{item.meta.brownout}</p>
          )}
          {ticket && item.meta?.fare_note && (
            <p className="mt-1 text-[13px] text-ink-mute">{item.meta.fare_note}</p>
          )}
          {ticket && item.meta?.provenance_warning && (
            <p className="mt-1 text-[13px] font-medium text-caution">{item.meta.provenance_warning}</p>
          )}
          {ticket && tripId && item.time && item.meta?.rail_source && <ReportTrain tripId={tripId} itemId={item.id} />}
          {ticket && !item.time && (
            <p className="mt-2.5 rounded-lg bg-haze-2/70 px-3 py-2 text-[13px] text-ink-soft">
              No timetable was available for this train, so no times are shown. Check the exact train before you go.
            </p>
          )}
          {major && (
            <div className="num mt-3 flex flex-wrap items-center gap-2 text-[13px] text-ink-soft">
              {item.duration_minutes > 0 && !ticket && <Pill><Clock className="h-3.5 w-3.5" aria-hidden />{minutes(item.duration_minutes)}</Pill>}
              {item.opening_time && <Pill>Open {item.opening_time}–{item.closing_time}<VerificationBadge state={item.hours_state} compact /></Pill>}
              {unpublished && <Pill><span title={item.hours_note ?? ""}>Hours unconfirmed</span></Pill>}
              {item.kind === "transport" && item.meta?.distance_m && <Pill>{km(item.meta.distance_m)}</Pill>}
              {item.kind === "transport" && item.hours_state && <Pill>Schedule <VerificationBadge state={item.hours_state} compact /></Pill>}
              {source && <Pill><span title="Where these details come from">{source.label}{source.detail ? ` · ${source.detail}` : ""}</span></Pill>}
            </div>
          )}
          {/* Hours that simply aren't published are a small chip, not an alarm on every sight; a real conflict
              (sources disagree, closes soon after you arrive) keeps its note. */}
          {item.hours_note && !unpublished && <p className="mt-3 rounded-lg bg-caution-soft px-3 py-2 text-[14px] text-caution">{item.hours_note}</p>}

          {(item.explanation || hasAlts || mappable || askable) && (
            <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3">
              {askable && (
                <button type="button" onClick={() => askAboutStop(item)}
                        className="inline-flex items-center gap-1.5 rounded-full bg-signal-soft px-3 py-1.5 text-[13px] font-semibold text-signal-deep transition-all hover:-translate-y-px hover:bg-signal hover:text-on-signal">
                  <MessageCircle className="h-3.5 w-3.5" aria-hidden />Ask{item.kind === "activity" || item.kind === "sunset" ? ": why famous?" : ""}
                </button>
              )}
              {item.explanation && (
                <ActionBtn onClick={() => setWhy(!why)} aria-expanded={why}>
                  Why this?<ChevronDown className={`h-3.5 w-3.5 transition-transform ${why ? "rotate-180" : ""}`} aria-hidden />
                </ActionBtn>
              )}
              {hasAlts && onChange && (
                <ActionBtn onClick={() => onChange(item)}>
                  <Repeat className="h-3.5 w-3.5" aria-hidden />Swap<span className="num rounded-full bg-haze-2 px-1.5 text-[12px] text-ink-soft">{item.alternatives.length}</span>
                </ActionBtn>
              )}
              {mappable && (
                <a href={mapsUrl} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[13px] font-semibold text-ink-soft transition-all hover:-translate-y-px hover:border-ink/30 hover:text-ink">
                  <MapIcon className="h-3.5 w-3.5" aria-hidden />Directions<ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
            </div>
          )}
          {why && (
            <div className="fade-up mt-3 rounded-xl bg-haze p-4 text-[14px]">
              <p className="leading-relaxed">{item.explanation}</p>
              {reasons.length > 0 && (
                <ul className="mt-3 space-y-1 text-ink-soft">
                  {reasons.map((r) => <li key={r.key} className="flex gap-2"><span className="text-teal">✓</span>{r.text}</li>)}
                </ul>
              )}
              {item.sources.length > 0 && (
                <div className="mt-3 border-t border-line pt-3">
                  <p className="text-xs font-semibold text-ink-mute">Sources checked</p>
                  <ul className="mt-1.5 space-y-1.5">
                    {item.sources.map((s, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-2 text-[13px]">
                        {s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 font-medium underline underline-offset-2">{s.domain || s.title}<ExternalLink className="h-3 w-3" aria-hidden /></a>
                          : <span className="font-medium">{s.label || s.title || s.domain}</span>}
                        {s.tier && <span className="text-ink-mute">{s.tier}</span>}
                        {s.value && <span className="num text-ink-mute">{s.value.open}–{s.value.close}</span>}
                        {s.status && s.status !== "unverified" && <span className={s.status === "confirmed" ? "text-teal-deep" : "text-caution"}>{s.status}</span>}
                        {s.verified_at && <span className="text-ink-mute">checked {new Date(s.verified_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </article>
      </div>
    </li>
  );
}

/** "This train didn't run" / "it ran at another time": the route's saved timetable is re-checked first (ADR 0003). */
function ReportTrain({ tripId, itemId }: { tripId: string; itemId: string }) {
  const [state, setState] = useState<"idle" | "open" | "sending" | "done" | "error">("idle");
  const [msg, setMsg] = useState("");
  async function send(problem: "didnt_run" | "wrong_time") {
    setState("sending");
    try { setMsg((await endpoints.reportTrain(tripId, itemId, problem)).message); setState("done"); }
    catch { setState("error"); }
  }
  if (state === "done") return <p className="mt-2 text-[13px] text-teal-deep">{msg}</p>;
  if (state === "idle") {
    return (
      <button type="button" onClick={() => setState("open")} className="mt-2 text-[13px] font-medium text-ink-mute underline-offset-4 hover:text-ink hover:underline">
        Report a problem with this train
      </button>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
      <span className="text-ink-soft">What happened?</span>
      <button type="button" disabled={state === "sending"} onClick={() => send("didnt_run")} className="rounded-full border border-line px-3 py-1 font-semibold hover:border-ink/30">It didn&apos;t run</button>
      <button type="button" disabled={state === "sending"} onClick={() => send("wrong_time")} className="rounded-full border border-line px-3 py-1 font-semibold hover:border-ink/30">Different time</button>
      {state === "error" && <span className="text-danger">Couldn&apos;t send. Try again.</span>}
    </div>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return <span className="inline-flex items-center gap-1.5 rounded-full bg-haze px-2.5 py-1">{children}</span>;
}

function ActionBtn({ children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" {...rest}
            className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[13px] font-semibold transition-all hover:-translate-y-px hover:border-ink/30">
      {children}
    </button>
  );
}
