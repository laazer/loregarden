/**
 * S8 — two refusals, one title, on the real host.
 *
 * The defect this file pins (AC1): `toastActionFailed` builds the title from
 * the ACTION, so two delete refusals for two different worktree paths stack as
 * two live toasts whose copy controls — and whose close buttons (UX-D8) —
 * report one accessible name. A screen reader cannot tell them apart, and
 * `getAllByRole` cannot either, which is the same defect for an agent driving
 * the UI.
 *
 * Everything here is asserted on the rendered DOM rather than on the store
 * array, deliberately (UX-D7, risk R4): the claim is that `1 of 2` is the
 * control a reader meets first and Tab reaches first, so a derivation whose
 * iteration order drifts from render order has to fail.
 *
 * The two refusals differ only INSIDE the 86-character path — the same
 * measured shape the live database has, where all 8 chat worktrees share a
 * 50-character prefix. That is what makes the positional qualifier the only
 * distinguishing name available: any slice of the message is either identical
 * across the pair or is the path AC3 forbids in a name.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
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

/** The title the operator actually meets: `toastActionFailed` appends "failed". */
const TITLE = `${CHAT_DELETE_REFUSAL_TITLE} failed`;

/**
 * A second chat's refusal, differing from the fixture only inside the path.
 *
 * Same 86 characters, same 50-character prefix, same 239-character sentence —
 * the worktree slug's trailing hex is the entire difference, exactly as two
 * rows of the live table differ.
 */
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

/** The host under the app's own motion providers, as AppLayout mounts it. */
function renderHost() {
  return render(
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <ToastHost />
      </MotionConfig>
    </LazyMotion>,
  );
}

/** Both refusals through the real route: server detail → ApiError → the store. */
function raiseBothRefusals() {
  act(() => {
    toastActionFailed(CHAT_DELETE_REFUSAL_TITLE, new ApiError(409, CHAT_DELETE_REFUSAL_MESSAGE));
    toastActionFailed(CHAT_DELETE_REFUSAL_TITLE, new ApiError(409, SECOND_REFUSAL_MESSAGE));
  });
}

const copyNames = () =>
  screen.getAllByRole("button", { name: /^Copy/ }).map((b) => b.getAttribute("aria-label"));
const dismissNames = () =>
  screen.getAllByRole("button", { name: /^Dismiss/ }).map((b) => b.getAttribute("aria-label"));

describe("S8.0 — the fixture pair is the measured shape", () => {
  it("differs only inside the 86-character path", () => {
    expect(SECOND_WORKTREE_PATH).toHaveLength(86);
    expect(SECOND_WORKTREE_PATH).not.toBe(CHAT_DELETE_REFUSAL_WORKTREE_PATH);
    expect(SECOND_REFUSAL_MESSAGE).toHaveLength(CHAT_DELETE_REFUSAL_MESSAGE.length);

    // The whole differing span lies at or after index 108, where the path
    // starts — so a name built from a prefix or a tail of the message is
    // byte-identical across the pair. That is why the qualifier is positional.
    const firstDifference = [...CHAT_DELETE_REFUSAL_MESSAGE].findIndex(
      (ch, i) => ch !== SECOND_REFUSAL_MESSAGE[i],
    );
    expect(firstDifference).toBeGreaterThanOrEqual(108);
  });

  it("stacks as two live toasts rather than deduping to one", () => {
    raiseBothRefusals();

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(2);
    expect(toasts.map((t) => t.title)).toEqual([TITLE, TITLE]);
  });
});

describe("S8.1 — two same-title refusals present two DISTINCT copy names (AC1)", () => {
  it("numbers them in document order", () => {
    renderHost();
    raiseBothRefusals();

    expect(copyNames()).toEqual([
      `Copy message 1 of 2: ${TITLE}`,
      `Copy message 2 of 2: ${TITLE}`,
    ]);
  });

  it("gives an exact-name lookup exactly one button each", () => {
    // What `getAllByRole` could not do before the fix, and what an agent
    // driving the UI needs: pick ONE of the pair by name.
    renderHost();
    raiseBothRefusals();

    expect(screen.getByRole("button", { name: `Copy message 1 of 2: ${TITLE}` })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Copy message 2 of 2: ${TITLE}` })).toBeInTheDocument();
  });
});

describe("S8.2 — the ordinal is DOM order and tab order (UX-D7)", () => {
  it("reaches `1 of 2`'s controls before `2 of 2`'s", async () => {
    const user = setupUser();
    renderHost();
    raiseBothRefusals();

    const first = screen.getByRole("button", { name: `Copy message 1 of 2: ${TITLE}` });
    act(() => first.focus());

    await user.tab();
    expect(screen.getByRole("button", { name: `Dismiss 1 of 2: ${TITLE}` })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: `Copy message 2 of 2: ${TITLE}` })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: `Dismiss 2 of 2: ${TITLE}` })).toHaveFocus();
  });

  it("puts `1 of 2` first in the live region's document order", () => {
    renderHost();
    raiseBothRefusals();

    const first = screen.getByRole("button", { name: `Copy message 1 of 2: ${TITLE}` });
    const second = screen.getByRole("button", { name: `Copy message 2 of 2: ${TITLE}` });
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("S8.3 — the close button carries the same qualifier (UX-D8)", () => {
  it("numbers both close buttons", () => {
    renderHost();
    raiseBothRefusals();

    expect(dismissNames()).toEqual([`Dismiss 1 of 2: ${TITLE}`, `Dismiss 2 of 2: ${TITLE}`]);
  });

  it("dismisses the one the name claims", async () => {
    // Keyboard dismissal is this surface's only exit. A close button that
    // cannot be picked by name is half the fix.
    const user = setupUser();
    renderHost();
    raiseBothRefusals();

    await user.click(screen.getByRole("button", { name: `Dismiss 1 of 2: ${TITLE}` }));

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
    expect(useToastStore.getState().toasts[0].message).toBe(SECOND_REFUSAL_MESSAGE);
  });
});

describe("S8.4 — the largest reachable volume, MAX_TOASTS (UX-D3)", () => {
  it("reads `1 of 4` through `4 of 4` on both controls, eight distinct names", async () => {
    renderHost();
    act(() => {
      for (const n of [1, 2, 3, 4]) {
        toastActionFailed(
          CHAT_DELETE_REFUSAL_TITLE,
          new ApiError(
            409,
            CHAT_DELETE_REFUSAL_MESSAGE.replace(
              CHAT_DELETE_REFUSAL_WORKTREE_PATH,
              `${CHAT_DELETE_REFUSAL_WORKTREE_PATH.slice(0, -1)}${n}`,
            ),
          ),
        );
      }
    });

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(4));
    expect(copyNames()).toEqual([
      `Copy message 1 of 4: ${TITLE}`,
      `Copy message 2 of 4: ${TITLE}`,
      `Copy message 3 of 4: ${TITLE}`,
      `Copy message 4 of 4: ${TITLE}`,
    ]);
    expect(dismissNames()).toEqual([
      `Dismiss 1 of 4: ${TITLE}`,
      `Dismiss 2 of 4: ${TITLE}`,
      `Dismiss 3 of 4: ${TITLE}`,
      `Dismiss 4 of 4: ${TITLE}`,
    ]);
    expect(new Set(copyNames()).size).toBe(4);
    expect(new Set(dismissNames()).size).toBe(4);
    // Single-digit at every reachable volume: no truncation, grouping or collapse.
    expect(copyNames().every((name) => /^Copy message \d of 4: /.test(name ?? ""))).toBe(true);
  });
});

describe("S8.5 — the ordinal renumbers as the stack changes (UX-D5)", () => {
  it("goes 3 → 2 → plain as the toasts are dismissed", async () => {
    const user = setupUser();
    renderHost();
    act(() => {
      for (const n of [1, 2, 3]) {
        pushToast({ tone: "error", title: TITLE, message: `/path/${n}` });
      }
    });

    expect(copyNames()).toEqual([
      `Copy message 1 of 3: ${TITLE}`,
      `Copy message 2 of 3: ${TITLE}`,
      `Copy message 3 of 3: ${TITLE}`,
    ]);

    await user.click(screen.getByRole("button", { name: `Dismiss 1 of 3: ${TITLE}` }));
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(2));

    // No stale `3 of 3` pointing at a button that is not there.
    await waitFor(() =>
      expect(copyNames()).toEqual([
        `Copy message 1 of 2: ${TITLE}`,
        `Copy message 2 of 2: ${TITLE}`,
      ]),
    );
    expect(dismissNames()).toEqual([`Dismiss 1 of 2: ${TITLE}`, `Dismiss 2 of 2: ${TITLE}`]);
    expect(screen.queryByRole("button", { name: /3 of 3/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: `Dismiss 1 of 2: ${TITLE}` }));
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));

    // Back to one: the qualifier disappears and the 1203 name returns exactly.
    await waitFor(() => expect(copyNames()).toEqual([`Copy message: ${TITLE}`]));
    expect(dismissNames()).toEqual([`Dismiss: ${TITLE}`]);
  });
});

describe("S8.6 — a message-less sibling splits the two groupings (UX-D8)", () => {
  it("leaves one PLAIN copy control and two numbered close buttons", () => {
    renderHost();
    act(() => {
      pushToast({ tone: "error", title: TITLE });
      pushToast({ tone: "error", title: TITLE, message: CHAT_DELETE_REFUSAL_MESSAGE });
    });

    // The message-less toast renders no copy control at all, so a shared
    // grouping would leave the single remaining control reading `1 of 2`.
    expect(copyNames()).toEqual([`Copy message: ${TITLE}`]);
    expect(dismissNames()).toEqual([`Dismiss 1 of 2: ${TITLE}`, `Dismiss 2 of 2: ${TITLE}`]);
  });
});

describe("S8.7 — the named control copies its own toast's message (UX-D2, UX-D4)", () => {
  it("puts the second refusal verbatim on the clipboard, then reads `Copied message 2 of 2:`", async () => {
    const user = setupUser();
    renderHost();
    raiseBothRefusals();

    await user.click(screen.getByRole("button", { name: `Copy message 2 of 2: ${TITLE}` }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0];
    expect(copied).toBe(SECOND_REFUSAL_MESSAGE);
    expect(copied).toHaveLength(239);
    expect(copied).toContain(SECOND_WORKTREE_PATH);
    expect(copied).not.toContain(CHAT_DELETE_REFUSAL_WORKTREE_PATH);

    // `CopyValueButton`'s own prefix composes over the qualified `what`
    // unchanged — the component gains no prop and no branch.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: `Copied message 2 of 2: ${TITLE}` }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: `Copy message 1 of 2: ${TITLE}` })).toBeInTheDocument();
  });

  it("copies the FIRST refusal when the first control is the one activated", async () => {
    // The ordinal has to point at the toast it is rendered on, not merely be
    // unique: a derivation that numbered the pair in reverse would pass S8.1.
    const user = setupUser();
    renderHost();
    raiseBothRefusals();

    await user.click(screen.getByRole("button", { name: `Copy message 1 of 2: ${TITLE}` }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toBe(CHAT_DELETE_REFUSAL_MESSAGE);
  });
});

describe("S8.8 — no accessible name carries the path (AC3)", () => {
  it("holds for every labelled element the host renders", () => {
    const { container } = renderHost();
    raiseBothRefusals();

    const labelled = [...container.querySelectorAll("[aria-label], [title]")];
    expect(labelled.length).toBeGreaterThan(0);
    for (const element of labelled) {
      for (const attribute of ["aria-label", "title"]) {
        const value = element.getAttribute(attribute) ?? "";
        expect(value).not.toContain(CHAT_DELETE_REFUSAL_WORKTREE_PATH);
        expect(value).not.toContain(SECOND_WORKTREE_PATH);
        expect(value).not.toContain(CHAT_DELETE_REFUSAL_MESSAGE);
        expect(value).not.toContain("/Users/");
      }
    }
  });

  it("still renders the message itself in the toast body, where it belongs", () => {
    renderHost();
    raiseBothRefusals();

    const [first, second] = screen.getAllByRole("alert");
    expect(within(first).getByText(CHAT_DELETE_REFUSAL_MESSAGE)).toBeInTheDocument();
    expect(within(second).getByText(SECOND_REFUSAL_MESSAGE)).toBeInTheDocument();
  });
});
