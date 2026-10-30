/**
 * S7 — the derivation, as a property of the STACK.
 *
 * `toastCopyWhat` and `toastDismissLabel` answer one question (UX-D1): of the
 * controls that share this name, which one is this? The interesting claim is
 * list-level — over any stack, the names a function returns are pairwise
 * distinct — and a list invariant is cheap here and expensive behind a render,
 * which is why the derivation is a pure module with a sibling unit test
 * (S1) rather than a helper inside `ToastHost`.
 *
 * Two groupings, not one (S2 vs S3, UX-D8): the copy group drops
 * message-less toasts because they render no copy control at all, and the
 * close group keeps them because every toast has a close button. A shared
 * helper that collapses the two leaves a lone control reading
 * `message 1 of 2`, and the fourth case below is the one that catches it.
 *
 * Neither function may read `tone` (S4, UX-D6). That is asserted behaviourally
 * — an error and a warning sharing a title must still be numbered — rather
 * than by grepping the source, because the behaviour is what the criterion is
 * about.
 */

import {
  CHAT_DELETE_REFUSAL_MESSAGE,
  CHAT_DELETE_REFUSAL_WORKTREE_PATH,
} from "../../test/chatDeleteRefusal";
import type { Toast, ToastTone } from "../../state/toastStore";
import { toastCopyWhat, toastDismissLabel } from "../toastControlNames";

const TITLE = "Delete session failed";

let seq = 0;

/**
 * A toast as the store holds one. `duration`, `revision` and `claims` are present
 * because `Toast` requires them and absent from every assertion because the
 * derivation must not read them.
 */
function toast(fields: { title?: string; message?: string; tone?: ToastTone; id?: string }): Toast {
  seq += 1;
  return {
    id: fields.id ?? `toast-${seq}`,
    tone: fields.tone ?? "error",
    title: fields.title ?? TITLE,
    message: fields.message ?? `detail ${seq}`,
    duration: 0,
    revision: seq,
    // `claims` is empty because the derivation must not read it: a name is
    // derived from the stack's titles and positions, never from how many
    // callers are holding the toast (lg-external-harness-1212).
    claims: [],
  };
}

const copyNames = (stack: readonly Toast[]) => stack.map((t) => toastCopyWhat(t, stack));
const dismissNames = (stack: readonly Toast[]) => stack.map((t) => toastDismissLabel(t, stack));

describe("S7.1 — a toast with no same-title sibling keeps the name it has today", () => {
  it("returns the plain copy `what` and the plain dismiss label", () => {
    const only = toast({ title: TITLE });

    // Byte-identical to lg-external-harness-1203. AC2 and AC4 are this branch.
    expect(toastCopyWhat(only, [only])).toBe("message: Delete session failed");
    expect(toastDismissLabel(only, [only])).toBe("Dismiss: Delete session failed");
  });

  it("S7.2 — and so does each of two toasts with DIFFERENT titles", () => {
    const stack = [toast({ title: TITLE }), toast({ title: "Start run failed" })];

    expect(copyNames(stack)).toEqual(["message: Delete session failed", "message: Start run failed"]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss: Delete session failed",
      "Dismiss: Start run failed",
    ]);
  });
});

describe("S7.3 — two toasts sharing a title are numbered, in stack order", () => {
  const stack = [
    toast({ title: TITLE, message: "/path/one" }),
    toast({ title: TITLE, message: "/path/two" }),
  ];

  it("numbers the copy control `1 of 2` then `2 of 2`", () => {
    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 2 of 2: Delete session failed",
    ]);
  });

  it("numbers the close button the same way (UX-D8)", () => {
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);
  });

  it("renames the FIRST toast too, not only the newcomer", () => {
    // Symmetric renaming, settled at plan-synthesis: qualifying only the newer
    // toast leaves the pair half-ambiguous in exactly the direction AC1 forbids.
    expect(toastCopyWhat(stack[0], stack)).not.toBe("message: Delete session failed");
    expect(toastDismissLabel(stack[0], stack)).not.toBe("Dismiss: Delete session failed");
  });
});

describe("S7.4 — the copy group and the close group are different groupings", () => {
  // One toast with a message, one without, sharing a title. The message-less
  // toast renders NO copy control (UX8, ToastHostCopyControl S3.1), so counting
  // it would leave a lone control announcing `message 1 of 2`.
  const silent = toast({ title: TITLE, message: "" });
  const spoken = toast({ title: TITLE, message: "/path/one" });
  const stack = [silent, spoken];

  it("leaves the one copy control with the PLAIN name", () => {
    expect(toastCopyWhat(spoken, stack)).toBe("message: Delete session failed");
  });

  it("still numbers BOTH close buttons, because both exist", () => {
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);
  });

  it("gives the message-less toast a plain copy name rather than a position", () => {
    // S2's `position < 0` branch, reached when a message-less toast is asked
    // for a copy name it will never render. Total function, no undefined.
    expect(toastCopyWhat(silent, stack)).toBe("message: Delete session failed");
  });
});

describe("S7.5 — the group is keyed on the title ALONE (UX-D6)", () => {
  it("numbers an error and a warning that share one title", () => {
    // The store dedupes on tone+title+message, so these are two live toasts
    // with two names that would otherwise collide. A tone-keyed group re-opens
    // the collision for exactly this pair.
    const stack = [
      toast({ tone: "error", title: TITLE, message: "/path/one" }),
      toast({ tone: "warning", title: TITLE, message: "/path/two" }),
    ];

    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 2 of 2: Delete session failed",
    ]);
    expect(dismissNames(stack)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);
  });
});

describe("S7.6 — the largest reachable volume is MAX_TOASTS (UX-D3)", () => {
  const stack = [1, 2, 3, 4].map((n) => toast({ title: TITLE, message: `/path/${n}` }));

  it("reads `1 of 4` through `4 of 4`, single-digit, no truncation or collapse", () => {
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

describe("S7.7 — the list-level property: names are pairwise distinct", () => {
  // The claim the whole ticket rests on, stated over the stack rather than over
  // a pair. Every reachable same-title volume, since MAX_TOASTS is 4.
  for (const size of [2, 3, 4]) {
    it(`holds for ${size} same-title toasts that all carry a message`, () => {
      const stack = Array.from({ length: size }, (_, n) =>
        toast({ title: TITLE, message: `/path/${n}` }),
      );

      expect(new Set(copyNames(stack)).size).toBe(size);
      expect(new Set(dismissNames(stack)).size).toBe(size);
    });
  }

  it("holds for the close names when one of the group carries no message", () => {
    const stack = [
      toast({ title: TITLE, message: "" }),
      toast({ title: TITLE, message: "/path/one" }),
      toast({ title: TITLE, message: "" }),
    ];

    expect(new Set(dismissNames(stack)).size).toBe(3);
  });

  it("holds across a mixed stack, within each title group", () => {
    const stack = [
      toast({ title: TITLE, message: "/path/one" }),
      toast({ title: "Start run failed", message: "worktree is dirty" }),
      toast({ title: TITLE, message: "/path/two" }),
    ];

    expect(copyNames(stack)).toEqual([
      "message 1 of 2: Delete session failed",
      "message: Start run failed",
      "message 2 of 2: Delete session failed",
    ]);
    expect(new Set(copyNames(stack)).size).toBe(3);
    expect(new Set(dismissNames(stack)).size).toBe(3);
  });
});

describe("S7.8 — no name carries the message or the path (AC3)", () => {
  it("names two real refusals without leaking either worktree path", () => {
    const second = CHAT_DELETE_REFUSAL_WORKTREE_PATH.slice(0, -8) + "b7e41c90";
    const stack = [
      toast({ title: TITLE, message: CHAT_DELETE_REFUSAL_MESSAGE }),
      toast({
        title: TITLE,
        message: CHAT_DELETE_REFUSAL_MESSAGE.replace(CHAT_DELETE_REFUSAL_WORKTREE_PATH, second),
      }),
    ];

    for (const name of [...copyNames(stack), ...dismissNames(stack)]) {
      expect(name).not.toContain(CHAT_DELETE_REFUSAL_WORKTREE_PATH);
      expect(name).not.toContain(second);
      expect(name).not.toContain(CHAT_DELETE_REFUSAL_MESSAGE);
      // Not just the whole path: no distinguishing run of it either. The two
      // messages are byte-identical except inside the path, so a name built
      // from any slice of the message would either leak it or collide.
      expect(name).not.toContain("/Users/");
      expect(name).not.toContain("unpublished work");
    }
  });
});

describe("S7.9 — the ordinal describes the stack as it is NOW (UX-D5)", () => {
  it("renumbers the survivors when the first of three is removed", () => {
    const three = [1, 2, 3].map((n) => toast({ title: TITLE, message: `/path/${n}` }));
    expect(copyNames(three)).toEqual([
      "message 1 of 3: Delete session failed",
      "message 2 of 3: Delete session failed",
      "message 3 of 3: Delete session failed",
    ]);

    const two = three.slice(1);
    expect(copyNames(two)).toEqual([
      "message 1 of 2: Delete session failed",
      "message 2 of 2: Delete session failed",
    ]);
    expect(dismissNames(two)).toEqual([
      "Dismiss 1 of 2: Delete session failed",
      "Dismiss 2 of 2: Delete session failed",
    ]);

    // Down to one, the qualifier disappears rather than freezing at `3 of 3`.
    const one = two.slice(1);
    expect(copyNames(one)).toEqual(["message: Delete session failed"]);
    expect(dismissNames(one)).toEqual(["Dismiss: Delete session failed"]);
  });

  it("gives a toast that is no longer in the stack the plain name, not a stale position", () => {
    const gone = toast({ title: TITLE, message: "/path/gone" });
    const live = [1, 2].map((n) => toast({ title: TITLE, message: `/path/${n}` }));

    expect(toastCopyWhat(gone, live)).toBe("message: Delete session failed");
    expect(toastDismissLabel(gone, live)).toBe("Dismiss: Delete session failed");
  });
});

describe("S7.10 — both functions are pure", () => {
  it("does not mutate or reorder the stack it is given", () => {
    const stack = [1, 2, 3].map((n) => toast({ title: TITLE, message: `/path/${n}` }));
    const before = JSON.stringify(stack);

    copyNames(stack);
    dismissNames(stack);

    expect(JSON.stringify(stack)).toBe(before);
  });

  it("returns the same name for the same inputs", () => {
    const stack = [1, 2].map((n) => toast({ title: TITLE, message: `/path/${n}` }));

    expect(toastCopyWhat(stack[1], stack)).toBe(toastCopyWhat(stack[1], stack));
    expect(toastDismissLabel(stack[1], stack)).toBe(toastDismissLabel(stack[1], stack));
  });
});
