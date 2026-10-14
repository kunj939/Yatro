/** The page sits on one colour; this is the only thing behind it — a transit network that drifts very slowly,
 *  like a map being panned. Grounded in what Yaitro does rather than a decorative gradient, and kept far enough
 *  back that text never competes with it. Fixed, so it stays put while the page scrolls past. */
const LINES = [
  { d: "M-40 180 L220 180 L360 320 L700 320 L840 180 L1180 180 L1320 320 L1560 320", w: 2.2, o: 0.5, dur: "48s" },
  { d: "M-40 520 L180 520 L320 380 L620 380 L760 520 L1040 520 L1180 660 L1560 660", w: 1.6, o: 0.33, dur: "66s" },
  { d: "M-40 760 L300 760 L440 620 L880 620 L1020 760 L1560 760", w: 1.6, o: 0.27, dur: "84s" },
  { d: "M120 -40 L120 240 L260 380 L260 900", w: 1.4, o: 0.22, dur: "72s" },
  { d: "M1120 -40 L1120 260 L980 400 L980 900", w: 1.4, o: 0.22, dur: "58s" },
];
const NODES = [
  [220, 180], [700, 320], [1180, 180], [320, 380], [760, 520], [1180, 660],
  [440, 620], [1020, 760], [260, 380], [980, 400],
];

export function RouteBackdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden="true">
      <svg viewBox="0 0 1440 900" className="h-full w-full opacity-[0.55] dark:opacity-100"
           preserveAspectRatio="xMidYMid slice">
        <g className="rb-drift">
          {LINES.map((l) => (
            <path key={l.d} d={l.d} fill="none" strokeLinecap="round" strokeLinejoin="round"
                  className="stroke-signal" strokeWidth={l.w} opacity={l.o}
                  style={{ ["--dur" as string]: l.dur }} />
          ))}
          {NODES.map(([x, y], i) => (
            <circle key={`${x}-${y}`} cx={x} cy={y} r="5" className="rb-node fill-signal"
                    style={{ ["--d" as string]: `${(i % 5) * 1.4}s` }} />
          ))}
        </g>
      </svg>
    </div>
  );
}