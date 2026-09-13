import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

/**
 * Keep a chat thread pinned to its newest content as that content grows.
 *
 * The obvious version — scroll to the bottom when the message count changes —
 * only tracks turns *arriving*. Most of a thread's height is gained after that:
 * a reply streams in a token at a time, a thinking card unfolds, a primitive
 * resolves its query and grows from a skeleton into a table, an image decodes.
 * Every one of those leaves the bottom of the thread below the fold, and the
 * operator has to chase it with the wheel. So the list's height is what is
 * watched, not its length.
 *
 * ## Deciding whether to follow
 *
 * Following every growth would make a thread impossible to read while an agent
 * works — scroll up to re-read something and the next frame drags you back
 * down. So a growth is followed only when the operator was at the bottom
 * *before* it happened, which is what `lastHeight` records: the scroller's
 * height as of the previous observation, compared against where the operator is
 * standing now.
 *
 * The first version of this tracked a `pinned` flag from the scroller's own
 * `scroll` events instead, and it was wrong in two ways that only showed up in
 * the running app. It measured once when the hook attached — on a thread that
 * has just loaded and is therefore scrolled to the *top* — and recorded "not at
 * the bottom" before the operator had done anything, so nothing ever followed.
 * And a smooth programmatic scroll emits `scroll` events the whole way down, so
 * the hook's own animation was read as the operator scrolling away.
 *
 * Comparing against the previous height has neither problem: there is no flag
 * to seed wrongly, and a scroll this hook performed leaves the thread at the
 * bottom, which is exactly what the next comparison wants to see. It also
 * self-heals — the operator scrolling back down starts the thread following
 * again with no event to listen for.
 *
 * @param listRef The element whose height changes — the message list.
 * @param bottomRef The tail element a new turn scrolls into view.
 * @param enabled False to stand down entirely (a surface that manages its own
 *   scrolling); nothing is observed and nothing is scrolled.
 * @param turnKeys Values that mean "a turn happened" rather than "it grew" —
 *   message count, busy flag. A change scrolls to the bottom and re-arms the
 *   follow, because a turn arriving is the operator asking to be down there.
 */
export function useStickToBottom(
  listRef: RefObject<HTMLElement | null>,
  bottomRef: RefObject<HTMLElement | null>,
  enabled: boolean,
  turnKeys: ReadonlyArray<unknown>,
): void {
  // 0 means "follow the next growth whatever the scroll position": nothing has
  // been observed yet, so a thread that has just mounted lands at its newest
  // content rather than at the top of its history.
  const lastHeightRef = useRef(0);

  // Serialised rather than spread: the array identity changes every render, and
  // the effect below must fire on a changed *value*, not on every render.
  const turnSignature = JSON.stringify(turnKeys);

  // A new turn re-arms the follow and scrolls. Layout effect so the scroll is
  // issued in the frame the turn painted in.
  useLayoutEffect(() => {
    if (!enabled) return;
    lastHeightRef.current = 0;
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [enabled, bottomRef, turnSignature]);

  useEffect(() => {
    if (!enabled) return;
    const list = listRef.current;
    // jsdom and older embedded webviews have no ResizeObserver. The turn-level
    // scroll above still runs, so the thread follows new messages — it just
    // stops following content that grows inside one.
    if (!list || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      const scroller = scrollableAncestor(list);
      if (!scroller) return;
      const previousHeight = lastHeightRef.current;
      lastHeightRef.current = scroller.scrollHeight;
      // 32px of slack: a thread at the very bottom often sits a pixel or two
      // short of it after a reflow, and an exact comparison would read that as
      // the operator having scrolled away.
      const wasAtBottom =
        previousHeight - scroller.scrollTop - scroller.clientHeight < 32;
      if (!wasAtBottom) return;
      // Instant, not smooth: an animation restarted on every streamed token
      // never arrives, and the thread jitters in place instead of following.
      scroller.scrollTop = scroller.scrollHeight;
    });
    observer.observe(list);
    return () => observer.disconnect();
    // `listRef.current` is read inside the effect, so a surface that swaps its
    // list element re-observes on its next enable rather than never — see the
    // scroller lookup below, which is deliberately done per callback for the
    // same reason.
  }, [enabled, listRef]);
}

/** The nearest ancestor that actually scrolls, or null when nothing does. */
function scrollableAncestor(node: HTMLElement): HTMLElement | null {
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
