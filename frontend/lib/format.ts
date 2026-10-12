export function inr(value: number | null | undefined, opts: { paise?: boolean } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const neg = value < 0;
  const abs = Math.abs(value);
  const hasPaise = opts.paise ?? Math.round(abs * 100) % 100 !== 0;
  const s = abs.toLocaleString("en-IN", { minimumFractionDigits: hasPaise ? 2 : 0, maximumFractionDigits: hasPaise ? 2 : 0 });
  return `${neg ? "−" : ""}₹${s}`;
}

// A locale for each currency's own grouping and symbol placement (₹12,34,567 vs €1.234.567 vs $1,234,567).
const CURRENCY_LOCALE: Record<string, string> = {
  INR: "en-IN", GBP: "en-GB", USD: "en-US", JPY: "ja-JP", CNY: "zh-CN", AUD: "en-AU", NZD: "en-NZ",
  CAD: "en-CA", SGD: "en-SG", HKD: "zh-HK", CHF: "de-CH", AED: "ar-AE", SAR: "ar-SA",
  EUR: "de-DE", KRW: "ko-KR", THB: "th-TH", MYR: "ms-MY", IDR: "id-ID", VND: "vi-VN", ZAR: "en-ZA", BRL: "pt-BR",
};

/** Same idea as inr(), but in the trip's own currency (the header, budget and timeline all use this once a
 * trip has been planned) rather than always assuming rupees. Falls back to inr() for INR or an unknown code. */
export function money(value: number | null | undefined, currency: string | null | undefined): string {
  if (!currency || currency === "INR") return inr(value);
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  try {
    // narrowSymbol: "₺1,500" as the plan's own text writes it, never "TRY 1,500" for a currency the locale lacks
    return new Intl.NumberFormat(CURRENCY_LOCALE[currency] ?? "en-US",
      { style: "currency", currency, currencyDisplay: "narrowSymbol",
        maximumFractionDigits: Math.round(value * 100) % 100 !== 0 ? 2 : 0 }).format(value);
  } catch {
    return inr(value); // a currency code Intl doesn't recognise: better a rupee figure than a crash
  }
}

/** The symbol a currency is written with (₹, £, €, ¥, US$ → $), for prefixing an input. */
export function currencySymbol(currency: string | null | undefined): string {
  const c = currency || "INR";
  try {
    const part = new Intl.NumberFormat("en", { style: "currency", currency: c, currencyDisplay: "narrowSymbol" })
      .formatToParts(0).find((p) => p.type === "currency");
    return part?.value ?? c;
  } catch { return c; }
}

// Currencies where a sensible trip budget runs into thousands or more of the unit.
const BIG_UNIT: Record<string, { presets: number[]; min: number }> = {
  INR: { presets: [2000, 5000, 10000, 25000], min: 100 },
  JPY: { presets: [20000, 50000, 100000, 250000], min: 1000 },
  KRW: { presets: [200000, 500000, 1000000, 2500000], min: 10000 },
  IDR: { presets: [2000000, 5000000, 10000000, 25000000], min: 100000 },
  VND: { presets: [3000000, 7500000, 15000000, 35000000], min: 200000 },
  THB: { presets: [3000, 7500, 15000, 35000], min: 100 },
  ZAR: { presets: [2000, 5000, 10000, 25000], min: 100 },
};
// Rough units of each currency per rupee (the backend's APPROX_INR_TO), only to size the quick-pick budgets.
const PER_INR: Record<string, number> = {
  USD: 0.012, GBP: 0.0095, EUR: 0.011, CNY: 0.086, AUD: 0.018, NZD: 0.020, CAD: 0.016, CHF: 0.011, SGD: 0.016,
  HKD: 0.094, AED: 0.044, SAR: 0.045, QAR: 0.044, MYR: 0.056, PHP: 0.68, EGP: 0.59, TRY: 0.41, BRL: 0.066,
  MXN: 0.24, PKR: 3.3, BDT: 1.3, LKR: 3.6, NPR: 1.6, NOK: 0.13, SEK: 0.13, DKK: 0.082, PLN: 0.048, CZK: 0.27, HUF: 4.3,
  JPY: 1.8, KRW: 16.2, IDR: 190, VND: 290, THB: 0.43, ZAR: 0.22,
};
const EURO_SCALE = { presets: [150, 400, 800, 1500], min: 10 };

/** 5,590 -> 5,500; 14,900 -> 15,000: two figures, the second a 0 or 5. */
function roundNice(v: number): number {
  const p = 10 ** Math.max(0, Math.floor(Math.log10(v)) - 1);
  return Math.max(p, Math.round(v / p / 5) * 5 * p);
}

/** An amount in one currency, roughly in another (₹40,000 typed for a Dubai trip -> about AED 1,800), rounded to
 * two figures. Only for pre-filling a budget the traveller then sees and can change; never for prices. */
export function convertApprox(amount: number, from: string, to: string): number {
  if (from === to || !amount) return amount;
  const rate = (c: string) => (c === "INR" ? 1 : PER_INR[c]);
  if (!rate(from) || !rate(to)) return amount;
  const v = (amount / rate(from)) * rate(to);
  const p = 10 ** Math.max(0, Math.floor(Math.log10(v)) - 1);
  return Math.max(1, Math.round(v / p) * p);
}

/** Quick-pick budgets and the smallest sensible budget, in the trip's own currency. A currency worth far less than a
 * euro gets the euro picks converted (₺5,500 to ₺55,000), not ₺150 to ₺1,500, which wouldn't buy a day anywhere. */
export function budgetScale(currency: string | null | undefined) {
  const c = currency || "INR";
  if (BIG_UNIT[c]) return BIG_UNIT[c];
  const ratio = PER_INR[c] ? PER_INR[c] / PER_INR.EUR : 1;
  if (ratio < 3) return EURO_SCALE;
  return { presets: EURO_SCALE.presets.map((v) => roundNice(v * ratio)), min: roundNice(EURO_SCALE.min * ratio) };
}

/** Euro amounts written the English way (€1,234.50), used by the landing page's sample trips. */
export function eur(value: number): string {
  const frac = Math.round(Math.abs(value) * 100) % 100 !== 0;
  const s = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR",
    minimumFractionDigits: frac ? 2 : 0, maximumFractionDigits: frac ? 2 : 0 }).format(Math.abs(value));
  return value < 0 ? `−${s}` : s;
}

export function signedInr(value: number | null | undefined): string {
  if (!value) return "Same cost";
  return value > 0 ? `+${inr(value)}` : `${inr(value)}`;
}

/** Same idea as signedInr(), but in the trip's own currency. */
export function signedMoney(value: number | null | undefined, currency: string | null | undefined): string {
  if (!value) return "Same cost";
  return value > 0 ? `+${money(value, currency)}` : `${money(value, currency)}`;
}

export function km(m: number | null | undefined): string {
  if (m === null || m === undefined) return "—";
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  return `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`;
}

export function minutes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 60) return `${n} min`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function niceDate(iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }) {
  const [y, mo, d] = iso.split("-").map(Number);
  return new Date(y, mo - 1, d).toLocaleDateString("en-IN", opts);
}

export function time12(hhmm: string | null | undefined): string {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const hh = h % 12 === 0 ? 12 : h % 12;
  return `${hh}:${String(m).padStart(2, "0")} ${suffix}`;
}

export const MODE_LABEL: Record<string, string> = {
  train: "Train", bus: "Bus", metro: "Metro", cab: "Cab", own_car: "Own car", flight: "Flight", mixed: "Mixed",
  walk: "Walk", transit: "Metro / bus",
};
export const TRAVELLER_LABEL: Record<string, string> = {
  solo: "Solo", couple: "Couple", partner: "Girlfriend / Boyfriend", friends: "Friends", family: "Family",
};
/** Age bands of the party (backend services/age_fit.py): what is asked in the wizard and shown on the trip. */
export const AGE_LABEL: Record<string, string> = { kids: "Kids", teens: "Teens", adults: "Adults", seniors: "Seniors" };
export const AGE_BAND: Record<string, string> = { kids: "0–12", teens: "13–17", adults: "18–59", seniors: "60+" };
export const AGE_ORDER = ["kids", "teens", "adults", "seniors"] as const;
export function ageSummary(groups: readonly string[] | undefined | null, short = false): string {
  const g = AGE_ORDER.filter((a) => groups?.includes(a));
  return g.map((a) => short ? AGE_LABEL[a] : `${AGE_LABEL[a]} ${AGE_BAND[a]}`).join(" · ");
}
export const FOOD_LABEL: Record<string, string> = { veg: "Vegetarian", non_veg: "Non-vegetarian", both: "Veg and non-veg" };
export const STAY_LABEL: Record<string, string> = {
  premium: "Premium hotel", tier1: "Tier 1 hotel", tier2: "Tier 2 hotel", budget: "Budget hotel", oyo: "OYO",
  hostel: "Hostel", no_stay: "No stay",
};
export const STYLE_LABEL: Record<string, string> = {
  premium: "Premium", balanced: "Balanced", budget: "Budget", romantic: "Romantic", adventure: "Adventure",
  sightseeing: "Sightseeing", food: "Food", relaxed: "Relaxed",
};

export function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/** "today at 3:05 pm", "tomorrow at 12:00 am" or "Thu 9 Oct at 12:00 am": when something reopens, in the person's own time zone. */
export function reopenAt(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  const clock = at.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true });
  const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((dayStart(at) - dayStart(now)) / 86_400_000);
  const when = days <= 0 ? "today" : days === 1 ? "tomorrow" : at.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  if (at.getHours() === 0 && at.getMinutes() === 0) {  // "12:00 am tomorrow" reads like tomorrow night: say midnight
    return days <= 1 ? "tonight at midnight" : `${when} at midnight`;
  }
  return `${when} at ${clock}`;
}

/** A wait in plain words: "40 seconds", "2 minutes". Rounded, because it is an estimate. */
export function waitWords(seconds: number): string {
  if (seconds < 90) return `${Math.max(5, Math.round(seconds / 5) * 5)} seconds`;
  const m = Math.max(2, Math.round(seconds / 60));
  return `${m} minutes`;
}

/** The estimate under "High demand — you're #N": "about 40 seconds (usually 30 to 60 seconds)". When no plan has finished yet to
 *  estimate from, it says so instead of inventing a number (ADR 0008). */
export function queueEstimate(eta: number | null | undefined, range?: [number, number] | null): string {
  if (eta == null) return "We'll start your plan as soon as there's room.";
  const about = `Estimated wait: about ${waitWords(eta)}`;
  if (!range || range[0] === range[1]) return `${about}.`;
  const [lo, hi] = range;
  if (hi < 90) return `${about} (usually ${Math.max(5, Math.round(lo / 5) * 5)} to ${Math.max(5, Math.round(hi / 5) * 5)} seconds).`;
  const l = Math.max(1, Math.round(lo / 60)), h = Math.max(l + 1, Math.round(hi / 60));
  return `${about} (usually ${l} to ${h} minutes).`;
}
