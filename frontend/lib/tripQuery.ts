/** Reads a trip typed as one sentence into the planner's fields: "Goa for 3 days under ₹20,000", "weekend in Dubai
 * for two", "Delhi to Agra this Saturday, veg", "Manali 5 din 25k me with friends".
 *
 * Plain rules, no network: anything it can't read is left for the traveller to fill in, and the planner keeps the
 * whole sentence as the trip's notes, so nothing typed is lost. */

export interface TripQuery {
  origin?: string;
  destination?: string;
  startDate?: string; // YYYY-MM-DD
  days?: number;
  budget?: number;
  budgetCurrency?: string; // the currency the amount was typed in
  travellers?: number;
  travellerType?: "solo" | "couple" | "family" | "friends";
  food?: "veg";
  modes?: string[];
  /** Ages mentioned: "with 2 kids", "with my parents" -> ["kids"], ["seniors"] (adults are added by the wizard) */
  ageGroups?: ("kids" | "teens" | "seniors")[];
}

const WORD_NUM: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  ek: 1, do: 2, teen: 3, char: 4, chaar: 4, paanch: 5, panch: 5, chhe: 6, saat: 7,
};
const NUM = `(\\d{1,2}|${Object.keys(WORD_NUM).join("|")})`;
const toNum = (s: string) => (/^\d+$/.test(s) ? Number(s) : WORD_NUM[s.toLowerCase()] ?? NaN);

const CURRENCIES: [RegExp, string][] = [
  [/₹|\brs\.?|\binr\b|rupees?|rupaye/i, "INR"], [/\$|\busd\b|dollars?/i, "USD"], [/€|\beur\b|euros?/i, "EUR"],
  [/£|\bgbp\b|pounds?/i, "GBP"], [/\baed\b|dirhams?/i, "AED"], [/\bsgd\b/i, "SGD"], [/\bthb\b|baht/i, "THB"],
];
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const FILLER = new Set(["trip", "plan", "planning", "holiday", "holidays", "vacation", "visit", "visiting", "travel", "travelling",
  "traveling", "going", "go", "a", "an", "the", "in", "for", "with", "me", "mein", "main", "ka", "ki", "ke", "ko", "jana", "jaana",
  "hai", "chalo", "chahiye", "under", "this", "next", "coming", "my", "our", "please", "pls", "i", "we", "want", "to", "around",
  "about", "and", "budget", "of", "on", "at", "from", "trip.", "getaway", "tour", "it", "let's", "lets", "some", "days", "day"]);

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function plusDays(base: Date, n: number) { const d = new Date(base); d.setDate(d.getDate() + n); return d; }
function upcomingWeekday(today: Date, wd: number, minAhead = 1) {
  for (let i = minAhead; i < minAhead + 7; i++) { const d = plusDays(today, i); if (d.getDay() === wd) return d; }
  return plusDays(today, minAhead);
}
const titleCase = (s: string) => s.replace(/\S+/g, (w) => (w.length > 2 || /^[a-z]{2}$/i.test(w) ? w[0].toUpperCase() + w.slice(1) : w));

export function parseTripQuery(input: string, today = new Date()): TripQuery {
  const out: TripQuery = {};
  let t = ` ${input.replace(/\s+/g, " ").trim()} `;
  const cut = (re: RegExp) => { t = t.replace(re, " | "); };

  // How long: "3 days", "5 din", "2 nights", "a week", "weekend", "day trip"
  let m = t.match(new RegExp(`\\b${NUM}\\s*(?:-\\s*)?(?:days?|din)\\b`, "i"));
  if (m) { out.days = toNum(m[1]); cut(new RegExp(m[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i")); }
  m = t.match(new RegExp(`\\b${NUM}\\s*(?:nights?|raat|raatein)\\b`, "i"));
  if (m) { out.days = toNum(m[1]) + 1; cut(new RegExp(m[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i")); }
  if (/\b(?:a|one|1)\s+week\b/i.test(t)) { out.days ??= 7; cut(/\b(?:a|one|1)\s+week\b/i); }
  if (/\bday\s*trip\b|\bek\s+din\b|\bone\s+day\b/i.test(t)) { out.days ??= 1; cut(/\bday\s*trip\b|\bek\s+din\b|\bone\s+day\b/i); }
  if (/\b(?:this\s+|next\s+|coming\s+)?weekend\b/i.test(t)) {
    out.startDate = iso(upcomingWeekday(today, 6, 1));
    out.days ??= 2;
    cut(/\b(?:this\s+|next\s+|coming\s+)?weekend\b/i);
  }

  // When: "today", "tomorrow", "this Saturday", "12 Oct", "Oct 12"
  if (/\b(?:today|aaj)\b/i.test(t)) { out.startDate = iso(today); cut(/\b(?:today|aaj)\b/i); }
  if (/\b(?:tomorrow|kal)\b/i.test(t)) { out.startDate = iso(plusDays(today, 1)); cut(/\b(?:tomorrow|kal)\b/i); }
  m = t.match(/\b(?:this\s+|next\s+|coming\s+|on\s+)?(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b/i);
  if (m) { out.startDate = iso(upcomingWeekday(today, WEEKDAYS.indexOf(m[1].toLowerCase()), 1)); cut(new RegExp(m[0], "i")); }
  m = t.match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b/i)
    ?? t.match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b/i);
  if (m) {
    const [day, mon] = /^\d/.test(m[1]) ? [Number(m[1]), m[2]] : [Number(m[2]), m[1]];
    let d = new Date(today.getFullYear(), MONTHS.indexOf(mon.toLowerCase()), day);
    if (d < new Date(today.getFullYear(), today.getMonth(), today.getDate())) d = new Date(today.getFullYear() + 1, d.getMonth(), day);
    out.startDate = iso(d);
    cut(new RegExp(m[0], "i"));
  }

  // How much: "under ₹20,000", "25k", "1.5 lakh", "1500 euros", "budget 8000"
  const moneyRe = /(?:\b(under|below|within|upto|up to|max|budget(?:\s+of)?|less than|around|about|in)\s+)?(₹|rs\.?|inr|\$|usd|€|eur|£|gbp|aed|sgd|thb)?\s?(\d[\d,]*(?:\.\d+)?)\s?(k|thousand|l|lakh|lakhs|lac)?\b\s?(rupees?|rupaye|rs|inr|dollars?|usd|euros?|eur|pounds?|gbp|dirhams?|aed|sgd|baht)?/gi;
  let best: { amount: number; currency: string; text: string } | null = null;
  for (const mm of t.matchAll(moneyRe)) {
    const [whole, kw, pre, num, mult, post] = mm;
    const after = t.slice((mm.index ?? 0) + whole.length);
    if (/^\s*(?:days?|din|nights?|raat|people|persons|adults|log|friends|travellers|pax|am|pm|st|nd|rd|th)\b/i.test(after)) continue;
    let amount = Number(num.replace(/,/g, ""));
    if (!amount) continue;
    const m2 = (mult || "").toLowerCase();
    if (m2 === "k" || m2 === "thousand") amount *= 1000;
    else if (m2) amount *= 100000;
    const marked = !!(pre || post || m2 || (kw && !/^(?:in|around|about)$/i.test(kw)));
    if (!marked && amount < 1000) continue; // "for 2", "12 Oct": not money
    const cur = CURRENCIES.find(([re]) => re.test(`${pre ?? ""} ${post ?? ""}`))?.[1] ?? "INR";
    if (!best || marked) best = { amount, currency: cur, text: whole };
    if (marked) break;
  }
  if (best) { out.budget = Math.round(best.amount); out.budgetCurrency = best.currency; t = t.replace(best.text, " | "); }

  // Who: "solo", "couple", "with family", "friends", "for two", "4 people"
  if (/\b(?:solo|alone|akela|akeli|akele)\b/i.test(t)) { out.travellers = 1; out.travellerType = "solo"; cut(/\b(?:solo|alone|akela|akeli|akele)\b/i); }
  if (/\b(?:couple|honeymoon|with (?:my )?(?:wife|husband|partner|girlfriend|boyfriend|gf|bf))\b/i.test(t)) {
    out.travellers = 2; out.travellerType = "couple";
    cut(/\b(?:a\s+)?(?:couple|honeymoon|with (?:my )?(?:wife|husband|partner|girlfriend|boyfriend|gf|bf))\b/i);
  }
  m = t.match(new RegExp(`\\bfamily(?:\\s+of\\s+${NUM})?\\b`, "i"));
  if (m) { out.travellerType = "family"; if (m[1]) out.travellers = toNum(m[1]); cut(new RegExp(`\\b(?:with\\s+)?(?:my\\s+)?family(?:\\s+of\\s+${NUM})?\\b`, "i")); }
  if (/\b(?:friends|dost|doston|dosto|yaar|gang)\b/i.test(t)) { out.travellerType = "friends"; cut(/\b(?:with\s+)?(?:my\s+)?(?:friends|dost|doston|dosto|yaar|gang)\b/i); }
  m = t.match(new RegExp(`\\b${NUM}\\s+(?:people|persons|adults|travellers|log|pax|of us)\\b`, "i"))
    ?? t.match(new RegExp(`\\bfor\\s+${NUM}\\b(?!\\s*(?:days?|nights?|din))`, "i"));
  if (m) { out.travellers = toNum(m[1]); cut(new RegExp(m[0], "i")); }
  if (out.travellers && !out.travellerType) out.travellerType = out.travellers === 1 ? "solo" : out.travellers === 2 ? "couple" : "friends";

  // Ages: "with 2 kids", "my 5 year old", "bachche", "with parents", "grandparents", "senior citizens"
  const ages = new Set<"kids" | "teens" | "seniors">();
  const KIDS = /\b(?:(?:with\s+)?(?:my\s+|our\s+)?(?:\d{1,2}\s+)?(?:kids?|child(?:ren)?|toddlers?|babies|baby|bachh?[ae]|bachhon|bacchon|bachchon|little\s+ones?))\b/i;
  const TEENS = /\b(?:with\s+)?(?:my\s+|our\s+)?(?:\d{1,2}\s+)?(?:teens?|teenagers?|teenage)\b/i;
  const OLDER = /\b(?:with\s+)?(?:my\s+|our\s+)?(?:parents|mom|mum|mother|dad|father|maa|papa|mummy|grand(?:parents?|ma|pa|mother|father)|dadi|dada|nani|nana|seniors?|senior\s+citizens?|elderly|buzurg|buzurgon)\b/i;
  const YEARS = /\b(?:with\s+)?(?:a\s+|my\s+)?(\d{1,2})[\s-]*(?:years?|yrs?|saal)[\s-]*old\b/i;
  if (KIDS.test(t)) { ages.add("kids"); cut(KIDS); }
  if (TEENS.test(t)) { ages.add("teens"); cut(TEENS); }
  if (OLDER.test(t)) { ages.add("seniors"); cut(OLDER); }
  m = t.match(YEARS);
  if (m) { const n = Number(m[1]); if (n <= 12) ages.add("kids"); else if (n <= 17) ages.add("teens"); else if (n >= 60) ages.add("seniors"); cut(YEARS); }
  if (ages.size) {
    out.ageGroups = [...ages];
    if (!out.travellerType) out.travellerType = "family";
  }

  // Food and travel
  if (/\b(?:veg|vegetarian|jain|shakahari)\b/i.test(t) && !/\bnon[\s-]?veg/i.test(t)) { out.food = "veg"; cut(/\b(?:pure\s+)?(?:veg|vegetarian|jain|shakahari)(?:\s+food)?\b/i); }
  const modes = new Set<string>();
  if (/\b(?:by\s+)?(?:train|rail)\b|\btrain\s+se\b/i.test(t)) modes.add("train");
  if (/\b(?:by\s+)?bus\b/i.test(t)) modes.add("bus");
  if (/\b(?:fly|flight|by\s+air|plane)\b/i.test(t)) modes.add("flight");
  if (/\b(?:by\s+car|drive|road\s*trip|self[\s-]drive)\b/i.test(t)) modes.add("own_car");
  if (modes.size) { out.modes = [...modes]; cut(/\b(?:by\s+)?(?:train|rail|bus|car|air|plane|flight)(?:\s+se)?\b|\b(?:fly|drive|road\s*trip|self[\s-]drive)\b/gi); }

  // Where: "from X to Y", "X to Y", "X se Y", "X → Y"; otherwise what's left is the destination
  const clean = (s: string) => titleCase(s.split(/[\s,|.!?]+/).filter((w) => w && !FILLER.has(w.toLowerCase())).join(" ").trim());
  const route = t.match(/(?:\bfrom\s+)?([^|,]+?)\s+(?:to|se|→|->|till)\s+([^|,]+)/i);
  if (route && clean(route[1]) && clean(route[2])) {
    out.origin = clean(route[1]);
    out.destination = clean(route[2]);
  } else {
    const to = t.match(/\b(?:to|in|visit|explore)\s+([^|,]+)/i);
    out.destination = clean(to ? to[1] : t) || undefined;
  }
  return out;
}
