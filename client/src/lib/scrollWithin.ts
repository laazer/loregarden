/**
 * Scrolling that stays inside the panel it belongs to.
 *
 * `Element.scrollIntoView` scrolls *every* ancestor, `overflow: hidden` ones
 * included. The app frame is one of those, so once a panel's own scroller
 * bottomed out the browser kept going and dragged the whole app up off the top
 * of the window, leaving a blank band beneath it. These scroll only the nearest
 * ancestor that is meant to scroll.
 */

/** The nearest ancestor that actually scrolls, or null when nothing does. */
export function scrollableAncestor(node: HTMLElement): HTMLElement | null {
  let current: HTMLElement | null = node.parentElement;
  while (current) {
    const overflowY = getComputedStyle(current).overflowY;
    if (overflowY === "auto" || overflowY === "scroll" || overflowY === "overlay") {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}

/**
 * Bring `node`'s top ("start") or bottom ("end") to the matching edge of its
 * nearest scroller. Does nothing when no ancestor scrolls — there is then
 * nothing that *should* move.
 */
export function scrollIntoScroller(
  node: HTMLElement,
  block: "start" | "end",
  behavior: ScrollBehavior = "auto",
): void {
  const scroller = scrollableAncestor(node);
  if (!scroller) return;
  const nodeRect = node.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  const delta = block === "start" ? nodeRect.top - scrollerRect.top : nodeRect.bottom - scrollerRect.bottom;
  // `scrollTo?.` because jsdom implements no element scrolling.
  scroller.scrollTo?.({ top: scroller.scrollTop + delta, behavior });
}

/** Scroll `node`'s nearest scroller all the way down. */
export function scrollScrollerToBottom(node: HTMLElement, behavior: ScrollBehavior = "auto"): void {
  const scroller = scrollableAncestor(node);
  scroller?.scrollTo?.({ top: scroller.scrollHeight, behavior });
}
