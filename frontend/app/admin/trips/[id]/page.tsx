"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Trash2 } from "lucide-react";
import { admin } from "@/lib/admin";
import { Card, ConfirmDialog, ErrorBox, Loading, Pill, ago, useLoad, when } from "@/components/admin/ui";
import { ageSummary, money, plural } from "@/lib/format";

export default function AdminTripPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: d, error, reload } = useLoad(() => admin.trip(id), [id]);
  const [del, setDel] = useState(false);

  if (error) return <><Link href="/admin/trips" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft hover:text-ink"><ArrowLeft className="h-4 w-4" />Trips</Link><ErrorBox text={error} retry={reload} /></>;
  if (!d) return <Loading />;
  const t = d.trip, it = t.itinerary;
  const ages = ageSummary(t.preferences?.age_groups);

  return (
    <>
      <Link href="/admin/trips" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft hover:text-ink"><ArrowLeft className="h-4 w-4" aria-hidden />Trips</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-[28px] font-extrabold leading-tight tracking-tight md:text-[34px]"><span className="min-w-0 break-words">{d.title}</span><Pill tone={d.status === "ready" ? "good" : "neutral"}>{d.status}</Pill></h1>
          <p className="mt-1 text-ink-soft">{d.origin} → {d.destination} · {d.start_date}{d.end_date !== d.start_date ? ` – ${d.end_date}` : ""} · {plural(d.travellers, "traveller")}</p>
          <p className="mt-1 text-[14px] text-ink-mute">Owner: <Link className="font-semibold text-signal-deep underline-offset-4 hover:underline" href={`/admin/users/${d.owner.id}`}>{d.owner.name}</Link> · {d.owner.email} · made {ago(d.created_at)}</p>
        </div>
        <button className="btn bg-danger text-white hover:brightness-110" onClick={() => setDel(true)}><Trash2 className="h-4 w-4" aria-hidden />Delete trip</button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {([["Budget", money(d.budget, d.currency)], ["Planned cost", d.estimated_cost != null ? money(d.estimated_cost, d.currency) : "—"], ["Plans / re-plans", `${d.plan_count} / ${d.replan_count}`], ["Data", d.data_mode]] as const).map(([k, v]) => (
          <div key={k} className="panel p-4"><p className="text-[13px] text-ink-mute">{k}</p><p className="num mt-1 truncate text-xl font-extrabold">{v}</p></div>
        ))}
      </div>
      {(ages || t.preferences) && (
        <p className="mt-4 flex flex-wrap gap-2 text-[13px]">
          {ages && <Pill tone="brand">Ages: {ages}</Pill>}
          <Pill>{t.preferences.traveller_type}</Pill><Pill>{t.preferences.food}</Pill><Pill>{t.preferences.stay}</Pill><Pill>pace {t.preferences.pace}</Pill>
          {t.preferences.trip_styles.map((s) => <Pill key={s}>{s}</Pill>)}
        </p>
      )}

      <Card title={it ? it.title : "Plan"} className="mt-6">
        {!it ? <p className="text-ink-mute">This trip hasn&apos;t been planned yet{d.feasible === false ? " (it was found not feasible)" : ""}.</p> : (
          <div className="space-y-6">
            {it.summary && <p className="text-[15px] text-ink-soft">{it.summary}</p>}
            {it.days.map((day) => (
              <div key={day.index}>
                <h3 className="mb-2 text-[15px] font-bold">{day.label} <span className="font-normal text-ink-mute">· {day.date}</span></h3>
                <ul className="divide-y divide-line rounded-xl border border-line">
                  {day.items.map((i) => (
                    <li key={i.id} className="grid grid-cols-[56px_minmax(0,1fr)_auto] items-baseline gap-3 px-3.5 py-2.5 text-[14px]">
                      <span className="num font-semibold text-signal-deep">{i.time ?? "—"}</span>
                      <span className="min-w-0"><span className="block truncate font-medium">{i.title}</span>{i.subtitle && <span className="block truncate text-[13px] text-ink-mute">{i.subtitle}</span>}</span>
                      <span className="num text-ink-soft">{i.cost ? money(i.cost, d.currency) : ""}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <p className="text-[13px] text-ink-mute">Version {it.version} · {when(it.created_at)} · {it.llm_status === "ai" ? "AI description" : "template description"}</p>
          </div>
        )}
      </Card>

      <ConfirmDialog open={del} title="Delete this trip?" confirmLabel="Delete trip" danger onClose={() => setDel(false)}
                     onConfirm={async (reason) => { await admin.deleteTrip(id, reason); router.replace("/admin/trips"); }}>
        &ldquo;{d.title}&rdquo; and its plans, group and expenses are removed for good. The owner keeps their account.
      </ConfirmDialog>
    </>
  );
}
