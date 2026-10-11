/** The page sits on one colour; this is the only thing behind it.
 *
 *  A ground plane laid in real CSS 3-D (rotateX on a perspective parent) runs away toward a horizon and keeps
 *  travelling, so there is always something happening back there. Above it a flat route network drifts at its
 *  own pace, with services running along the lines — the product's own subject matter rather than decorative
 *  blobs. Everything is far enough back that text keeps its contrast, and all of it holds still under
 *  prefers-reduced-motion. */
const LINES = [
  { d: "M-60 200 L240 200 L380 340 L720 340 L860 200 L1500 200", w: 2.2, o: 0.42 },
  { d: "M-60 560 L200 560 L340 420 L640 420 L780 560 L1060 560 L1200 700 L1500 700", w: 1.6, o: 0.3 },
  { d: "M140 -60 L140 260 L280 400 L280 960", w: 1.4, o: 0.22 },
  { d: "M1140 -60 L1140 280 L1000 420 L1000 960", w: 1.4, o: 0.22 },
];
const NODES = [[240, 200], [720, 340], [340, 420], [780, 560], [1200, 700], [280, 400], [1000, 420]];

export function NetworkBackdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden="true">
      {/* the ground plane, actually tilted in 3-D and running toward the horizon */}
      <div className="nb-stage absolute inset-x-0 bottom-0 h-[64vh]">
        <div className="nb-floor" />
      </div>

      {/* the network above it */}
      <svg viewBox="0 0 1440 900" className="h-full w-full opacity-[0.6] dark:opacity-100"
           preserveAspectRatio="xMidYMid slice">
        <g className="nb-drift">
          {LINES.map((l) => (
            <path key={l.d} d={l.d} fill="none" strokeLinecap="round" strokeLinejoin="round"
                  className="stroke-signal" strokeWidth={l.w} opacity={l.o} />
          ))}
          {NODES.map(([x, y], i) => (
            <circle key={`${x}-${y}`} cx={x} cy={y} r="5" className="nb-node fill-signal"
                    style={{ ["--d" as string]: `${(i % 4) * 1.6}s` }} />
          ))}
          {/* services running the two main corridors */}
          {LINES.slice(0, 2).map((l, i) => (
            <circle key={`svc-${l.d}`} r="4.5" className="nb-service fill-signal"
                    style={{ ["--path" as string]: `path("${l.d}")`, ["--dur" as string]: `${22 + i * 9}s`,
                             ["--delay" as string]: `${i * 5}s` }} />
          ))}
        </g>
      </svg>
    </div>
  );
}