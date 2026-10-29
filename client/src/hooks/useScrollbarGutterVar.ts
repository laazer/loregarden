import { useLayoutEffect, type RefObject } from "react";

/**
 * Publish the width of one scroller's scrollbar gutter (per side) as a CSS
 * variable on another element, so siblings can line up with the scroller's
 * content edge.
 *
 * The chat thread reserves its gutter on both edges (`scrollbar-gutter: stable
 * both-edges`), and its width is the platform's: 0 with overlay scrollbars,
 * about 11px with classic ones. The garden's vines and composer sit on the
 * thread's column edge, which moves by exactly that much; CSS cannot read it.
 */
export function useScrollbarGutterVar(
  scrollerRef: RefObject<HTMLElement | null>,
  targetRef: RefObject<HTMLElement | null>,
  name: string,
): void {
  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    const target = targetRef.current;
    if (!scroller || !target) return;
    const publish = () => {
      // Both edges reserve the same gutter, so each side is half the difference.
      const perSide = Math.max(0, (scroller.offsetWidth - scroller.clientWidth) / 2);
      target.style.setProperty(name, `${perSide}px`);
    };
    publish();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(publish);
    observer.observe(scroller);
    return () => {
      observer.disconnect();
      target.style.removeProperty(name);
    };
  });
}
