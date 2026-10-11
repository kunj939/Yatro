import { LiveMoon } from "./LiveMoon";

/** The dark theme's sky: a fixed field of stars, the real moon (see LiveMoon) and, now and then, a shooting
 * star. Purely decorative and behind everything (pointer-events off, aria-hidden). Shown only in dark mode; motion
 * is switched off for people who ask for reduced motion (see globals.css).
 *
 * Stars come from a seeded generator, so the server and the browser draw the same sky (no hydration mismatch) and
 * it never jumps between pages. */

function seeded(seed: number) {
  let s = seed;
  return () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; };
}

type Star = { x: number; y: number; r: number; o: number; twinkle: number | null };

const STARS: Star[] = (() => {
  const rnd = seeded(20261005);
  return Array.from({ length: 170 }, (_, i) => {
    const big = rnd() > 0.93;
    return {
      x: Math.round(rnd() * 1600 * 10) / 10,
      // denser towards the top, like a real night sky over a horizon glow
      y: Math.round(Math.pow(rnd(), 1.5) * 1000 * 10) / 10,
      r: big ? 1.4 + rnd() * 0.8 : 0.5 + rnd() * 0.7,
      o: big ? 0.85 : 0.25 + rnd() * 0.5,
      twinkle: i % 6 === 0 ? Math.round(rnd() * 60) / 10 : null,
    };
  });
})();

export function NightSky() {
  return (
    <div className="night-sky pointer-events-none fixed inset-0 -z-10 hidden overflow-hidden dark:block" aria-hidden>
      <svg className="h-full w-full" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">
        {STARS.map((s, i) => (
          <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#eef2ff" opacity={s.o}
                  className={s.twinkle !== null ? "star-twinkle" : undefined}
                  style={s.twinkle !== null ? { animationDelay: `${s.twinkle}s` } : undefined} />
        ))}
      </svg>

      {/* The real moon: today's phase, its real tilt and where it actually is in the visitor's sky (LiveMoon.tsx). */}
      <LiveMoon />
      <span className="shooting-star" />
    </div>
  );
}
