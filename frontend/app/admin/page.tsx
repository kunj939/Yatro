"use client";

import Link from "next/link";
import { ArrowRight, Power } from "lucide-react";
import { admin, type DayCount } from "@/lib/admin";
import { Card, ErrorBox, Loading, PageHeader, Pill, RefreshButton, Stat, ago, useLoad, when } from "@/components/admin/ui";

function Bars({ data, label }: { data: DayCount[]; label: string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  const total = data.reduce((a, d) => a + d.count, 0);
  return (
    <div>
      <p className="mb-3 text-sm text-ink-mute"><span className="num font-semibold text-ink">{total}</span> in the last 14 days</p>
      <div className="flex h-32 items-end gap-1.5" role="img" aria-label={`${label} per day, last 14 days`}>
        {data.map((d) => (
          <div key={d.date} className="group relative flex h-full min-w-0 flex-1 flex-col justify-end" title={`${d.date}: ${d.count}`}>
            <div className="w-full rounded-t-md bg-signal/80 transition-all group-hover:bg-signal" style={{ height: `${Math.max(d.count ? 6 : 2, (d.count / max) * 100)}%`, opacity: d.count ? 1 : 0.25 }} />
          </div>
        ))}
      </div>
      <div className="num mt-1.5 flex justify-between text-[11px] text-ink-mute"><span>{data[0]?.date.slice(5)}</span><span>{data[data.length - 1]?.date.slice(5)}</span></div>
    </div>
  );
}

const UNIT: Record<string, string> = { usd: "$", calls: "", plans: "", texts: "" };
const GROUP_LABEL: Record<string, string> = { google: "Google Maps", llm: "AI text", search: "Web search", railradar: "RailRadar", irctc1: "IRCTC", plans: "New plans", sms: "SMS codes" };

export default function AdminOverview() {
  const { data: o, error, busy, reload } = useLoad(() => admin.overview(), []);
  return (
    <>
      <PageHeader title="Overview" text="How Yatro is doing right now. Today resets at midnight, India time." actions={<RefreshButton onClick={reload} busy={busy} />} />
      {error && <ErrorBox text={error} retry={reload} />}
      {!o && !error && <Loading />}
      {o && (
        <div className="space-y-6">
          {o.switches_on.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-caution/40 bg-caution-soft px-4 py-3 text-[15px] text-caution">
              <Power className="h-4 w-4 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 font-medium">Stopped right now: {o.switches_on.join(", ")}</span>
              <Link href="/admin/controls" className="inline-flex items-center gap-1 font-semibold underline underline-offset-4">Open controls <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Users" value={o.users.total} sub={<>+{o.users.new_24h} today · +{o.users.new_7d} this week</>} />
            <Stat label="Plus subscribers" value={o.users.plus} sub={<>{o.users.verified} verified · {o.users.suspended} suspended</>} />
            <Stat label="Trips" value={o.trips.total} sub={<>{o.trips.planned} planned · +{o.trips.new_24h} today</>} />
            <Stat label="Plans, last 24 h" value={o.plans.last_24h} tone={o.plans.refunds_24h ? "bad" : undefined}
                  sub={<>{o.plans.by_state.done ?? 0} done · {o.plans.refunds_24h} refunded{o.plans.median_seconds ? ` · ${o.plans.median_seconds}s typical` : ""}</>} />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="New accounts"><Bars data={o.series.signups} label="New accounts" /></Card>
            <Card title="New plans"><Bars data={o.series.plans} label="New plans" /></Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
            <Card title="Planner" aside={<Pill tone={o.runner.accepting ? "good" : "bad"}>{o.runner.accepting ? "Taking plans" : "Not taking plans"}</Pill>}>
              <dl className="num grid grid-cols-2 gap-3 text-[15px]">
                {([["Running now", `${o.runner.running} / ${o.runner.max_concurrent}`], ["Waiting", `${o.runner.waiting} (limit ${o.runner.queue_limit})`],
                  ["Typical plan", o.runner.median_plan_seconds ? `${o.runner.median_plan_seconds} s` : "—"],
                  ["Busy mode", o.runner.brownout_level ? `Level ${o.runner.brownout_level}` : "Off"]] as const).map(([k, v]) => (
                  <div key={k} className="rounded-xl bg-haze px-3.5 py-3"><dt className="text-xs text-ink-mute">{k}</dt><dd className="mt-0.5 font-bold">{v}</dd></div>
                ))}
              </dl>
              {o.plans.late_finishes_24h > 0 && <p className="mt-3 text-sm text-caution">{o.plans.late_finishes_24h} plan(s) finished after being refunded.</p>}
            </Card>

            <Card title="Today's spend" aside={<Link href="/admin/controls" className="text-sm font-semibold text-signal-deep underline-offset-4 hover:underline">Controls</Link>}>
              <ul className="space-y-3">
                {o.budgets.filter((b) => b.limit).map((b) => (
                  <li key={b.group}>
                    <div className="flex items-baseline justify-between gap-3 text-[14px]">
                      <span className="font-medium">{GROUP_LABEL[b.group] ?? b.group}</span>
                      <span className="num text-ink-soft">{UNIT[b.unit] ?? ""}{b.used} / {UNIT[b.unit] ?? ""}{b.limit}{b.stopped && <Pill tone="bad"> stopped</Pill>}</span>
                    </div>
                    <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-haze-2">
                      <div className={`h-full rounded-full ${(b.percent ?? 0) >= 80 ? "bg-danger" : "bg-signal"}`} style={{ width: `${Math.min(100, b.percent ?? 0)}%` }} />
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          <Card title="Recent admin activity" aside={<Link href="/admin/audit" className="text-sm font-semibold text-signal-deep underline-offset-4 hover:underline">Full log</Link>}>
            {o.recent_audit.length === 0 ? <p className="text-ink-mute">Nothing has been changed from the console yet.</p> : (
              <ul className="divide-y divide-line">
                {o.recent_audit.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2.5 text-[14px]">
                    <Pill tone="brand">{a.action}</Pill>
                    <span className="min-w-[11rem] flex-1 truncate"><b className="font-semibold">{a.actor}</b> · {a.target_label ?? a.target_id ?? ""}</span>
                    <span className="text-ink-mute" title={when(a.at)}>{ago(a.at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
