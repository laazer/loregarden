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
 * nothing that *should* move. Starting, it leaves room for the node's
 * `scroll-margin-top` (a sticky heading above it); `onlyIfAbove` skips a node
 * whose top is already showing.
 */
export function scrollIntoScroller(
  node: HTMLElement,
  block: "start" | "end",
  { behavior = "auto", onlyIfAbove = false }: { behavior?: ScrollBehavior; onlyIfAbove?: boolean } = {},
): void {
  const scroller = scrollableAncestor(node);
  if (!scroller) return;
  const nodeRect = node.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  const margin = Number.parseFloat(getComputedStyle(node).scrollMarginTop) || 0;
  const delta =
    block === "start" ? nodeRect.top - scrollerRect.top - margin : nodeRect.bottom - scrollerRect.bottom;
  if (onlyIfAbove && delta >= 0) return;
  // `scrollTo?.` because jsdom implements no element scrolling.
  scroller.scrollTo?.({ top: scroller.scrollTop + delta, behavior });
}

/** Scroll `node`'s nearest scroller all the way down. */
export function scrollScrollerToBottom(node: HTMLElement, behavior: ScrollBehavior = "auto"): void {
  const scroller = scrollableAncestor(node);
  scroller?.scrollTo?.({ top: scroller.scrollHeight, behavior });
}
