import { brand } from "@/lib/brand";

/** Two routes converging into one — which is both a junction and the Y of Yatro. */
export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden="true">
      <g fill="none" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 8v6l11 10 11-10V8" className="stroke-signal" />
        <path d="M20 24v8" className="stroke-ink" />
      </g>
      <circle cx="9" cy="7" r="3.4" className="fill-signal" />
      <circle cx="31" cy="7" r="3.4" className="fill-signal" />
      <circle cx="20" cy="33" r="3.4" className="fill-ink" />
    </svg>
  );
}

export function Logo({ light = false }: { light?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${light ? "text-white" : "text-ink"}`}>
      <LogoMark className={`h-7 w-7 transition-transform duration-500 ease-out group-hover:-rotate-12 group-hover:scale-110 ${
        light ? "[&_.stroke-ink]:stroke-white [&_.fill-ink]:fill-white" : ""}`} />
      <span className="wordmark pr-0.5 text-[30px] leading-none">{brand.name}</span>
    </span>
  );
}
