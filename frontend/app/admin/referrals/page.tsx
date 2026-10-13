"use client";

import Link from "next/link";
import { admin } from "@/lib/admin";
import { Card, ErrorBox, Loading, PageHeader, Pill, RefreshButton, Stat, ago, useLoad, when } from "@/components/admin/ui";

const STATUS: Record<string, { label: string; tone: "brand" | "good" | "neutral" | "bad" }> = {
  joined: { label: "Joined, no plan yet", tone: "neutral" }, rewarded: { label: "Inviter rewarded", tone: "good" },
  capped: { label: "Over the monthly cap", tone: "bad" },
};

/** Invite-a-friend (ADR 0012): who invited whom and what each got. The offer itself is changed in Controls. */
export default function AdminReferrals() {
  const { data, error, busy, reload } = useLoad(() => admin.referrals(), []);
  const rows = data?.referrals ?? [];
  const rewarded = rows.filter((r) => r.status === "rewarded");
  return (
    <>
      <PageHeader title="Referrals" text="Friends who joined with an invite code, and the free plans each side received."
                  actions={<RefreshButton onClick={reload} busy={busy} />} />
      {error && <ErrorBox text={error} retry={reload} />}
      {!data && !error && <Loading />}
      {data && (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-4">
            <Stat label="Friends joined" value={rows.length} />
            <Stat label="Went on to plan a trip" value={rewarded.length} sub={rows.length ? `${Math.round((rewarded.length / rows.length) * 100)}% of those invited` : undefined} tone="good" />
            <Stat label="Plans given to friends" value={rows.reduce((n, r) => n + r.friend_credits, 0)} />
            <Stat label="Plans given to inviters" value={rows.reduce((n, r) => n + r.inviter_credits, 0)} />
          </div>
          <Card title="The offer now" aside={<Link href="/admin/controls" className="text-[13px] font-semibold text-signal-deep underline underline-offset-4">Change in Controls</Link>}>
            <p className="text-[14px] text-ink-soft">
              {data.enabled ? "Invites are on. " : <><b className="text-danger">Invites are paused.</b> </>}
              Friend gets {data.rewards.friend_plans} plan{data.rewards.friend_plans === 1 ? "" : "s"}
              {data.rewards.plus_discount_pct ? ` and ${data.rewards.plus_discount_pct}% off their first month of Plus` : ""}; inviter gets {data.rewards.inviter_plans} plan{data.rewards.inviter_plans === 1 ? "" : "s"} when the friend&apos;s first plan is ready
              {data.rewards.monthly_cap ? `, at most ${data.rewards.monthly_cap} rewards a month` : ""}.
            </p>
          </Card>
          <Card className="mt-5 !p-0 overflow-hidden">
            {rows.length === 0 && <p className="px-5 py-10 text-center text-ink-mute">No one has joined with an invite yet.</p>}
            <ul className="divide-y divide-line">
              {rows.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-3.5 text-[14px]">
                  <span className="min-w-[14rem] flex-1">
                    <Link href={`/admin/users/${r.inviter.id}`} className="font-semibold hover:underline">{r.inviter.name ?? "—"}</Link>
                    <span className="text-ink-mute"> invited </span>
                    <Link href={`/admin/users/${r.friend.id}`} className="font-semibold hover:underline">{r.friend.name ?? "—"}</Link>
                    <span className="num block text-[13px] text-ink-mute">code {r.code} · friend +{r.friend_credits}, inviter +{r.inviter_credits}
                      {r.discount_pct ? ` · ${r.discount_pct}% off Plus${r.discount_used ? " (used)" : ""}` : ""}</span>
                  </span>
                  <Pill tone={STATUS[r.status]?.tone ?? "neutral"}>{STATUS[r.status]?.label ?? r.status}</Pill>
                  <span className="text-ink-mute" title={when(r.created_at)}>{ago(r.created_at)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </>
  );
}
