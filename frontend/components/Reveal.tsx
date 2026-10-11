"use client";

import { useEffect, useRef, useState, type ElementType, type ReactNode } from "react";

/** Fades and lifts its children into place the first time they scroll into view. */
export function Reveal({ children, delay = 0, as: Tag = "div", className = "" }:
  { children: ReactNode; delay?: number; as?: ElementType; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  const seen = useInView(ref);
  return (
    <Tag ref={ref} className={`reveal ${seen ? "in" : ""} ${className}`} style={{ ["--rd" as string]: `${delay}ms` }}>
      {children}
    </Tag>
  );
}

/** True once the element has entered the viewport (and stays true). */
export function useInView(ref: React.RefObject<HTMLElement | null>, margin = "0px 0px -12% 0px") {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (!("IntersectionObserver" in window)) return setSeen(true);
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect(); } }, { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, margin, seen]);
  return seen;
}

/** Counts from the previous value to `to` over `ms`, so figures roll rather than jump. */
export function useCountUp(to: number, ms = 600, start = true) {
  const [v, setV] = useState(start ? to : 0);
  const from = useRef(start ? to : 0);
  useEffect(() => {
    if (!start) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { from.current = to; return setV(to); }
    const a = from.current, t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      const cur = a + (to - a) * e;
      setV(cur);
      from.current = cur;
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [to, ms, start]);
  return v;
}
