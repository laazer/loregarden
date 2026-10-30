/**
 * Adversarial cover on the real host — the stacks S8 cannot reach.
 *
 * Every stack in `ToastHostDuplicateTitles.test.tsx` is built by pushing
 * distinct messages in ascending order, so its array order, its id order and
 * its `revision` order agree. Three reachable stacks break that agreement, and
 * each of them is a way to ship a name that points at the wrong button:
 *
 * - B1: a REPEATED failure refreshes its twin in place (same id, same index,
 *   higher revision), so the array is ordered [rev 3, rev 2]. A derivation
 *   ordered by revision renders `2 of 2` on the first toast in the DOM —
 *   green over every S8 fixture, and exactly the UX-D7 defect.
 * - B2/B5: the `MAX_TOASTS` cap evicts, so the group renumbers without any
 *   dismissal. UX-D5 names an eviction and nothing asserted one; UX-D9 accepts
 *   the clipboard refusal's own push as one of them.
 * - B4: an `error` and a `warning` sharing a title AND a message are two live
 *   toasts (the store dedupes on tone+title+message), the one pair a
 *   content-keyed position cannot tell apart.
 *
 * B3 pins the copy group to the host's own render condition, and B6 states the
 * host-level invariant the ticket actually rests on: every control the host
 * renders can be picked by name.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";

import { ApiError } from "../../api/http";
import {
  CHAT_DELETE_REFUSAL_MESSAGE,
  CHAT_DELETE_REFUSAL_TITLE,
  CHAT_DELETE_REFUSAL_WORKTREE_PATH,
} from "../../test/chatDeleteRefusal";
import { pushToast, toastActionFailed, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

const TITLE = `${CHAT_DELETE_REFUSAL_TITLE} failed`;
const SECOND_WORKTREE_PATH = `${CHAT_DELETE_REFUSAL_WORKTREE_PATH.slice(0, -8)}b7e41c90`;
const SECOND_REFUSAL_MESSAGE = CHAT_DELETE_REFUSAL_MESSAGE.replace(
  CHAT_DELETE_REFUSAL_WORKTREE_PATH,
  SECOND_WORKTREE_PATH,
);

const writeText = jest.fn<Promise<void>, [string]>();

beforeEach(() => {
  jest.clearAllMocks();
  writeText.mockResolvedValue(undefined);
  useToastStore.getState().clear();
});

/** As in ToastHostCopyControl: the stub must be installed AFTER userEvent's own. */
function setupUser() {
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return user;
}

function renderHost() {
  return render(
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <ToastHost />
      </MotionConfig>
    </LazyMotion>,
  );
}

const copyNames = () =>
  screen.getAllByRole("button", { name: /^Copy/ }).map((b) => b.getAttribute("aria-label"));
const dismissNames = () =>
  screen.getAllByRole("button", { name: /^Dismiss/ }).map((b) => b.getAttribute("aria-label"));

describe("B1 — a repeated failure keeps its place, and its ordinal keeps its place", () => {
  it("leaves `1 of 2` on the first toast in the DOM after the first is re-raised", async () => {
    const user = setupUser();
    renderHost();
    act(() => {
      toastActionFailed(CHAT_DELETE_REFUSAL_TITLE, new ApiError(409, CHAT_DELETE_REFUSAL_MESSAGE));
      toastActionFailed(CHAT_DELETE_REFUSAL_TITLE, new ApiError(409, SECOND_REFUSAL_MESSAGE));
      // The same refusal again: refreshed in place with a higher revision.
      toastActionFailed(CHAT_DELETE_REFUSAL_TITLE, new ApiError(409, CHAT_DELETE_REFUSAL_MESSAGE));
    });

    const stack = useToastStore.getState().toasts;
    expect(stack).toHaveLength(2);
    expect(stack[0].revision).toBeGreaterThan(stack[1].revision);

    expect(copyNames()).toEqual([
      `Copy message 1 of 2: ${TITLE}`,
      `Copy message 2 of 2: ${TITLE}`,
    ]);
    expect(dismissNames()).toEqual([`Dismiss 1 of 2: ${TITLE}`, `Dismiss 2 of 2: ${TITLE}`]);

    // And the name still points at the toast it is rendered on.
    await user.click(screen.getByRole("button", { name: `Copy message 1 of 2: ${TITLE}` }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toBe(CHAT_DELETE_REFUSAL_MESSAGE);
  });
});

describe("B2 — the cap renumbers the group with nothing dismissed (UX-D5)", () => {
  it("drops the oldest and reads `1 of 4` through `4 of 4` over the survivors", async () => {
    const user = setupUser();
    renderHost();
    act(() => {
      for (const n of [1, 2, 3, 4, 5]) {
        pushToast({ tone: "error", title: TITLE, message: `/path/${n}` });
      }
    });

    expect(useToastStore.getState().toasts).toHaveLength(4);
    expect(copyNames()).toEqual([
      `Copy message 1 of 4: ${TITLE}`,
      `Copy message 2 of 4: ${TITLE}`,
      `Copy message 3 of 4: ${TITLE}`,
      `Copy message 4 of 4: ${TITLE}`,
    ]);
    expect(screen.queryByRole("button", { name: `Copy message 5 of 5: ${TITLE}` })).not.toBeInTheDocument();

    // `1 of 4` is the second push, since the first was evicted — the ordinal
    // describes the stack as it is now, not the order the pushes arrived in.
    await user.click(screen.getByRole("button", { name: `Copy message 1 of 4: ${TITLE}` }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toBe("/path/2");
  });
});

describe("B3 — a whitespace-only message still renders a control, so it is still numbered", () => {
  it("gives two copy controls two distinct names", () => {
    // `ToastHost` renders the control on `toast.message ?`. A copy group
    // filtered on a trimmed message would exclude this toast while the host
    // keeps its button, putting one name on two live controls.
    renderHost();
    act(() => {
      pushToast({ tone: "error", title: TITLE, message: " " });
      pushToast({ tone: "error", title: TITLE, message: CHAT_DELETE_REFUSAL_MESSAGE });
    });

    const names = copyNames();
    expect(names).toHaveLength(2);
    expect(new Set(names).size).toBe(2);
    expect(names).toEqual([`Copy message 1 of 2: ${TITLE}`, `Copy message 2 of 2: ${TITLE}`]);
  });
});

describe("B4 — one title, one message, two tones (UX-D6)", () => {
  it("numbers the error and the warning, and dismisses the one the name claims", async () => {
    const user = setupUser();
    renderHost();
    act(() => {
      pushToast({ tone: "error", title: TITLE, message: CHAT_DELETE_REFUSAL_MESSAGE });
      pushToast({ tone: "warning", title: TITLE, message: CHAT_DELETE_REFUSAL_MESSAGE });
    });

    expect(useToastStore.getState().toasts).toHaveLength(2);
    expect(copyNames()).toEqual([
      `Copy message 1 of 2: ${TITLE}`,
      `Copy message 2 of 2: ${TITLE}`,
    ]);
    expect(dismissNames()).toEqual([`Dismiss 1 of 2: ${TITLE}`, `Dismiss 2 of 2: ${TITLE}`]);

    await user.click(screen.getByRole("button", { name: `Dismiss 1 of 2: ${TITLE}` }));
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
    expect(useToastStore.getState().toasts[0].tone).toBe("warning");
  });
});

describe("B5 — a clipboard refusal is itself a push, and the group renumbers under it (UX-D9)", () => {
  it("leaves the survivors reading `1 of 3` through `3 of 3` with no stale `4 of 4` control", async () => {
    const user = setupUser();
    writeText.mockRejectedValue(new Error("Document is not focused"));
    renderHost();
    act(() => {
      for (const n of [1, 2, 3, 4]) {
        pushToast({ tone: "error", title: TITLE, message: `/path/${n}` });
      }
    });

    await user.click(screen.getByRole("button", { name: `Copy message 4 of 4: ${TITLE}` }));

    // The refusal's own toast is a fifth push, which evicts `/path/1` under the
    // accepted UX14 cap, so the group is three and renumbers.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: `Copy message 3 of 3: ${TITLE}` }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: `Copy message 1 of 3: ${TITLE}` })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Copy message 2 of 3: ${TITLE}` })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: `Copy message 4 of 4: ${TITLE}` }),
    ).not.toBeInTheDocument();

    // The refusal names which of the group refused, purely by composition —
    // `CopyValueButton` gains no prop and no branch.
    expect(screen.getByText(`Couldn't copy message 4 of 4: ${TITLE}`)).toBeInTheDocument();
  });
});

describe("B6 — every control the host renders can be picked by name", () => {
  it("holds over a mixed stack of message-carrying and message-less toasts", () => {
    renderHost();
    act(() => {
      // Interleaved on purpose: the other title sits BETWEEN two members of the
      // group, so a position taken from the stack rather than from the group
      // numbers the last of them `3 of 3` out of a group of three it is second
      // in — and `4 of 3` once the message-less toast is counted.
      pushToast({ tone: "error", title: TITLE, message: CHAT_DELETE_REFUSAL_MESSAGE });
      pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
      pushToast({ tone: "error", title: TITLE });
      pushToast({ tone: "error", title: TITLE, message: SECOND_REFUSAL_MESSAGE });
    });

    const toasts = useToastStore.getState().toasts;
    const withMessage = toasts.filter((t) => t.message !== "");

    // One close button per toast, one copy control per message-carrying toast,
    // and no name shared inside either kind.
    expect(dismissNames()).toHaveLength(toasts.length);
    expect(new Set(dismissNames()).size).toBe(toasts.length);
    expect(copyNames()).toHaveLength(withMessage.length);
    expect(new Set(copyNames()).size).toBe(withMessage.length);

    // The two groupings differ: three toasts share the title, two of them carry
    // a message, so the close names count to three and the copy names to two.
    expect(dismissNames()).toEqual([
      `Dismiss 1 of 3: ${TITLE}`,
      "Dismiss: Start run failed",
      `Dismiss 2 of 3: ${TITLE}`,
      `Dismiss 3 of 3: ${TITLE}`,
    ]);
    expect(copyNames()).toEqual([
      `Copy message 1 of 2: ${TITLE}`,
      "Copy message: Start run failed",
      `Copy message 2 of 2: ${TITLE}`,
    ]);
  });
});
