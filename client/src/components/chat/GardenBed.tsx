import type { CSSProperties } from "react";

import "./GardenBed.css";

/**
 * Where each plant sits along the track (fraction of its width) and the bloom
 * it opens into. The walker crosses the track left to right in the first 46%
 * of its 3.6s pace, so a plant at `x` sprouts just as Baxter passes it.
 */
const PLANTS: ReadonlyArray<{ x: number; bloom: string; tall: boolean }> = [
  { x: 0.08, bloom: "var(--aml)", tall: false },
  { x: 0.2, bloom: "var(--rdl)", tall: true },
  { x: 0.32, bloom: "var(--vil)", tall: false },
  { x: 0.45, bloom: "var(--bll)", tall: true },
  { x: 0.57, bloom: "var(--aml)", tall: false },
  { x: 0.69, bloom: "var(--rdl)", tall: true },
  { x: 0.81, bloom: "var(--vil)", tall: false },
  { x: 0.92, bloom: "var(--grl)", tall: true },
];

/** Seconds from the start of a pace until the walker reaches `x`. */
const PACE_SECONDS = 3.6;
const OUTWARD_SHARE = 0.46;

/**
 * A row of plants that sprout behind Baxter as he paces the loading track,
 * bloom, then wilt and regrow. Decorative: the track's `role="status"` parent
 * carries the words.
 */
export function GardenBed() {
  return (
    <div className="lg-garden-bed" aria-hidden>
      <div className="lg-garden-soil" />
      {PLANTS.map((plant) => (
        <svg
          key={plant.x}
          className={`lg-garden-plant${plant.tall ? " lg-garden-plant--tall" : ""}`}
          viewBox="0 0 20 28"
          style={
            {
              left: `${plant.x * 100}%`,
              "--lg-sprout-delay": `${(plant.x * OUTWARD_SHARE * PACE_SECONDS).toFixed(2)}s`,
              "--lg-bloom": plant.bloom,
            } as CSSProperties
          }
        >
          <g className="lg-garden-stem">
            <path d="M10 28 C10 22 9 16 10 10" />
            <ellipse className="lg-garden-leaf" cx="6.4" cy="19" rx="3.6" ry="1.6" transform="rotate(-28 6.4 19)" />
            <ellipse className="lg-garden-leaf" cx="13.6" cy="15" rx="3.6" ry="1.6" transform="rotate(28 13.6 15)" />
          </g>
          <g className="lg-garden-bloom">
            <circle cx="10" cy="5.2" r="2.4" />
            <circle cx="14" cy="8.2" r="2.4" />
            <circle cx="12.6" cy="12.6" r="2.4" />
            <circle cx="7.4" cy="12.6" r="2.4" />
            <circle cx="6" cy="8.2" r="2.4" />
            <circle className="lg-garden-heart" cx="10" cy="9.2" r="2" />
          </g>
        </svg>
      ))}
    </div>
  );
}
