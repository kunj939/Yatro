/** A quiet transit diagram for the sign-in page: three lines meeting at an interchange, drawn once and still.
 *  Decoration, not data — so it carries no verification colours. */
export function TransitScene() {
  return (
    <svg viewBox="0 0 400 560" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <rect width="400" height="560" fill="#101B36" />
      <g fill="none" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M60 90 L60 300 L200 440 L340 440" stroke="#1B4DFF" />
        <path d="M340 110 L200 110 L60 250" stroke="#2A3A61" />
        <path d="M120 500 L120 360 L260 220 L340 220" stroke="#2A3A61" />
      </g>
      {[[60, 90], [60, 300], [200, 440], [340, 440], [340, 110], [200, 110], [120, 500], [260, 220]].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="6" fill="#101B36" stroke="#DCE3EF" strokeWidth="3" />
      ))}
      <circle cx="60" cy="250" r="9" fill="#1B4DFF" stroke="#101B36" strokeWidth="4" />
    </svg>
  );
}