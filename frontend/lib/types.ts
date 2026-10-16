export type VerificationState =
  | "verified_official" | "verified_provider" | "estimated" | "uncertain" | "unavailable" | "demo" | "n/a";

export interface Alternative {
  id: string; kind: "place" | "transport" | "hotel"; title: string; rank: number; score: number;
  cost: number | null; cost_delta: number | null; time_delta_minutes: number; distance_from_prev_m: number | null;
  reason: string | null; checks: Record<string, boolean>; meta: Record<string, any>;
}

export interface Source {
  url?: string; title?: string; domain?: string; tier?: string; status?: string; label?: string; state?: string;
  value?: { open: string; close: string } | null; verified_at?: string;
}

/** One train of a journey with a change ("meta.legs" on a transport item). Times are local ISO strings. */
export interface TransportLeg {
  name: string; number: string | null; service_class: string | null; from_name: string; to_name: string;
  depart_at: string; arrive_at: string; duration_minutes: number; cost_per_person: number;
  depart_local?: string; arrive_local?: string;  // a flight's own airport clocks (ADR 0020)
}

export interface ItineraryItem {
  id: string; position: number; day_index: number;
  kind: "transport" | "activity" | "meal" | "sunset" | "stay" | "arrival" | "free";
  // time/end_time are null for a train with no timetable (I1): show meta.when ("Morning") instead
  start_at: string; end_at: string; time: string | null; end_time: string | null; duration_minutes: number;
  title: string; subtitle: string | null; place_id: string | null;
  distance_from_prev_m: number | null; travel_minutes_from_prev: number | null; travel_mode: string | null;
  travel_cost: number | null; leg_state: string | null; polyline: string | null;
  cost: number | null; cost_state: string | null; recommended_minutes: number | null;
  opening_time: string | null; closing_time: string | null; hours_state: string | null; hours_note: string | null;
  sources: Source[]; reasons: { key: string; text: string }[]; explanation: string | null;
  meta: Record<string, any>; alternatives: Alternative[];
}

export interface BudgetCategory {
  key: string; label: string; amount: number; typical: number;
  items: { label: string; amount: number; state: string }[];
}

export interface BudgetBreakdown {
  budget: number; planned: number; remaining: number; subtotal: number; status: "within" | "tight" | "over";
  travellers: number; per_person: number; buffer_pct: number; categories: BudgetCategory[];
}

export interface StageEntry { key: string; label: string; status: string; detail?: string; ms?: number }

export interface Itinerary {
  // summary is null (and summary_expired true) once the Google content it was written from is 30 days old (I6)
  id: string; version: number; title: string; summary: string | null; summary_expired?: boolean; tips: string[];
  total_cost: number; remaining: number; budget: BudgetBreakdown; warnings: string[];
  pipeline_trace: StageEntry[]; weather: { summary: string; max_c: number | null; min_c: number | null;
    rain_chance: number | null; state: string; source: string } | null;
  llm_status: string; change_note: string | null; created_at: string; data_mode: string | null;
  local_mode: string | null; days: { index: number; date: string; label: string; items: ItineraryItem[] }[];
  // set when the plan was made under load (ADR 0008): the level, and the words for each part it affected
  brownout?: { level: number; labels: { description?: string; alternatives?: string; options?: string; rail?: string } } | null;
}

/** A plan waiting its turn: "High demand — you're #N", with an estimate made from recent real plan durations. */
export interface QueueInfo { position: number; waiting: number; eta_seconds: number | null; eta_range: [number, number] | null }

export interface Member { id: string; name: string; email: string | null; budget: number | null; food_pref: string | null; is_owner: boolean }
export interface Expense {
  id: string; description: string; category: string; amount: number; paid_by_member_id: string; split_type: string;
  created_at: string; splits: { member_id: string; amount: number }[];
}
export interface ExpenseSummary {
  total: number; by_category: Record<string, number>;
  members: { member_id: string; name: string; paid: number; share: number; net: number }[];
  settlements: { from_member_id: string; from_name: string; to_member_id: string; to_name: string; amount: number }[];
}

export interface Adjustment { key: string; label: string; description: string; saves?: number; apply: Record<string, any> }
export interface Feasibility {
  feasible: boolean; kind?: "budget" | "time" | "quality" | "no_service"; message?: string; main_conflict?: string; adjustments?: Adjustment[];
  minimum?: Record<string, number>; status?: string; transport?: string; checks?: Record<string, boolean>;
}

/** Who is in the party, by age (the wizard's "Who's travelling?"): kids 0-12, teens 13-17, adults 18-59, seniors 60+. */
export type AgeGroup = "kids" | "teens" | "adults" | "seniors";

export interface Preferences {
  traveller_type: string; age_groups?: AgeGroup[]; transport_modes: string[]; food: string; trip_styles: string[]; stay: string; pace: string;
  notes: string | null; interests: string[]; excluded_place_ids: string[]; pinned_place_ids: string[];
  pinned_transport: Record<string, string>;
}

export interface TripCard {
  id: string; title: string; status: string; origin: string; destination: string; start_date: string; end_date: string;
  destination_lat?: number | null; destination_lng?: number | null;
  travellers: number; traveller_type: string | null; budget: number; estimated_cost: number | null;
  remaining: number | null; feasible: boolean | null; data_mode: string; created_at: string; currency?: string;
}

export interface Trip extends TripCard {
  timezone: string;  // the destination's clock, e.g. Asia/Kolkata (plan times are this wall clock)
  start_time: string; end_time: string;
  origin_detail: { name: string; place_id: string | null; lat: number | null; lng: number | null };
  destination_detail: { name: string; place_id: string | null; lat: number | null; lng: number | null };
  currency: string; preferences: Preferences; feasibility: Feasibility | null; plan_count: number;
  home_currency?: string | null; home_rate?: number | null;  // the starting point's money, and a rough rate to it
  replan_count: number; replans_left: number; quota_source: string | null; read_only: boolean;
  members: Member[]; expenses: Expense[]; expense_summary: ExpenseSummary; itinerary: Itinerary | null;
  versions: { version: number; created_at: string; change_note: string | null; total_cost: number }[];
}

export interface Quota {
  plan: string; status: string; free_plans_total: number; free_plans_used: number; free_plans_left: number;
  trip_credits: number; can_plan: boolean; current_period_end: string | null;
  prices: { plus_monthly_inr: number; plus_yearly_inr?: number; one_trip_inr: number; plus_first_month_inr?: number | null };
  payments_mode: string; free_plans_reset?: string | null;
}
/** Invite friends (ADR 0012): the signed-in user's code and how it's going. */
export interface ReferralCard {
  enabled: boolean; code: string; link: string; invited: number; rewarded: number; plans_earned: number; invited_by: string | null;
  rewards: { friend_plans: number; inviter_plans: number; plus_discount_pct: number; monthly_cap: number };
}
export interface User {
  id: string; name: string; email: string; free_plans_used: number;
  phone?: string | null; email_verified?: boolean; phone_verified?: boolean;
  upi_id?: string | null; // the person's OWN UPI ID, entered by them
  email_plans?: boolean; // email me my plan when a new one is ready (default on)
  is_admin?: boolean; // an owner or admin: shows the Admin link (the console itself is enforced by the server)
}

export interface ShareLink {
  id: string; prefix: string; created_at: string; expires_at: string | null; show_budget: boolean;
  include_split: boolean; view_count: number; last_viewed_at: string | null; active: boolean;
}
/** wa_url opens WhatsApp to pick a chat; wa_me_url opens it on the signed-in person's OWN saved mobile (null without one). Neither sends anything. */
export interface ShareResult { share: ShareLink; url: string; text: string; wa_url: string; wa_me_url?: string | null }

/** What a share link shows (ADR 0006): built server-side from an allow-list, no emails, phones or personal budgets. */
export interface SharedItem {
  id: string; day_index: number; kind: ItineraryItem["kind"]; start_at: string; end_at: string; time: string | null;
  end_time: string | null; duration_minutes: number; title: string; subtitle: string | null; cost: number | null;
  cost_state: string | null; travel_mode: string | null; travel_minutes_from_prev: number | null;
  distance_from_prev_m: number | null; travel_cost: number | null; opening_time: string | null; closing_time: string | null;
  explanation: string | null; meta: Record<string, any>;
}
export interface SharedTrip {
  trip: { title: string; destination: string; start_date: string; end_date: string; travellers: number; currency: string };
  itinerary: { title: string; summary: string | null; tips: string[]; total_cost: number; cost_per_person: number;
    days: { index: number; date: string; label: string; items: SharedItem[] }[] };
  google: { attribution: string | null; valid_until: string | null };
  budget?: { budget: number; remaining: number };
  split?: { total: number; members: { name: string; paid: number; share: number; net: number }[];
    settlements: { from: string; to: string; amount: number }[]; expenses: { description: string; amount: number; paid_by: string | null }[] };
}

export interface PlanEvent {
  type: "stage" | "result" | "error" | "ping" | "queue";
  key?: string; label?: string; status?: string; detail?: string; message?: string;
  // status "unavailable": a plan that can't be made right now is a normal answer, never an error (ADR 0007)
  reason?: "provider_busy" | "daily_cap" | "high_demand" | "paused"; retry_at?: string; quota_used?: boolean;
  // type "queue": a plan waiting its turn (ADR 0008). eta_seconds is null until real plans have finished to estimate from.
  position?: number; waiting?: number; eta_seconds?: number | null; eta_range?: [number, number] | null;
  trip?: Trip; feasibility?: Feasibility;
  error?: { code: string; message: string; details?: any };
}

export interface Suggestion { place_id: string; label: string; secondary: string | null; lat: number | null; lng: number | null }
export interface HighlightPlace { place_id: string; name: string; teaser: string | null; rating: number | null; rating_count: number | null }
/** Plan together (ADR 0022): what the organiser and the friends see. Friends never get anyone's budget. */
export interface GroupOption { id: string; name: string; votes: number; lat: number | null; lng: number | null }
export interface GroupSummary {
  id: string; title: string; origin: string; window_start: string; window_end: string; days: number;
  status: "open" | "made" | "closed" | "expired"; expires_at: string; note: string | null; stay: string;
  options: GroupOption[]; replies_count: number; max_replies: number;
  best_dates: { start: string; end: string; fits: number; of: number }[];
  days_heat: { date: string; count: number }[];
  food: { veg: number; non_veg: number; both: number }; budgets_given: number; trip_id: string | null; currency: string;
}
export interface GroupReply { id: string; name: string; budget: number | null; food: string; dates: string[]; votes: string[]; is_owner: boolean; updated_at: string }
export interface GroupOwnerView extends GroupSummary {
  url: string; budget_hint: number | null; replies: GroupReply[];
  budget: { per_person: number | null; people: number; total: number | null; from: "lowest" | "organiser" | null; gave: number };
}
export interface GroupPublicView extends GroupSummary {
  organiser: string; names: string[]; budget_hint: number | null;
  mine: { name: string; budget: number | null; food: string; dates: string[]; votes: string[] } | null;
}
export interface GroupBundle { group: GroupOwnerView; text: string; wa_url: string }
export interface GroupCard { id: string; title: string; status: GroupSummary["status"]; replies_count: number; options: string[];
  window_start: string; window_end: string; days: number; trip_id: string | null; created_at: string }
export interface LateResult { status: "shifted" | "missed" | "nothing"; moved: number; dropped: string[]; shortened: string[];
  missed: { title: string; time: string } | null; note: string | null }
