import { brand } from "@/lib/brand";
import { MARK, WORDMARK } from "@/lib/logo";

/** A lowercase "y" drawn as a route that ends at a destination dot. `lime` is the version for dark backgrounds. */
export function LogoMark({ className = "h-8 w-8", lime = false }: { className?: string; lime?: boolean }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="16" fill={lime ? "#beff4a" : "#101311"} />
      <path d={MARK.route} fill="none" stroke={lime ? "#101311" : "#ffffff"} strokeWidth={MARK.stroke} strokeLinecap="round" />
      <circle {...MARK.dot} fill={lime ? "#101311" : "#beff4a"} />
    </svg>
  );
}

/** "yaitro", outlined from Inter Tight ExtraBold; the i-dot is the mark's destination dot. Takes the text colour. */
export function Wordmark({ className = "h-[27px]", light = false }: { className?: string; light?: boolean }) {
  return (
    <svg viewBox={WORDMARK.viewBox} className={`w-auto ${className}`} style={{ aspectRatio: WORDMARK.ratio }}
         role="img" aria-label={brand.name}>
      <path d={WORDMARK.d} fill="currentColor" />
      <circle {...WORDMARK.dot} className={light ? "fill-[#beff4a]" : "fill-signal"} />
    </svg>
  );
}

export function Logo({ light = false }: { light?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${light ? "text-white" : "text-ink"}`}>
      <LogoMark lime={light} className="h-8 w-8 shrink-0 transition-transform duration-500 ease-out group-hover:-rotate-6 group-hover:scale-105" />
      <Wordmark light={light} />
    </span>
  );
}
