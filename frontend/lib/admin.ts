import { api } from "./api";
import type { Trip } from "./types";

/** The admin console's API (backend app/api/routes/admin.py, ADR 0010). Every call answers 404 to anyone who isn't an admin. */

export interface Paged<T> { items: T[]; total: number; page: number; page_size: number }

export interface AdminUser {
  id: string; name: string; email: string; phone: string | null; is_active: boolean; is_owner: boolean; is_admin: boolean;
  email_verified: boolean; phone_verified: boolean; created_at: string; plan: "free" | "plus"; plan_status: string;
  plan_ends: string | null; trip_credits: number; free_plans_used: number; free_plans_left: number; trips: number; last_trip_at: string | null;
}
export interface AdminJob { id: string; trip_id: string; kind: string; state: string; result: string | null; error: string | null; seconds: number | null; late_finish: boolean; created_at: string; user?: string }
export interface AdminUserDetail extends AdminUser {
  recent_trips: { id: string; title: string; destination: string; status: string; start_date: string; end_date: string; budget: number; currency: string; plan_count: number; created_at: string }[];
  recent_plans: AdminJob[];
}
export interface AdminTrip {
  id: string; title: string; status: string; origin: string; destination: string; start_date: string; end_date: string; travellers: number;
  budget: number; currency: string; plan_count: number; replan_count: number; estimated_cost: number | null; feasible: boolean | null;
  data_mode: string; created_at: string; owner: { id: string; name: string; email: string };
}
export interface AuditRow { id: string; at: string; actor: string; action: string; target_type: string | null; target_id: string | null; target_label: string | null; detail: Record<string, unknown> | null; reason: string | null }
export interface DayCount { date: string; count: number }
export interface Overview {
  users: { total: number; new_24h: number; new_7d: number; verified: number; suspended: number; admins: number; owners: number; plus: number };
  trips: { total: number; planned: number; new_24h: number };
  plans: { last_24h: number; by_state: Record<string, number>; refunds_24h: number; late_finishes_24h: number; median_seconds: number | null };
  runner: { running: number; waiting: number; max_concurrent: number; queue_limit: number; accepting: boolean; median_plan_seconds: number | null; brownout_level: number };
  budgets: { group: string; unit: string; limit: number | null; used: number; percent: number | null; stopped: boolean }[];
  series: { signups: DayCount[]; plans: DayCount[] };
  switches_on: string[]; recent_audit: AuditRow[]; generated_at: string;
}
export interface SwitchInfo { name: string; label: string; effect: string; people_see: string; on: boolean; source: "admin" | "env" | "default"; reason: string | null; by: string | null; at: string | null }
export interface NumberInfo { key: string; label: string; hint: string; low: number; high: number; value: number; default: number; source: "admin" | "default"; reason: string | null; by: string | null; at: string | null }
export interface Controls { switches: SwitchInfo[]; numbers: NumberInfo[] }

export interface UserEdit {
  is_active?: boolean; name?: string; free_plans_used?: number; trip_credits?: number; plan?: "free" | "plus"; plus_days?: number;
  email_verified?: boolean; phone_verified?: boolean; is_admin?: boolean; reason?: string;
}

const qs = (o: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export interface AdminReferral {
  id: string; created_at: string; code: string; status: "joined" | "rewarded" | "capped";
  inviter: { id: string; name: string | null; email: string | null }; friend: { id: string; name: string | null; email: string | null };
  friend_credits: number; inviter_credits: number; discount_pct: number; discount_used: boolean; rewarded_at: string | null;
}

export const admin = {
  referrals: () => api<{ referrals: AdminReferral[]; enabled: boolean;
    rewards: { friend_plans: number; inviter_plans: number; plus_discount_pct: number; monthly_cap: number } }>("/api/admin/referrals"),
  me: () => api<{ id: string; name: string; email: string; role: "owner" | "admin" }>("/api/admin/me"),
  overview: () => api<Overview>("/api/admin/overview"),
  users: (o: { q?: string; status?: string; plan?: string; sort?: string; page?: number; page_size?: number }) =>
    api<Paged<AdminUser>>(`/api/admin/users${qs(o)}`),
  user: (id: string) => api<AdminUserDetail>(`/api/admin/users/${id}`),
  editUser: (id: string, body: UserEdit) => api<AdminUserDetail>(`/api/admin/users/${id}`, { method: "PATCH", json: body }),
  deleteUser: (id: string, confirm_email: string, reason?: string) =>
    api<void>(`/api/admin/users/${id}/delete`, { method: "POST", json: { confirm_email, reason } }),
  trips: (o: { q?: string; status?: string; owner?: string; page?: number; page_size?: number }) => api<Paged<AdminTrip>>(`/api/admin/trips${qs(o)}`),
  trip: (id: string) => api<AdminTrip & { trip: Trip }>(`/api/admin/trips/${id}`),
  deleteTrip: (id: string, reason?: string) => api<void>(`/api/admin/trips/${id}/delete`, { method: "POST", json: { reason } }),
  plans: (state?: string) => api<{ items: AdminJob[] }>(`/api/admin/plans${qs({ state })}`),
  controls: () => api<Controls>("/api/admin/controls"),
  setSwitch: (name: string, on: boolean, reason?: string) => api<Controls>(`/api/admin/controls/switches/${name}`, { method: "PUT", json: { on, reason } }),
  setNumber: (key: string, value: number, reason?: string) => api<Controls>(`/api/admin/controls/settings/${key}`, { method: "PUT", json: { value, reason } }),
  clear: (key: string) => api<Controls>(`/api/admin/controls/${encodeURIComponent(key)}`, { method: "DELETE" }),
  audit: (o: { action?: string; q?: string; page?: number; page_size?: number }) => api<Paged<AuditRow>>(`/api/admin/audit${qs(o)}`),
  // existing operator views, also open to admins
  budgets: () => api<any>("/api/system/budgets"),
};
