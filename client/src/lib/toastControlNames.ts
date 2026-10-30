import type { Toast } from "../state/toastStore";

/**
 * Accessible names for a toast's two controls, derived from the live stack.
 *
 * The ambiguity these solve is a property of the STACK, not of a toast:
 * `toastActionFailed` builds a title from the ACTION, so two refusals for two
 * different worktree paths are two live toasts whose controls would otherwise
 * carry one name each (UX-D1). The thing that distinguishes them is an
 * 86-character path the name may not carry (AC3), so the qualifier is
 * positional.
 *
 * It is CONDITIONAL: a toast with no live same-title sibling keeps the name it
 * has today, byte for byte. That identity branch is what makes the common case
 * no worse to hear (AC2) and every existing `Copy message: <title>` /
 * `Dismiss: <title>` assertion still true (AC4), rather than a second code
 * path trying to reproduce them.
 *
 * Neither function reads `tone` (UX-D6): the store dedupes on
 * tone+title+message, so an `error` and a `warning` sharing one title are two
 * live toasts with two colliding names, and a tone-keyed group would re-open
 * the collision for exactly that pair.
 *
 * Nothing is memoized or stored. Both are called in `ToastHost`'s render, so a
 * dismissal, an eviction or a push renumbers the survivors on the next render
 * and no control is left announcing `3 of 3` when two remain (UX-D5).
 */

/**
 * ` <n> of <m>` for a toast that shares its group with another, and `""` for
 * one that does not.
 *
 * The group predicate is a parameter rather than a shared rule on purpose
 * (UX-D8): the copy group drops message-less toasts, which render no copy
 * control, and the close group keeps them, because every toast has a close
 * button. Collapse the two and a stack of one message-less and one
 * message-carrying toast leaves a lone copy control reading `1 of 2`.
 *
 * `stack` is used in the order it arrives — the store array, which `ToastHost`
 * renders directly — so `1 of m` is the first of the group in document order
 * and the first the Tab sequence reaches (UX-D7).
 */
function groupOrdinal(
  toast: Toast,
  stack: readonly Toast[],
  inGroup: (candidate: Toast) => boolean,
): string {
  const group = stack.filter(inGroup);
  const position = group.findIndex((candidate) => candidate.id === toast.id);
  if (group.length < 2 || position < 0) return "";
  return ` ${position + 1} of ${group.length}`;
}

/**
 * The `what` string for a toast's `CopyValueButton`, which composes its own
 * `Copy` / `Copied` / `Couldn't copy` prefix over it unchanged.
 */
export function toastCopyWhat(toast: Toast, stack: readonly Toast[]): string {
  const ordinal = groupOrdinal(
    toast,
    stack,
    (candidate) => candidate.title === toast.title && candidate.message !== "",
  );
  return `message${ordinal}: ${toast.title}`;
}

/** The complete `aria-label` for a toast's close button. */
export function toastDismissLabel(toast: Toast, stack: readonly Toast[]): string {
  const ordinal = groupOrdinal(toast, stack, (candidate) => candidate.title === toast.title);
  return `Dismiss${ordinal}: ${toast.title}`;
}
