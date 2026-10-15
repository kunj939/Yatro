"use client";

import {
  Armchair, Baby, Bed, BedDouble, Building, Building2, Bus, Car, CarTaxiFront, Castle, Check, Church, Coffee, Compass, Drumstick, Gem,
  Heart, HeartHandshake, Landmark, Leaf, Minus, Mountain, Palette, Plane, Plus, Scale, ShoppingBag, Shuffle, Soup,
  Smile, Sparkles, Sunset, Tent, TrainFront, TramFront, Trees, User, UsersRound, UtensilsCrossed, Wallet, Wine,
} from "lucide-react";
import { budgetScale, convertApprox, currencySymbol, money } from "@/lib/format";
import { INTEREST_CHIPS, MAX_INTERESTS } from "@/lib/interests";
import { brand } from "@/lib/brand";

/** A selectable tile. Selection is shown by the brand edge, a filled icon and a tick, never by colour alone. */
function Option({ selected, onClick, title, text, icon: Icon, multi = false, aside, compact = false }: {
  selected: boolean; onClick: () => void; title: string; text?: string; icon?: any; multi?: boolean; aside?: React.ReactNode; compact?: boolean;
}) {
  return (
    <button type="button" onClick={onClick} role={multi ? "checkbox" : "radio"} aria-checked={selected}
            className={`group relative flex w-full items-start gap-3 rounded-2xl border p-4 text-left transition-all duration-200 ${
              compact ? "sm:flex-col sm:items-start" : ""} ${
              selected ? "border-signal bg-signal-soft/50 shadow-[0_0_0_1px_rgb(var(--signal))]"
                       : "border-line bg-paper hover:-translate-y-0.5 hover:border-ink/25 hover:shadow-lift"}`}>
      {Icon && (
        <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition-colors duration-200 ${
          selected ? "bg-signal text-on-signal" : "bg-haze text-ink-soft group-hover:text-ink"}`}>
          <Icon className="h-5 w-5" aria-hidden />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{title}</span>
        {text && <span className="mt-0.5 block text-sm text-ink-mute">{text}</span>}
        {aside && <span className="mt-1.5 block">{aside}</span>}
      </span>
      <span className={`grid h-5 w-5 shrink-0 place-items-center border-2 transition-all duration-200 ${multi ? "rounded-md" : "rounded-full"} ${
        compact ? "absolute right-3 top-3" : "mt-1"} ${selected ? "scale-100 border-signal bg-signal text-on-signal" : "border-line"}`}>
        {selected && <Check className="h-3 w-3" strokeWidth={3} aria-hidden />}
      </span>
    </button>
  );
}

export function BudgetInput({ value, onChange, travellers, days, currency }: {
  value: number; onChange: (v: number) => void; travellers: number; days: number; currency: string;
}) {
  const { presets, min } = budgetScale(currency);
  const max = presets[presets.length - 1] * 2;
  const symbol = currencySymbol(currency);
  const fill = Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
  const perPerson = travellers > 0 ? value / travellers : value;
  return (
    <div>
      <label htmlFor="budget" className="text-sm font-semibold">Total budget for everyone</label>
      <div className="relative mt-1.5">
        <span className="pointer-events-none absolute left-5 top-1/2 -translate-y-1/2 text-2xl font-bold text-ink-mute">{symbol}</span>
        <input id="budget" type="number" inputMode="numeric" min={min} max={9999999999} step={min}
               className="field num py-4 text-3xl font-bold" style={{ paddingLeft: `${1.6 + symbol.length * 0.95}rem` }}
               value={Number.isFinite(value) && value > 0 ? value : ""}
               onChange={(e) => onChange(Math.max(0, Math.round(Number(e.target.value))))} />
        <span className="pointer-events-none absolute right-5 top-1/2 -translate-y-1/2 rounded-full bg-haze px-2.5 py-1 text-xs font-bold text-ink-soft">{currency}</span>
      </div>
      <input type="range" aria-label="Adjust budget" min={min} max={max} step={presets[0] / 20} value={Math.min(max, value || min)}
             onChange={(e) => onChange(Number(e.target.value))} className="range mt-5 w-full" style={{ ["--fill" as string]: `${fill}%` }} />
      <div className="mt-4 flex flex-wrap gap-2">
        {presets.map((p) => <button key={p} type="button" onClick={() => onChange(p)} className={value === p ? "chip-on" : "chip-off"}>{money(p, currency)}</button>)}
      </div>
      {value > 0 && (
        <div className="num mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Figure label="Per person" value={money(Math.round(perPerson), currency)} />
          <Figure label={days > 1 ? "Per person, per day" : "Trip length"} value={days > 1 ? money(Math.round(perPerson / days), currency) : "Day trip"} />
          <Figure label="Safety buffer kept" value="About 7%" className="col-span-2 sm:col-span-1" />
        </div>
      )}
      <p className="mt-3 text-sm text-ink-mute">Covers travel, stay, food, entry fees and local transport.</p>
      {value > 0 && value < min && <p className="mt-2 text-sm text-danger">The minimum budget is {money(min, currency)}.</p>}
      {currency !== "INR" && value > 0 && (
        <p className="num mt-2 text-sm text-ink-mute">About {money(convertApprox(value, currency, "INR"), "INR")} in rupees. The trip is planned in {currency}.</p>
      )}
    </div>
  );
}

function Figure({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className={`rounded-xl border border-line bg-haze/60 px-4 py-3 ${className}`}>
      <p className="text-xs text-ink-mute">{label}</p>
      <p className="mt-0.5 font-bold">{value}</p>
    </div>
  );
}

const TYPES = [
  { key: "solo", title: "Solo", icon: User, text: "Just me" },
  { key: "couple", title: "Couple", icon: Heart, text: "Two of us" },
  { key: "partner", title: "Partner", icon: HeartHandshake, text: "Girlfriend / boyfriend" },
  { key: "friends", title: "Friends", icon: UsersRound, text: "A group of mates" },
  { key: "family", title: "Family", icon: Baby, text: "Kids or parents along" },
];

export function TravellerSelector({ type, count, onChange }: { type: string; count: number; onChange: (type: string, count: number) => void }) {
  return (
    <div className="space-y-4">
      <div role="radiogroup" aria-label="Who is travelling" className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {TYPES.map((t) => (
          <Option key={t.key} compact selected={type === t.key} title={t.title} text={t.text} icon={t.icon}
                  onClick={() => onChange(t.key, t.key === "solo" ? 1 : ["couple", "partner"].includes(t.key) ? 2 : Math.max(count, 3))} />
        ))}
      </div>
      <div className="flex items-center justify-between rounded-2xl border border-line bg-paper p-4">
        <span><span className="block font-semibold">Travellers</span><span className="text-sm text-ink-mute">Including you</span></span>
        <div className="flex items-center gap-3">
          <Stepper label="Fewer travellers" disabled={count <= 1 || type === "solo"} onClick={() => onChange(type, count - 1)}><Minus className="h-4 w-4" /></Stepper>
          <span className="num w-8 text-center text-2xl font-bold" aria-live="polite">{count}</span>
          <Stepper label="More travellers" disabled={count >= 20 || type === "solo"} onClick={() => onChange(type, count + 1)}><Plus className="h-4 w-4" /></Stepper>
        </div>
      </div>
    </div>
  );
}

const AGES = [
  { key: "kids", title: "Kids", band: "0–12", text: "Zoos, parks, easy days", icon: Baby },
  { key: "teens", title: "Teens", band: "13–17", text: "Activities and street food", icon: Smile },
  { key: "adults", title: "Adults", band: "18–59", text: "Everything on offer", icon: User },
  { key: "seniors", title: "Seniors", band: "60+", text: "Gentle pace, less walking", icon: Armchair },
] as const;

/** Ages in the party. Backend services/age_fit.py turns this into what is shown: adults-only places are left out when anyone is
 *  under 18, treks drop for older travellers, and children or seniors get a relaxed pace with shorter walks. */
export function AgeSelector({ value, onChange, solo }: { value: string[]; onChange: (v: string[]) => void; solo: boolean }) {
  function toggle(k: string) {
    if (solo) return onChange([k]);  // one person is one age
    const next = value.includes(k) ? value.filter((v) => v !== k) : [...value, k];
    onChange(next.length ? next : [k]);  // at least one group stays picked
  }
  const minors = value.includes("kids") || value.includes("teens");
  const older = value.includes("seniors");
  return (
    <div>
      <p className="mb-3 text-sm text-ink-mute">
        {solo ? "Pick your age group." : "Tick every age group in your party."} We use it to show places that suit everyone.
      </p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="group" aria-label="Ages in your group">
        {AGES.map((a) => (
          <Option key={a.key} compact multi={!solo} selected={value.includes(a.key)} title={a.title} icon={a.icon}
                  text={a.text} aside={<span className="num text-[13px] font-bold text-ink-soft">{a.band} years</span>}
                  onClick={() => toggle(a.key)} />
        ))}
      </div>
      {(minors || older) && (
        <p className="fade-up mt-3 flex items-start gap-2 rounded-xl border border-line bg-haze/60 px-4 py-3 text-[14px] text-ink-soft" aria-live="polite">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-signal-deep" aria-hidden />
          <span>
            {minors && "Nightlife and adults-only places are left out. "}
            {value.includes("kids") && "Zoos, parks and family spots go first. "}
            {older && "Treks and steep climbs are avoided, with fewer stops a day and shorter walks. "}
          </span>
        </p>
      )}
    </div>
  );
}

function Stepper({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-label={label} disabled={disabled} onClick={onClick}
            className="grid h-10 w-10 place-items-center rounded-full border border-line transition-all hover:border-signal hover:bg-signal-soft active:scale-95 disabled:pointer-events-none disabled:opacity-35">
      {children}
    </button>
  );
}

const TRANSPORT = [
  { key: "train", title: "Train", text: "Scheduled trains with class options", icon: TrainFront },
  { key: "bus", title: "Bus", text: "Intercity coaches", icon: Bus },
  { key: "metro", title: "Metro", text: "Metro and city buses", icon: TramFront },
  { key: "cab", title: "Cab", text: "Taxis and ride-hailing", icon: CarTaxiFront },
  { key: "own_car", title: "Own car", text: "Fuel, tolls and parking", icon: Car },
  { key: "flight", title: "Flight", text: "For long distances", icon: Plane },
  { key: "mixed", title: "Mixed", text: `Let ${brand.name} choose`, icon: Shuffle },
];

export function TransportSelector({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  function toggle(k: string) {
    if (k === "mixed") return onChange(["mixed"]);
    const without = value.filter((v) => v !== "mixed");
    const next = without.includes(k) ? without.filter((v) => v !== k) : [...without, k];
    onChange(next.length ? next : ["mixed"]);
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" role="group" aria-label="Transport">
      {TRANSPORT.map((t) => <Option key={t.key} multi selected={value.includes(t.key)} title={t.title} text={t.text} icon={t.icon} onClick={() => toggle(t.key)} />)}
    </div>
  );
}

export function FoodSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const opts = [
    { key: "veg", title: "Vegetarian", text: "Every meal stop serves vegetarian food", icon: Leaf },
    { key: "non_veg", title: "Non-vegetarian", text: "Places known for meat and fish", icon: Drumstick },
    { key: "both", title: "Anything good", text: "No restrictions", icon: UtensilsCrossed },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Food preference">
      {opts.map((o) => <Option key={o.key} compact selected={value === o.key} title={o.title} text={o.text} icon={o.icon} onClick={() => onChange(o.key)} />)}
    </div>
  );
}

const STYLES = [
  { key: "balanced", title: "Balanced", text: "A bit of everything", icon: Scale },
  { key: "budget", title: "Budget", text: "Stretch every cent", icon: Wallet },
  { key: "premium", title: "Premium", text: "Comfort first", icon: Gem },
  { key: "romantic", title: "Romantic", text: "Sunsets, views, quiet corners", icon: Heart },
  { key: "adventure", title: "Adventure", text: "Hikes and activities", icon: Mountain },
  { key: "sightseeing", title: "Sightseeing", text: "The landmarks", icon: Landmark },
  { key: "food", title: "Food", text: "Local favourites", icon: Soup },
  { key: "relaxed", title: "Relaxed", text: "Fewer stops, more time", icon: Coffee },
];

export function TripStyleSelector({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  function toggle(k: string) {
    let next = value.includes(k) ? value.filter((v) => v !== k) : [...value, k];
    if (k === "budget" && next.includes("budget")) next = next.filter((v) => v !== "premium");
    if (k === "premium" && next.includes("premium")) next = next.filter((v) => v !== "budget");
    if (next.length > 3) next = next.slice(next.length - 3);
    onChange(next.length ? next : ["balanced"]);
  }
  return (
    <div>
      <p className="mb-3 text-sm text-ink-mute">Pick up to three. <span className="num font-semibold text-ink">{value.length}/3</span> chosen.</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="group" aria-label="Trip style">
        {STYLES.map((s) => <Option key={s.key} compact multi selected={value.includes(s.key)} title={s.title} text={s.text} icon={s.icon} onClick={() => toggle(s.key)} />)}
      </div>
    </div>
  );
}

const INTEREST_ICON: Record<string, any> = {
  forts_palaces: Castle, temples_spiritual: Church, nature_lakes: Trees, street_food: Soup, museums_art: Palette,
  markets_shopping: ShoppingBag, adventure: Mountain, nightlife: Wine, heritage_history: Landmark, viewpoints_sunsets: Sunset,
};

export function InterestSelector({ value, onChange, noNightlife = false }: { value: string[]; onChange: (v: string[]) => void; noNightlife?: boolean }) {
  function toggle(id: string) {
    if (value.includes(id)) { onChange(value.filter((v) => v !== id)); return; }
    if (value.length >= MAX_INTERESTS) return;
    onChange([...value, id]);
  }
  return (
    <div>
      <p className="mb-3 text-sm text-ink-mute">
        Optional — pick up to {MAX_INTERESTS}. {brand.name} weighs these places higher when choosing what to visit.
        {noNightlife && " Nightlife is off because someone under 18 is travelling."}
      </p>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Favourite things to see (optional)">
        {INTEREST_CHIPS.map((c) => {
          const selected = value.includes(c.id);
          const disabled = (!selected && value.length >= MAX_INTERESTS) || (noNightlife && c.id === "nightlife");
          const Icon = INTEREST_ICON[c.id] ?? Compass;
          return (
            <button key={c.id} type="button" onClick={() => toggle(c.id)} aria-pressed={selected} disabled={disabled}
                    className={`${selected ? "chip-on" : "chip-off"} !rounded-full ${disabled ? "opacity-40" : ""}`}>
              <Icon className="h-4 w-4" aria-hidden />{c.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Typical nightly rates are calibrated in rupees, so they're only quoted for trips priced in rupees; elsewhere the
// tier and a price level say enough, and the plan itself uses real rates for the destination.
const STAYS = [
  { key: "no_stay", title: "No stay", text: "Day trip, or staying with friends", icon: Tent, level: 0 },
  { key: "premium", title: "Premium", text: "5-star hotel", inr: "around ₹11,000 a night", icon: Sparkles, level: 4 },
  { key: "tier1", title: "Upscale", text: "4-star hotel", inr: "around ₹6,000 a night", icon: Building2, level: 3 },
  { key: "tier2", title: "Comfort", text: "3-star hotel", inr: "around ₹2,800 a night", icon: Building, level: 2 },
  { key: "budget", title: "Budget hotel", text: "Clean and simple", inr: "around ₹1,600 a night", icon: BedDouble, level: 1 },
  { key: "oyo", title: "Economy room", text: "Budget chain rooms", inr: "around ₹1,200 a night", icon: Bed, level: 1 },
  { key: "hostel", title: "Hostel", text: "A bed in a shared room", inr: "around ₹650 a bed", icon: UsersRound, level: 1 },
];

export function StaySelector({ value, onChange, overnight, currency }: { value: string; onChange: (v: string) => void; overnight: boolean; currency: string }) {
  const sym = currencySymbol(currency);
  const symbol = sym.length === 1 ? sym : "●"; // "CHF" four times over would read as noise
  return (
    <div>
      {!overnight && <p className="mb-3 rounded-xl border border-signal/30 bg-signal-soft px-4 py-3 text-[16px]">This looks like a day trip, so a hotel isn&apos;t needed. Pick a stay only if you plan to add a night.</p>}
      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Stay">
        {STAYS.map((s) => (
          <Option key={s.key} selected={value === s.key} title={s.title} icon={s.icon}
                  text={currency === "INR" && s.inr ? `${s.text}, ${s.inr}` : s.text}
                  aside={s.level > 0 && (
                    <span className="num text-[13px] font-bold tracking-widest" aria-label={`Price level ${s.level} of 4`}>
                      <span className="text-ink">{symbol.repeat(s.level)}</span><span className="text-ink-mute/50">{symbol.repeat(4 - s.level)}</span>
                    </span>
                  )}
                  onClick={() => onChange(s.key)} />
        ))}
      </div>
      <p className="mt-3 text-sm text-ink-mute">Your plan uses the actual hotel rate where available.</p>
    </div>
  );
}
