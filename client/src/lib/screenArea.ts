/**
 * Follow the page area (`.screen-area`) for chrome that positions itself
 * against it: the rail and the toast stack.
 *
 * The node is looked up on every measure, never captured. Anything that
 * remounts the area (a hot update to the topbar's provider does) leaves a
 * captured node detached, and a detached node measures zero: the rail then
 * drew its logo under the first group's heading, and toasts went past the
 * window's edge, until a reload.
 *
 * @returns a function that stops following.
 */
export function followScreenArea(onMeasure: (area: DOMRect) => void): () => void {
  let observed: Element | null = null;

  const measure = () => {
    const area = document.querySelector(".screen-area");
    if (area === null) return;
    if (area !== observed) {
      if (observed) observer?.unobserve(observed);
      observer?.observe(area);
      observed = area;
    }
    onMeasure(area.getBoundingClientRect());
  };
  // A removed node reports a size change too, which is what re-runs the lookup
  // when the area is remounted.
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
  measure();
  window.addEventListener("resize", measure);

  return () => {
    window.removeEventListener("resize", measure);
    observer?.disconnect();
  };
}
