/**
 * Adversarial cover for `toastControlNames` — the corners S7 leaves open.
 *
 * S7 states the contract over stacks whose array order, id order, revision
 * order and message order all agree, and whose same-title toasts all differ in
 * their message. Several wrong derivations are green over exactly those stacks:
 *
 * - `[...stack].sort(...)` or `.reverse()` before `findIndex` (risk R4). Every
 *   S7 stack is built in ascending id/revision order, so a sorted copy returns
 *   the same ordinals the correct one does.
 * - `stack.length` as the denominator, or `stack.indexOf` as the position, in
 *   `toastDismissLabel`. S7.7's mixed stack asserts the COPY names exactly and
 *   the dismiss names only through `new Set(...).size`, which both mutants
 *   satisfy — one of them while announcing `Dismiss 3 of 2`.
 * - matching the toast by title+message instead of by `id`. The store dedupes
 *   on tone+title+message, so an `error` and a `warning` sharing BOTH a title
 *   and a message are two live toasts; S7.5's pair differs in its message, so
 *   the mutant numbers them correctly there and gives both `1 of 2` here.
 * - `t.message.trim() !== ""` as the copy group's filter. `ToastHost` renders
 *   the copy control on `toast.message ?`, so a whitespace-only message gets a
 *   control; a group that excludes it leaves two controls with one name.
 *
 * Each case below is the stack that separates the mutant from the contract, and
 * the last one builds its stack from the real store rather than by hand — the
 * store is the only thing that produces an array whose order disagrees with its
 * own `revision` order (a repeat failure refreshes in place).
 */

import { ApiError } from "../../api/http";
import { CHAT_DELETE_REFUSAL_MESSAGE, CHAT_DELETE_REFUSAL_WORKTREE_PATH } from "../../test/chatDeleteRefusal";
import { pushToast, toastActionFailed, useToastStore } from "../../state/toastStore";
import type { Toast, ToastTone } from "../../state/toastStore";
import { toastCopyWhat, toastDismissLabel } from "../toastControlNames";

const TITLE = "Delete session failed";
const OTHER = "Start run failed";

function toast(fields: {
  id: string;
  title?: string;
  message?: string;
  tone?: ToastTone;
  revision?: number;
}): Toast {
  return {
    id: fields.id,
    tone: fields.tone ?? "error",
    title: fields.title ?? TITLE,
    message: fields.message ?? `detail ${fields.id}`,
    duration: 0,
    revision: fields.revision ?? 1,
    // `claims` is empty because the derivation must not read it: a name is
    // derived from the stack's titles and positions, never from how many
    // callers are holding the toast (lg-external-harness-1212).
    claims: [],
  };
}

const copyNames = (stack: readonly Toast[]) => stack.map((t) => toastCopyWhat(t, stack));
const dismissNames = (stack: readonly Toast[]) => stack.map((t) => toastDismissLabel(t, stack));

describe("A1 — the ordinal follows the ARRAY, not any field that usually agrees with it", () => {
  it("numbers by stack position when id, revision and message all order the other way", () => {
    // Descending on every orderable field: `toast-9` sorts after `toast-2`,
    // revision 9 after 2, and "zzz" after "aaa". A derivation that sorts or
    // reverses before indexing hands `1 of 2` to the wrong toast, and S7's
    // fixtures — built in ascending order — cannot tell the two apart.
    const first = toast({ id: "toast-9", revision: 9, message: "zzz /path/one" });
    const second = toast({ id: "toast-2", revision: 2, message: "aaa /path/two" });
    const stack = [first, second];

    expect(toastCopyWhat(first, stack)).toBe("message 1 of 2: Delete session failed");
    expect(toastCopyWhat(second, stack)).toBe("message 2 of 2: Delete session failed");
    expect(toastDismissLabel(first, stack)).toBe("Dismiss 1 of 2: Delete session failed");
    expect(toastDismissLabel(second, stack)).toBe("Dismiss 2 of 2: Delete session failed");
  });

  it("holds at MAX_TOASTS with the ids shuffled against the array", () => {
    const stack = [
      toast({ id: "toast-30", revision: 30, message: "/path/d" }),
      toast({ id: "toast-4", revision: 4, message: "/path/c" }),
      toast({ id: "toast-100", revision: 100, message: "/path/b" }),
      toast({ id: "toast-7", revision: 7, message: "/path/a" }),
    ];

    expect(copyNames(stack)).toEqual([
      "message 1 of 4: Delete session failed",
      "message 2 of 4: Delete session failed",
      "message 3 of 4: Delete session failed",
      "message 4 of 4: Delete session failed",
    ]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 4: Delete session failed",
      "Dismiss 2 of 4: Delete session failed",
      "Dismiss 3 of 4: Delete session failed",
      "Dismiss 4 of 4: Delete session failed",
    ]);
  });
});

describe("A2 — the toast is found by `id`, not by what it says", () => {
  it("numbers an error and a warning that share a title AND a message", () => {
    // Two live toasts: the store's dedupe keys on tone+title+message, so only
    // the tone differs and neither name may read `1 of 2` twice. A derivation
    // matching on title+message gives both position 0 — the exact collision
    // AC1 forbids, surviving S7.5 because that pair differs in its message.
    const shared = `at ${CHAT_DELETE_REFUSAL_WORKTREE_PATH}`;
    const stack = [
      toast({ id: "toast-1", tone: "error", message: shared }),
      toast({ id: "toast-2", tone: "warning", message: shared }),
    ];

    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 2 of 2: Delete session failed",
    ]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);
    expect(new Set(copyNames(stack)).size).toBe(2);
    expect(new Set(dismissNames(stack)).size).toBe(2);
  });

  it("is driven by the store for that pair, not only by hand-built toasts", () => {
    useToastStore.getState().clear();
    const shared = `at ${CHAT_DELETE_REFUSAL_WORKTREE_PATH}`;
    pushToast({ tone: "error", title: TITLE, message: shared });
    pushToast({ tone: "warning", title: TITLE, message: shared });

    const stack = useToastStore.getState().toasts;
    expect(stack).toHaveLength(2);
    expect(new Set(copyNames(stack)).size).toBe(2);
    expect(new Set(dismissNames(stack)).size).toBe(2);
  });
});

describe("A3 — the dismiss name is pinned exactly over a MIXED stack too", () => {
  // S7.7 asserts the copy names exactly here and the dismiss names only by
  // their cardinality. Both `stack.length` as the denominator and
  // `stack.indexOf` as the position keep the names distinct while making them
  // wrong, so cardinality is not an oracle for either.
  it("counts only the same-title toasts, and counts them from within the group", () => {
    const stack = [
      toast({ id: "toast-1", title: TITLE, message: "/path/one" }),
      toast({ id: "toast-2", title: OTHER, message: "worktree is dirty" }),
      toast({ id: "toast-3", title: TITLE, message: "/path/two" }),
    ];

    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss: Start run failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);
  });

  it("keeps two interleaved groups numbered independently", () => {
    const stack = [
      toast({ id: "toast-1", title: TITLE, message: "/path/one" }),
      toast({ id: "toast-2", title: OTHER, message: "dirty one" }),
      toast({ id: "toast-3", title: TITLE, message: "/path/two" }),
      toast({ id: "toast-4", title: OTHER, message: "dirty two" }),
    ];

    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 1 of 2: Start run failed",
      "message 2 of 2: Delete session failed",
      "message 2 of 2: Start run failed",
    ]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 1 of 2: Start run failed",
      "Dismiss 2 of 2: Delete session failed",
      "Dismiss 2 of 2: Start run failed",
    ]);
  });
});

describe("A4 — the copy group holds exactly the toasts that render a copy control", () => {
  it("counts a whitespace-only message, because `ToastHost` renders a control for it", () => {
    // The host's condition is `toast.message ?` — truthiness, not emptiness
    // after a trim. A group filtered with `.trim() !== ""` would exclude this
    // toast while the host still gives it a control, leaving two live copy
    // controls whose names are `message: …` and `message: …`.
    const blank = toast({ id: "toast-1", message: " " });
    const spoken = toast({ id: "toast-2", message: "/path/two" });
    const stack = [blank, spoken];

    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 2 of 2: Delete session failed",
    ]);
  });

  it("excludes only the genuinely empty message", () => {
    const empty = toast({ id: "toast-1", message: "" });
    const blank = toast({ id: "toast-2", message: " " });
    const spoken = toast({ id: "toast-3", message: "/path/three" });
    const stack = [empty, blank, spoken];

    // Two controls render, so two are numbered, and the message-less toast —
    // which renders none — is handed the plain name by S2's `position < 0`.
    expect(toastCopyWhat(blank, stack)).toBe("message 1 of 2: Delete session failed");
    expect(toastCopyWhat(spoken, stack)).toBe("message 2 of 2: Delete session failed");
    expect(toastCopyWhat(empty, stack)).toBe("message: Delete session failed");
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 3: Delete session failed",
      "Dismiss 2 of 3: Delete session failed",
      "Dismiss 3 of 3: Delete session failed",
    ]);
  });
});

describe("A5 — over the real store, where revision order and array order diverge", () => {
  const second = CHAT_DELETE_REFUSAL_MESSAGE.replace(
    CHAT_DELETE_REFUSAL_WORKTREE_PATH,
    `${CHAT_DELETE_REFUSAL_WORKTREE_PATH.slice(0, -8)}b7e41c90`,
  );

  beforeEach(() => useToastStore.getState().clear());

  it("keeps the refreshed toast's ordinal where the toast is, not where its revision is", () => {
    // A repeat failure refreshes its twin IN PLACE: same id, same index, higher
    // `revision`. So after re-raising the first refusal the array is ordered
    // [rev 3, rev 2] — the one stack the store can produce whose array order
    // disagrees with every field a derivation might sort by.
    toastActionFailed("Delete session", new ApiError(409, CHAT_DELETE_REFUSAL_MESSAGE));
    toastActionFailed("Delete session", new ApiError(409, second));
    toastActionFailed("Delete session", new ApiError(409, CHAT_DELETE_REFUSAL_MESSAGE));

    const stack = useToastStore.getState().toasts;
    expect(stack).toHaveLength(2);
    expect(stack[0].message).toBe(CHAT_DELETE_REFUSAL_MESSAGE);
    expect(stack[0].revision).toBeGreaterThan(stack[1].revision);

    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 2 of 2: Delete session failed",
    ]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);
  });

  it("renumbers the survivors when the cap evicts the oldest of a group", () => {
    // UX-D5 names an eviction alongside a dismissal, and the cap is reachable:
    // a fifth same-title refusal drops the first. The survivors must read
    // `1 of 4` upward with the EVICTED toast's message gone from the group.
    for (const n of [1, 2, 3, 4, 5]) {
      pushToast({ tone: "error", title: TITLE, message: `/path/${n}` });
    }

    const stack = useToastStore.getState().toasts;
    expect(stack.map((t) => t.message)).toEqual(["/path/2", "/path/3", "/path/4", "/path/5"]);
    expect(copyNames(stack)).toEqual([
      "message 1 of 4: Delete session failed",
      "message 2 of 4: Delete session failed",
      "message 3 of 4: Delete session failed",
      "message 4 of 4: Delete session failed",
    ]);
    expect(copyNames(stack).some((name) => name.includes("of 5"))).toBe(false);
  });
});

describe("A6 — the stack is read, never written", () => {
  it("works on a frozen stack of frozen toasts", () => {
    // `readonly Toast[]` is a compile-time claim only. A derivation that sorts
    // in place satisfies the type and throws here, which is the behaviour a
    // caller passing the store's own array needs.
    const stack = Object.freeze(
      [1, 2, 3].map((n) => Object.freeze(toast({ id: `toast-${n}`, message: `/path/${n}` }))),
    );

    expect(() => copyNames(stack)).not.toThrow();
    expect(copyNames(stack)).toEqual([
      "message 1 of 3: Delete session failed",
      "message 2 of 3: Delete session failed",
      "message 3 of 3: Delete session failed",
    ]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 3: Delete session failed",
      "Dismiss 2 of 3: Delete session failed",
      "Dismiss 3 of 3: Delete session failed",
    ]);
  });
});
