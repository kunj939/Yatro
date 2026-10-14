import type {
  HighlightPlace, PlanEvent, Quota, ReferralCard, ShareLink, ShareResult, SharedTrip, Suggestion, Trip, TripCard, User } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "tripai.token";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: any) {
    super(message);
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try { return window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable */ }
}

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const e = body?.error;
    if (e) return new ApiError(res.status, e.code, e.message, e.details);
  } catch { /* not JSON */ }
  return new ApiError(res.status, "http_error", res.status >= 500
    ? "The planner is unreachable right now. Please try again in a moment."
    : `Request failed (${res.status}).`);
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...rest, headers: { ...headers(json !== undefined), ...(rest.headers || {}) },
      body: json !== undefined ? JSON.stringify(json) : rest.body, cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach Yaitro right now. Check your internet and try again in a moment.");
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** POST that returns newline-delimited JSON events (live planning progress). */
/** `idempotencyKey`: one per tap of a Plan button. If the connection drops and the same tap is sent again, the server treats it as the
 *  same plan, never a second one and never a second charge (ADR 0008). */
export async function streamEvents(path: string, body: unknown, onEvent: (e: PlanEvent) => void, idempotencyKey?: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { method: "POST", headers: { ...headers(true), ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}) },
      body: JSON.stringify(body ?? {}) });
  } catch {
    throw new ApiError(0, "network", "Can't reach Yaitro right now. Check your internet and try again in a moment.");
  }
  if (!res.ok || !res.body) throw await toError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) {
        const event = JSON.parse(line) as PlanEvent;
        if (event.type !== "ping") onEvent(event);
      }
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as PlanEvent);
}

export interface CalendarLinks { feed_url: string; webcal_url: string; google_url: string; outlook_url: string }
export interface AuthResult { access_token: string; user: User; subscription: Quota }
export interface SentCode { channel: string; sent_to: string; expires_in: number; resend_in: number; dev_code?: string | null }
export interface SignUp {
  name: string; email: string; phone: string; password: string; password_confirm: string; email_code: string; phone_code?: string;
  referral_code?: string;  // a friend's invite code; a wrong one is ignored, never refused
}

export const endpoints = {
  // Sign-up is two steps: a code to the email and one to the mobile (sendCode), then register with both codes.
  sendCode: (channel: "email" | "phone", target: string) =>
    api<SentCode>("/api/auth/send-code", { method: "POST", json: { channel, target } }),
  register: (body: SignUp) => api<AuthResult>("/api/auth/register", { method: "POST", json: body }),
  // "email" may also be the mobile number
  login: (email: string, password: string) => api<AuthResult>("/api/auth/login", { method: "POST", json: { email, password } }),
  me: () => api<{ user: User; subscription: Quota }>("/api/auth/me"),
  trips: () => api<{ trips: TripCard[] }>("/api/trips"),
  trip: (id: string) => api<Trip>(`/api/trips/${id}`),
  createTrip: (body: unknown) => api<Trip>("/api/trips", { method: "POST", json: body }),
  deleteTrip: (id: string) => api<void>(`/api/trips/${id}`, { method: "DELETE" }),
  subscription: () => api<Quota>("/api/subscription"),
  referrals: () => api<ReferralCard>("/api/subscription/referrals"),
  upgrade: (product: "plus" | "plus_yearly" | "one_trip") => api<Quota>("/api/subscription/upgrade", { method: "POST", json: { product } }),
  cancel: () => api<Quota>("/api/subscription/cancel", { method: "POST" }),
  autocomplete: (q: string) => api<{ results: Suggestion[]; message?: string }>(`/api/places/autocomplete?q=${encodeURIComponent(q)}`),
  reverse: (lat: number, lng: number) => api<{ result: Suggestion | null }>(`/api/places/reverse?lat=${lat}&lng=${lng}`),
  placeHighlights: (destination: string, placeId?: string | null) => {
    const q = new URLSearchParams({ destination });
    if (placeId) q.set("place_id", placeId);
    return api<{ results: HighlightPlace[]; message?: string; resolved: {
      place_id: string; name: string; address?: string | null; lat?: number | null; lng?: number | null; currency?: string | null } | null }>(
      `/api/places/highlights?${q.toString()}`);
  },
  demoTrip: () => api<Trip>("/api/demo/trip"),
  config: () => api<{ mode: string; app_name: string; free_trip_plans: number; share_links_enabled?: boolean; upi_links_enabled?: boolean; plan_emails?: boolean }>("/api/config"),
  addMember: (id: string, body: unknown) => api(`/api/trips/${id}/members`, { method: "POST", json: body }),
  removeMember: (id: string, mid: string) => api<void>(`/api/trips/${id}/members/${mid}`, { method: "DELETE" }),
  // "this train didn't run / ran at another time": its route's timetable is re-checked first (ADR 0003)
  reportTrain: (id: string, item_id: string, problem: "didnt_run" | "wrong_time") =>
    api<{ message: string }>(`/api/trips/${id}/report-train`, { method: "POST", json: { item_id, problem } }),
  // Sharing (ADR 0006): the full link and the WhatsApp text are made when the person taps Share, and stored nowhere
  createShare: (id: string, body: { show_budget: boolean; include_split: boolean; expires: "default" | "never" }) =>
    api<ShareResult>(`/api/trips/${id}/share`, { method: "POST", json: body }),
  shareText: (id: string, token: string) =>
    api<Omit<ShareResult, "share">>(`/api/trips/${id}/share-text`, { method: "POST", json: { token } }),
  shares: (id: string) => api<{ shares: ShareLink[] }>(`/api/trips/${id}/shares`),
  updateShare: (id: string, sid: string, body: { show_budget?: boolean; include_split?: boolean; expires?: "default" | "never"; extend_days?: number }) =>
    api<ShareLink>(`/api/trips/${id}/shares/${sid}`, { method: "PATCH", json: body }),
  revokeShare: (id: string, sid: string) => api<void>(`/api/trips/${id}/shares/${sid}`, { method: "DELETE" }),
  // Calendar (ADR 0015): a feed calendars subscribe to, so a changed plan updates there too; or a one-off .ics file
  // The trip chat (ADR 0016): an answer about this trip, or "change" (then the same message goes through /replan)
  chat: (id: string, message: string, history: { role: "user" | "assistant"; text: string }[]) =>
    api<{ action: "answer" | "change"; text?: string; by?: string }>(`/api/trips/${id}/chat`, { method: "POST", json: { message, history } }),
  calendarOn: (id: string) => api<CalendarLinks>(`/api/trips/${id}/calendar`, { method: "POST" }),
  calendarOff: (id: string) => api<void>(`/api/trips/${id}/calendar`, { method: "DELETE" }),
  calendarFile: async (id: string): Promise<string> => {
    let res: Response;
    try { res = await fetch(`${API_URL}/api/trips/${id}/calendar.ics`, { headers: headers(false), cache: "no-store" }); }
    catch { throw new ApiError(0, "network", "Can't reach Yaitro right now. Check your internet and try again in a moment."); }
    if (!res.ok) throw await toError(res);
    return res.text();
  },
  shared: (token: string) => api<SharedTrip>(`/api/share/${encodeURIComponent(token)}`),
  // the signed-in person's OWN UPI ID (null removes it); nobody else's is ever stored
  // "Email me this plan" now, and whether new plans are emailed automatically (the person's own choice)
  emailPlan: (id: string) => api<{ sent_to: string }>(`/api/trips/${id}/email`, { method: "POST" }),
  setNotifications: (email_plans: boolean) => api<User>("/api/auth/me/notifications", { method: "PATCH", json: { email_plans } }),
  setUpi: (upi_id: string | null) => api<User>("/api/auth/me/upi", { method: "PATCH", json: { upi_id } }),
  addExpense: (id: string, body: unknown) => api(`/api/trips/${id}/expenses`, { method: "POST", json: body }),
  deleteExpense: (id: string, eid: string) => api<void>(`/api/trips/${id}/expenses/${eid}`, { method: "DELETE" }),
};