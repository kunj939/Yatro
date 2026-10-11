"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { admin } from "@/lib/admin";
import { Card, ErrorBox, Loading, PageHeader, Pager, Pill, RefreshButton, SearchBox, Select, ago, useDebounced, useLoad } from "@/components/admin/ui";
import { money } from "@/lib/format";

export default function AdminTrips() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, status]);
  const { data, error, busy, reload } = useLoad(() => admin.trips({ q: dq, status, page, page_size: 20 }), [dq, status, page]);

  return (
    <>
      <PageHeader title="Trips" text="Every trip anyone has made. Open one to see the plan or remove it." actions={<RefreshButton onClick={reload} busy={busy} />} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchBox value={q} onChange={setQ} placeholder="Search title, place or owner email" />
        <Select label="Status" value={status} onChange={setStatus} options={[["", "Any status"], ["draft", "Draft"], ["planning", "Planning"], ["ready", "Ready"], ["active", "Active"], ["completed", "Completed"]]} />
      </div>
      {error && <ErrorBox text={error} retry={reload} />}
      {!data && !error && <Loading />}
      {data && (
        <Card className="!p-0 overflow-hidden">
          <div className="hidden grid-cols-[minmax(0,2fr)_minmax(0,1.4fr)_minmax(0,1fr)_60px_100px_24px] gap-3 border-b border-line bg-haze/60 px-5 py-3 text-[12px] font-semibold uppercase tracking-[0.12em] text-ink-mute md:grid">
            <span>Trip</span><span>Owner</span><span>Budget</span><span>Plans</span><span>Created</span><span />
          </div>
          {data.items.length === 0 && <p className="px-5 py-10 text-center text-ink-mute">No trips match.</p>}
          <ul className="divide-y divide-line">
            {data.items.map((t) => (
              <li key={t.id}>
                <Link href={`/admin/trips/${t.id}`} className="grid items-center gap-x-3 gap-y-1 px-5 py-3.5 transition-colors hover:bg-haze/70 md:grid-cols-[minmax(0,2fr)_minmax(0,1.4fr)_minmax(0,1fr)_60px_100px_24px]">
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-1.5"><span className="truncate font-semibold">{t.title}</span><Pill tone={t.status === "ready" ? "good" : "neutral"}>{t.status}</Pill></span>
                    <span className="block truncate text-[13px] text-ink-mute">{t.origin} → {t.destination} · {t.start_date}</span>
                  </span>
                  <span className="min-w-0 truncate text-[14px]">{t.owner.name}<span className="block truncate text-[13px] text-ink-mute">{t.owner.email}</span></span>
                  <span className="num text-[14px]">{money(t.budget, t.currency)}</span>
                  <span className="num text-[14px]">{t.plan_count}</span>
                  <span className="text-[13px] text-ink-mute">{ago(t.created_at)}</span>
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
