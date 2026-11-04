import { stemGrown, stemPath, vineSide, type VineMark } from "../../lib/vine";

import "./Vine.css";

/** The shape at a node, drawn in a 14 × 14 box pointing right; `left` mirrors it. */
function MarkShape({ mark }: { mark: VineMark }) {
  switch (mark) {
    case "leaf":
      return (
        <>
          {/* A stem, a round base, a tip lifted to a point: a leaf, not the
              pea pod a lens pointed at both ends reads as. */}
          <path className="vine-leaf-stem" d="M0.8 7.1 H 3.2" />
          <path className="vine-leaf" d="M3 7 C 3 4.5, 4.6 3.1, 6.4 3.1 C 9 3.1, 11.5 4.9, 13.6 6.2 C 11.5 8.2, 9 10.7, 6.4 10.8 C 4.6 10.9, 3 9.5, 3 7 Z" />
          <path className="vine-leaf-rib" d="M3.6 7 Q 8.2 6.55, 12.2 6.35" />
        </>
      );
    case "pruned":
      return <path className="vine-pruned" d="M7 2 V 8 M 4 10.5 L 10 7.5" />;
    case "fork":
      return <path className="vine-fork" d="M7 1.5 V 12.5 M 7 7 C 9 7, 11 5.5, 12.5 3" />;
    default:
      // A bud: closed until it opens; the fill says which kind of wait it is.
      return <path className={`vine-bud vine-bud--${mark}`} d="M7 1.5 C 10.5 4.5, 11.5 9, 7 12.5 C 2.5 9, 3.5 4.5, 7 1.5 Z" />;
  }
}

/**
 * One stage's node on the vine, and the stem down to the next.
 *
 * Decoration only: hidden from screen readers, because the stage's name and
 * status pill beside it already say what it shows. Its shape comes from its
 * own index and state, never from how many stages follow (see `lib/vine`).
 */
export function VineNode({ index, mark, last }: { index: number; mark: VineMark; last: boolean }) {
  const side = vineSide(index);
  return (
    <div className={`vine-node vine-node--${side}`} data-mark={mark} aria-hidden>
      {last ? null : (
        <svg className={`vine-stem${stemGrown(mark) ? " is-grown" : ""}`} viewBox="0 0 14 100" preserveAspectRatio="none">
          <path d={stemPath(index)} vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      <svg className="vine-mark" viewBox="0 0 14 14" width="14" height="14">
        <MarkShape mark={mark} />
      </svg>
    </div>
  );
}
