import { stemGrown, stemPath, vineSide, type VineMark } from "../../lib/vine";

import "./Vine.css";

/** The shape at a node, drawn in a 14 × 14 box pointing right; `left` mirrors it. */
function MarkShape({ mark }: { mark: VineMark }) {
  switch (mark) {
    case "leaf":
      return (
        <>
          <path className="vine-leaf" d="M2 7 C 4 1.5, 10.5 1.5, 13 7 C 10.5 12.5, 4 12.5, 2 7 Z" />
          <path className="vine-leaf-rib" d="M3 7 H 11" />
        </>
      );
    case "pruned":
      return <path className="vine-pruned" d="M7 2 V 8 M 4 10.5 L 10 7.5" />;
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
