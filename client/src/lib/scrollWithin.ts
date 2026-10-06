/**
 * Scrolling that stays inside the panel it belongs to.
 *
 * `Element.scrollIntoView` scrolls *every* ancestor, `overflow: hidden` ones
 * included. The app frame was one of those, so once a panel's own scroller
 * bottomed out the browser kept going and dragged the whole app up off the top
 * of the window, leaving a blank band beneath it. The frame is `overflow: clip`
 * now and cannot scroll, but other clipped boxes (the sidebar panel, card
 * bodies) still can. These scroll only the nearest ancestor meant to scroll.
 */

/** Which way a scroller scrolls: `y` is the block axis, `x` the inline one. */
export type ScrollAxis = "x" | "y";

/**
 * Where the node lands in its scroller, as `scrollIntoView`'s `block`/`inline`
 * mean it. `"nearest"` moves only as far as it must, and not at all when the
 * node is already in view.
 */
export type ScrollAlign = "start" | "end" | "center" | "nearest";

/** The nearest ancestor that actually scrolls on `axis`, or null when nothing does. */
export function scrollableAncestor(node: HTMLElement, axis: ScrollAxis = "y"): HTMLElement | null {
  let current: HTMLElement | null = node.parentElement;
  while (current) {
    const style = getComputedStyle(current);
    const overflow = axis === "y" ? style.overflowY : style.overflowX;
    if (overflow === "auto" || overflow === "scroll" || overflow === "overlay") {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}

/**
 * How far the scroller must move so the span `[start, end]` sits at `align`
 * within `[viewStart, viewEnd]`. For `"nearest"` this is `scrollIntoView`'s
 * rule: nothing when the node is in view or already covers the view, else the
 * edge it is off past — or, when it is larger than the view, the other edge,
 * so as much of it shows as can.
 */
function alignDelta(start: number, end: number, viewStart: number, viewEnd: number, align: ScrollAlign): number {
  switch (align) {
    case "start":
      return start - viewStart;
    case "end":
      return end - viewEnd;
    case "center":
      return (start + end) / 2 - (viewStart + viewEnd) / 2;
    case "nearest": {
      const fits = end - start <= viewEnd - viewStart;
      if (start < viewStart && end <= viewEnd) return fits ? start - viewStart : end - viewEnd;
      if (end > viewEnd && start >= viewStart) return fits ? end - viewEnd : start - viewStart;
      return 0;
    }
  }
}

function scrollOnAxis(node: HTMLElement, axis: ScrollAxis, align: ScrollAlign, behavior: ScrollBehavior): void {
  const scroller = scrollableAncestor(node, axis);
  if (!scroller) return;
  const nodeRect = node.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  const delta =
    axis === "y"
      ? alignDelta(nodeRect.top, nodeRect.bottom, scrollerRect.top, scrollerRect.bottom, align)
      : alignDelta(nodeRect.left, nodeRect.right, scrollerRect.left, scrollerRect.right, align);
  if (delta === 0) return;
  // `scrollTo?.` because jsdom implements no element scrolling.
  if (axis === "y") scroller.scrollTo?.({ top: scroller.scrollTop + delta, behavior });
  else scroller.scrollTo?.({ left: scroller.scrollLeft + delta, behavior });
}

/**
 * Bring `node` to `block` in its nearest vertical scroller — `"start"` lines its
 * top up with the scroller's top, `"end"` its foot with the foot. Does nothing
 * when no ancestor scrolls — there is then nothing that *should* move.
 */
export function scrollIntoScroller(node: HTMLElement, block: ScrollAlign, behavior: ScrollBehavior = "auto"): void {
  scrollOnAxis(node, "y", block, behavior);
}

/** {@link scrollIntoScroller} on the inline axis, for a strip that scrolls sideways. */
export function scrollIntoScrollerInline(
  node: HTMLElement,
  inline: ScrollAlign,
  behavior: ScrollBehavior = "auto",
): void {
  scrollOnAxis(node, "x", inline, behavior);
}

/** Scroll `node`'s nearest scroller all the way down. */
export function scrollScrollerToBottom(node: HTMLElement, behavior: ScrollBehavior = "auto"): void {
  const scroller = scrollableAncestor(node);
  scroller?.scrollTo?.({ top: scroller.scrollHeight, behavior });
}
