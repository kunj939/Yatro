"use client";

import { useEffect, useState } from "react";
import { guessLocation, litPath, moonState, type MoonState } from "@/lib/moon";

const R = 26, BOX = 150, C = BOX / 2;
const REFRESH_MS = 5 * 60 * 1000;

/** The moment the sky shows: now, or one given as ?moon=2026-10-06T07:00 (local time) to preview how the moon moves
 * without waiting for it to rise. */
function skyTime(): Date {
  const q = new URLSearchParams(window.location.search).get("moon");
  const t = q ? new Date(q) : null;
  return t && !Number.isNaN(t.valueOf()) ? t : new Date();
}

/** The real moon over the visitor's sky: today's phase, tilted the way they'd see it, and where it actually is —
 * hidden while it is below the horizon, just like outside.
 *
 * The page is the sky as seen facing the equator: east on the left in the northern hemisphere (on the right in the
 * south). So the moon rises in one side margin and sets in the other, higher on the page the higher it stands. It
 * keeps to the side margins so it never sits behind text, which is why it only shows from 1280 px wide. */
export function LiveMoon() {
  const [m, setM] = useState<(MoonState & { south: boolean }) | null>(null);

  useEffect(() => {
    const { lat, lng } = guessLocation();
    const update = () => setM({ ...moonState(skyTime(), lat, lng), south: lat < 0 });
    update();
    const t = setInterval(update, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  if (!m || m.altitude <= 0) return null;
  const east = m.azimuth < 180;
  const onLeft = m.south ? !east : east;
  const high = Math.min(1, m.altitude / 75); // from about 75° up it is as high as the page goes
  // centre of the side margin (between the page edge and the content), and from just under the navbar (centre at
  // 150 px) down to near the bottom of the window (centre 130 px above it)
  const side = "calc((100vw - 72rem) / 4 + 1rem - 75px)";
  const top = `calc(75px + ${(1 - high).toFixed(3)} * (100vh - 280px))`;

  return (
    <svg className="absolute hidden xl:block motion-safe:transition-[top] motion-safe:duration-1000" width={BOX} height={BOX}
         viewBox={`0 0 ${BOX} ${BOX}`} style={{ top, ...(onLeft ? { left: side } : { right: side }) }}>
      <defs>
        <radialGradient id="moon-halo">
          <stop offset="0%" stopColor="rgb(220 230 255)" stopOpacity="0.20" />
          <stop offset="45%" stopColor="rgb(150 170 230)" stopOpacity="0.06" />
          <stop offset="100%" stopColor="rgb(150 170 230)" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="moon-face" cx="40%" cy="38%">
          <stop offset="0%" stopColor="#fdfdf8" />
          <stop offset="100%" stopColor="#dfe3ee" />
        </radialGradient>
      </defs>
      {/* a thin crescent glows less than a full moon */}
      <circle cx={C} cy={C} r={C} fill="url(#moon-halo)" opacity={0.3 + 0.7 * m.fraction} />
      <g transform={`translate(${C} ${C}) rotate(${(-(m.brightLimb + 90)).toFixed(1)})`}>
        <circle r={R} fill="rgb(205 214 236)" opacity="0.08" />{/* the dark part, faintly lit by the Earth */}
        <path d={litPath(m.fraction, R)} fill="url(#moon-face)" />
      </g>
    </svg>
  );
}
