import { useEffect, useRef, type RefObject } from "react";

import { focusOnOperatorIntent } from "../lib/focusIntent";

/**
 * Move focus to a surface's own heading once, when the surface first has
 * something to read — and only if the operator asked for it.
 *
 * The dependency list is the fix. `ready` is the library's own status flag
 * (`source.isSuccess`), a primitive that does **not** change across a
 * background refetch — `fetchStatus` is what changes there. So a refetch
 * cannot flip a boolean that is already true, and the effect does not re-run at
 * all. The effect this replaces depended on the query payload's object
 * identity, which every changed refetch renews; that is the defect.
 *
 * The latch is a `string | null` ref keyed by `key`, not a boolean: it survives
 * StrictMode's double-invoke, and a new `key` is the one thing that re-arms it.
 * It is deliberately **not** touched while `!ready`, which is what keeps the
 * error→Retry→data path working — a cold load that errored first must still
 * find the latch unspent, or the operator whose retry just worked is left on
 * `<body>`.
 *
 * Whether the move actually happens is `lib/focusIntent`'s decision, not this
 * hook's. There is exactly one copy of that rule.
 *
 * @param key what the surface is showing — the source id, which is also the
 *   value the activation sites stamp.
 * @param ready whether the surface has data to read, from the query's own
 *   status; never a payload or anything derived from one.
 * @param target the element focus would move to.
 */
export function useFocusOnOperatorIntent(
  key: string,
  ready: boolean,
  target: RefObject<HTMLElement | null>,
) {
  const latched = useRef<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (latched.current === key) return;
    latched.current = key;
    focusOnOperatorIntent(key, target.current);
    // `target` is read, not depended on: a ref whose identity churned on a
    // parent re-render is not an edge worth a second attempt at the move.
  }, [key, ready]);
}
