"use client";

import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { admin } from "@/lib/admin";
import { Card, ErrorBox, Loading, PageHeader, Pager, Pill, RefreshButton, SearchBox, Select, ago, useDebounced, useLoad, when } from "@/components/admin/ui";

const show = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "boolean" ? (v ? "yes" : "no") : String(v));

export default function AdminAudit() {
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, action]);
  const { data, error, busy, reload } = useLoad(() => admin.audit({ q: dq, action, page, page_size: 30 }), [dq, action, page]);

  return (
    <>
      <PageHeader title="Audit log" text="Everything changed from this console: who did it, to what, and why. Entries can't be edited or removed." actions={<RefreshButton onClick={reload} busy={busy} />} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchBox value={q} onChange={setQ} placeholder="Search by admin or what was changed" />
        <Select label="Kind" value={action} onChange={setAction} options={[["", "Everything"], ["user", "Accounts"], ["trip", "Trips"], ["control", "Controls"]]} />
      </div>
      {error && <ErrorBox text={error} retry={reload} />}
      {!data && !error && <Loading />}
      {data && (
        <Card className="!p-0 overflow-hidden">
          {data.items.length === 0 && <p className="px-5 py-10 text-center text-ink-mute">Nothing logged yet.</p>}
          <ul className="divide-y divide-line">
            {data.items.map((a) => (
              <li key={a.id} className="px-5 py-3.5 text-[14px]">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Pill tone={a.action.endsWith("delete") ? "bad" : "brand"}>{a.action}</Pill>
                  <span className="min-w-[11rem] flex-1 truncate"><b className="font-semibold">{a.actor}</b> → {a.target_label ?? a.target_id ?? "—"}</span>
                  <span className="text-ink-mute" title={when(a.at)}>{ago(a.at)}</span>
                </div>
                {a.detail && (
                  <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-ink-soft">
                    {Object.entries(a.detail).map(([k, v]) => Array.isArray(v) && v.length === 2
                      ? <span key={k} className="inline-flex flex-wrap items-center gap-1"><span className="text-ink-mute">{k}:</span>{show(v[0])}<ArrowRight className="h-3 w-3 text-ink-mute" aria-hidden />{show(v[1])}</span>
                      : <span key={k}><span className="text-ink-mute">{k}:</span> {show(v)}</span>)}
                  </p>
                )}
                {a.reason && <p className="mt-1 text-[13px] italic text-ink-mute">“{a.reason}”</p>}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {data && <Pager page={page} size={data.page_size} total={data.total} onPage={setPage} />}
    </>
  );
}
