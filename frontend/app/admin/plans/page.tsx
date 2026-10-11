"use client";

import Link from "next/link";
import { useState } from "react";
import { admin } from "@/lib/admin";
import { Card, ErrorBox, Loading, PageHeader, Pill, RefreshButton, ago, useLoad, when } from "@/components/admin/ui";

const STATES = [["", "All"], ["running", "Running"], ["pending", "Waiting"], ["done", "Done"], ["failed", "Failed"], ["refunded", "Refunded"]] as const;
const tone = (s: string) => (s === "done" ? "good" : s === "failed" || s === "refunded" ? "bad" : s === "running" ? "brand" : "neutral") as "good" | "bad" | "brand" | "neutral";

export default function AdminPlans() {
  const [state, setState] = useState("");
  const { data, error, busy, reload } = useLoad(() => admin.plans(state || undefined), [state]);
  return (
    <>
      <PageHeader title="Plan runs" text="The last 50 times the planner ran. Refunded means the person got their free plan back." actions={<RefreshButton onClick={reload} busy={busy} />} />
      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filter by state">
        {STATES.map(([v, l]) => <button key={v} type="button" aria-pressed={state === v} onClick={() => setState(v)} className={state === v ? "chip-on" : "chip-off"}>{l}</button>)}
      </div>
      {error && <ErrorBox text={error} retry={reload} />}
      {!data && !error && <Loading />}
      {data && (
        <Card className="!p-0 overflow-hidden">
          {data.items.length === 0 && <p className="px-5 py-10 text-center text-ink-mute">No plan runs{state ? ` in “${state}”` : ""}.</p>}
          <ul className="divide-y divide-line">
            {data.items.map((j) => (
              <li key={j.id} className="grid items-center gap-x-3 gap-y-1 px-5 py-3 text-[14px] md:grid-cols-[110px_minmax(0,1.6fr)_90px_90px_130px]">
                <span><Pill tone={tone(j.state)}>{j.state}</Pill></span>
                <span className="min-w-0 truncate">{j.user ?? "—"}<span className="ml-2 text-ink-mute">{j.kind}{j.result ? ` · ${j.result}` : ""}{j.error ? ` · ${j.error}` : ""}</span></span>
                <span className="num text-ink-soft">{j.seconds ? `${j.seconds} s` : "—"}</span>
                <span>{j.late_finish && <Pill tone="warn">late</Pill>}</span>
                <span className="text-ink-mute" title={when(j.created_at)}>{ago(j.created_at)} · <Link className="font-semibold text-signal-deep hover:underline" href={`/admin/trips/${j.trip_id}`}>trip</Link></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
