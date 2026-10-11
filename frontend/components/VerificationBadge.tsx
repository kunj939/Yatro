import { BadgeCheck, CircleDashed, CircleHelp, FlaskConical, ShieldCheck, TriangleAlert } from "lucide-react";

const MAP: Record<string, { label: string; cls: string; Icon: any; hint: string }> = {
  verified_official: { label: "Official", cls: "bg-teal text-white", Icon: ShieldCheck, hint: "Confirmed by an official source" },
  verified_provider: { label: "Verified", cls: "bg-teal-soft text-teal-deep", Icon: BadgeCheck, hint: "From a structured data provider" },
  estimated: { label: "Estimated", cls: "bg-haze-2 text-ink-soft", Icon: CircleDashed, hint: "Calculated estimate; confirm before booking" },
  uncertain: { label: "Needs check", cls: "bg-caution-soft text-caution", Icon: TriangleAlert, hint: "Sources disagree; please confirm" },
  unavailable: { label: "Not available", cls: "bg-haze-2 text-ink-mute", Icon: CircleHelp, hint: "No reliable information found" },
  demo: { label: "Sample data", cls: "bg-demo-soft text-demo", Icon: FlaskConical, hint: "Illustrative demo data" },
};

/** `label` replaces the text only (e.g. "Estimated fare"); the colour and icon always come from `state`, so an estimate
 *  can never look verified (I1, ADR 0004). */
export function VerificationBadge({ state, compact = false, label: text }: { state: string | null | undefined; compact?: boolean; label?: string }) {
  if (!state || !MAP[state]) return null;
  const { cls, Icon, hint } = MAP[state];
  const label = text || MAP[state].label;
  return (
    <span title={hint} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] leading-snug font-semibold ${cls}`}>
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {compact ? <span className="sr-only">{label}</span> : label}
    </span>
  );
}

export const VERIFICATION_LEGEND = Object.entries(MAP).map(([k, v]) => ({ key: k, label: v.label, hint: v.hint }));
