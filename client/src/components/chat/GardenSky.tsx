/**
 * The few stars behind Baxter's garden (Mock 4). Static: nothing here moves,
 * so there is no motion to honour or withhold. Decoration only, hidden from
 * assistive tech; the field stretches with the frame (`preserveAspectRatio`
 * none), which a scatter of dots tolerates.
 */

/** Star positions in a 884 × 856 field, and each one's radius and brightness. */
const STARS: readonly [number, number, number, number][] = [
  [391, 164, 1.2, 0.6], [109, 84, 0.7, 0.45], [656, 69, 0.8, 0.3], [148, 454, 1.2, 0.3], [306, 102, 1.2, 0.3],
  [639, 136, 0.8, 0.6], [702, 606, 0.7, 0.6], [659, 416, 0.7, 0.3], [107, 580, 0.8, 0.45], [489, 157, 0.7, 0.6],
  [375, 583, 0.8, 0.3], [655, 594, 0.8, 0.45], [159, 570, 0.7, 0.6], [121, 643, 0.8, 0.45], [756, 554, 1.2, 0.45],
  [536, 609, 1.2, 0.45], [366, 264, 0.8, 0.6], [309, 93, 1, 0.6], [566, 361, 1.2, 0.45], [683, 84, 0.7, 0.6],
  [488, 178, 1, 0.3], [560, 441, 0.7, 0.6], [139, 792, 1, 0.45], [771, 368, 1.2, 0.6], [527, 80, 0.7, 0.45],
  [545, 723, 0.7, 0.3], [808, 728, 1, 0.6], [651, 707, 1.2, 0.45], [793, 405, 1, 0.3], [532, 373, 0.8, 0.6],
];

export function GardenSky() {
  return (
    <svg className="garden-sky" viewBox="0 0 884 856" preserveAspectRatio="none" aria-hidden focusable="false">
      {STARS.map(([cx, cy, r, opacity]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={r} opacity={opacity} />
      ))}
    </svg>
  );
}
