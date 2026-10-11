"use client";

import { useEffect } from "react";

/** Feeds the pointer position to the card under it, for the .spotlight glow (globals.css). One listener for the whole
 * page rather than one per card; does nothing on touch screens, where there is no pointer to follow. */
export function SpotlightTracker() {
  useEffect(() => {
    if (window.matchMedia("(hover: none)").matches) return;
    const move = (e: PointerEvent) => {
      const card = (e.target as Element | null)?.closest?.(".spotlight") as HTMLElement | null;
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${Math.round(e.clientX - r.left)}px`);
      card.style.setProperty("--my", `${Math.round(e.clientY - r.top)}px`);
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => window.removeEventListener("pointermove", move);
  }, []);
  return null;
}
