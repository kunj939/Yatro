"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { admin } from "@/lib/admin";
import { UserBadges } from "@/components/admin/UserBadges";
import { Card, ErrorBox, Loading, PageHeader, Pager, Pill, RefreshButton, SearchBox, Select, ago, useDebounced, useLoad } from "@/components/admin/ui";

export default function AdminUsers() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [plan, setPlan] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, status, plan, sort]);
  const { data, error, busy, reload } = useLoad(
    () => admin.users({ q: dq, status, plan, sort, page, page_size: 20 }), [dq, status, plan, sort, page]);

  return (
    <>
      <PageHeader title="Users" text="Everyone with an account. Open one to change their plan, suspend them or fix their free plans."
                  actions={<RefreshButton onClick={reload} busy={busy} />} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchBox value={q} onChange={setQ} placeholder="Search name, email or mobile" />
        <Select label="Status" value={status} onChange={setStatus} options={[["", "All accounts"], ["active", "Active"], ["suspended", "Suspended"], ["admins", "Admins"], ["unverified", "Unverified"]]} />
        <Select label="Plan" value={plan} onChange={setPlan} options={[["", "Any plan"], ["free", "Free"], ["plus", "Plus"]]} />
        <Select label="Sort" value={sort} onChange={setSort} options={[["newest", "Newest first"], ["oldest", "Oldest first"], ["name", "Name A–Z"], ["trips", "Most trips"]]} />
      </div>
      {error && <ErrorBox text={error} retry={reload} />}
      {!data && !error && <Loading />}
      {data && (
        <Card className="!p-0 overflow-hidden">
          <div className="hidden grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)_minmax(0,1fr)_70px_110px_24px] gap-3 border-b border-line bg-haze/60 px-5 py-3 text-[12px] font-semibold uppercase tracking-[0.12em] text-ink-mute md:grid">
            <span>User</span><span>Plan</span><span>Free plans left</span><span>Trips</span><span>Joined</span><span />
          </div>
          {data.items.length === 0 && <p className="px-5 py-10 text-center text-ink-mute">No one matches.</p>}
          <ul className="divide-y divide-line">
            {data.items.map((u) => (
              <li key={u.id}>
                <Link href={`/admin/users/${u.id}`} className="grid items-center gap-x-3 gap-y-1.5 px-5 py-3.5 transition-colors hover:bg-haze/70 md:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)_minmax(0,1fr)_70px_110px_24px]">
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-1.5"><span className="truncate font-semibold">{u.name}</span><UserBadges u={u} /></span>
                    <span className="block truncate text-[13px] text-ink-mute">{u.email}{u.phone ? ` · ${u.phone}` : ""}</span>
                  </span>
                  <span className="text-[14px]">{u.plan === "plus" ? <Pill tone="good">Plus</Pill> : <Pill>Free</Pill>}{u.trip_credits > 0 && <span className="ml-1.5 text-ink-mute">+{u.trip_credits} credit{u.trip_credits > 1 ? "s" : ""}</span>}</span>
                  <span className="num text-[14px]"><span className="text-ink-mute md:hidden">Free plans left: </span>{u.free_plans_left}</span>
                  <span className="num text-[14px]"><span className="text-ink-mute md:hidden">Trips: </span>{u.trips}</span>
                  <span className="text-[13px] text-ink-mute">{ago(u.created_at)}</span>
                  <ChevronRight className="hidden h-4 w-4 text-ink-mute md:block" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {data && <Pager page={page} size={data.page_size} total={data.total} onPage={setPage} />}
    </>
  );
}
