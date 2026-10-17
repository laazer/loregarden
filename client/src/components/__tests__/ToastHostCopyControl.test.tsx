/**
 * S3, S6 and S7.5 — handing the value over.
 *
 * The question this surface answers (UX1): the action was refused, the message
 * names a path, how does the operator get it out of here? The answer is one
 * named control, and the thing worth asserting about it is that the clipboard
 * receives the WHOLE message — the clamp still clips pixels, so a copy that
 * trimmed, normalised or path-extracted would hand over exactly the broken
 * value the operator can already see.
 *
 * The fixture is the real 239-character refusal from the live database, pushed
 * through the real route (`ApiError` → `toastActionFailed` → `describeError`),
 * because the defect only exists at that length.
 *
 * S6.4 fence, deliberately honoured by omission: nothing here asserts how many
 * characters are visible or clipped. jsdom has no layout and `max-height:
 * 5.8em` hides nothing in it, so any such number would be an estimate wearing
 * a measurement's clothes. That measurement is `npm run visual-qa`'s.
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

const writeText = jest.fn<Promise<void>, [string]>();

beforeEach(() => {
  jest.clearAllMocks();
  writeText.mockResolvedValue(undefined);
  useToastStore.getState().clear();
});

/**
 * `userEvent.setup()` installs a clipboard stub of its own, so the stub this
 * file asserts against has to be defined AFTER it. Defined before, every
 * assertion about what reached the clipboard reads zero calls while the button
 * still says "Copied" — a test that fails for a reason that has nothing to do
 * with the criterion.
 */
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

/** The refusal exactly as the operator meets it: server detail through the store. */
function raiseRefusal() {
  act(() => {
    toastActionFailed(CHAT_DELETE_REFUSAL_TITLE, new ApiError(409, CHAT_DELETE_REFUSAL_MESSAGE));
  });
}

const COPY_NAME = `Copy message: ${CHAT_DELETE_REFUSAL_TITLE} failed`;
const COPIED_NAME = `Copied message: ${CHAT_DELETE_REFUSAL_TITLE} failed`;

describe("S6.1 — the fixture is the real refusal, at its real length", () => {
  it("is 239 characters, with the 86-character path at 108-194", () => {
    // Asserted so a later edit cannot quietly shorten the fixture back into the
    // short string UX3 forbids — which would leave every test below passing
    // while covering none of the case the ticket is about.
    expect(CHAT_DELETE_REFUSAL_MESSAGE).toHaveLength(239);
    expect(CHAT_DELETE_REFUSAL_WORKTREE_PATH).toHaveLength(86);
    expect(CHAT_DELETE_REFUSAL_MESSAGE.indexOf(CHAT_DELETE_REFUSAL_WORKTREE_PATH)).toBe(108);
  });

  it("S6.2 — reaches toast.message verbatim through the real route", () => {
    raiseRefusal();

    const [toast] = useToastStore.getState().toasts;
    expect(toast.title).toBe("Delete session failed");
    expect(toast.message).toBe(CHAT_DELETE_REFUSAL_MESSAGE);
  });
});

describe("S3 — the copy control", () => {
  it("S3.1 — renders a copy control for a toast that carries a message", () => {
    renderHost();
    raiseRefusal();

    expect(screen.getByRole("button", { name: COPY_NAME })).toBeInTheDocument();
  });

  it("S3.1 — renders no copy control at all for a toast with no message", () => {
    // UX8: nothing, not a disabled button. A disabled control is a promise the
    // surface cannot keep, and it takes a tab stop to say so.
    renderHost();
    act(() => {
      pushToast({ tone: "error", title: "Save file failed" });
    });

    expect(screen.queryByRole("button", { name: /^Copy/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss: Save file failed" })).toBeInTheDocument();
  });

  it("S3.3 — puts the whole of toast.message on the clipboard, character for character", async () => {
    const user = setupUser();
    renderHost();
    raiseRefusal();

    await user.click(screen.getByRole("button", { name: COPY_NAME }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0];
    expect(copied).toBe(CHAT_DELETE_REFUSAL_MESSAGE);
    expect(copied).toHaveLength(239);
    expect(copied).toContain(CHAT_DELETE_REFUSAL_WORKTREE_PATH);
  });

  it("S3.2 — names its own toast, before and after the copy", async () => {
    const user = setupUser();
    renderHost();
    raiseRefusal();

    await user.click(screen.getByRole("button", { name: COPY_NAME }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: COPIED_NAME })).toBeInTheDocument(),
    );
  });

  it("S3.2 — two stacked toasts present two distinct copy controls", () => {
    renderHost();
    act(() => {
      pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
      pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
    });

    const names = screen
      .getAllByRole("button", { name: /^Copy message: / })
      .map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual(["Copy message: Delete session failed", "Copy message: Start run failed"]);
  });

  it("S3.2 — four stacked toasts do not present four controls with one name", () => {
    renderHost();
    act(() => {
      for (const n of [1, 2, 3, 4]) {
        pushToast({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
      }
    });

    const names = screen
      .getAllByRole("button", { name: /^Copy message: / })
      .map((button) => button.getAttribute("aria-label"));
    expect(new Set(names).size).toBe(4);
  });

  it("S3.4 — the control column is copy then close, in DOM and in tab order", async () => {
    const user = setupUser();
    renderHost();
    raiseRefusal();

    const toast = screen.getByRole("alert");
    const copy = screen.getByRole("button", { name: COPY_NAME });
    const close = screen.getByRole("button", { name: "Dismiss: Delete session failed" });

    // Glyph, body, copy, close: the copy control precedes the close button, and
    // both follow the message, so reading order and tab order agree.
    expect(copy.compareDocumentPosition(close) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toast.querySelector(".toast__body")).not.toBeNull();
    expect(
      toast
        .querySelector(".toast__body")!
        .compareDocumentPosition(copy) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    act(() => copy.focus());
    await user.tab();
    expect(close).toHaveFocus();
  });

  it("S3.4 — adds no tabIndex anywhere in the toast", () => {
    renderHost();
    raiseRefusal();

    const toast = screen.getByRole("alert");
    expect(toast.hasAttribute("tabindex")).toBe(false);
    expect(toast.querySelectorAll("[tabindex]")).toHaveLength(0);
  });
});

describe("S7.5 — a clipboard refusal does not take the source toast with it", () => {
  it("adds a second toast and leaves the first one's id untouched", async () => {
    const user = setupUser();
    writeText.mockRejectedValue(new Error("Document is not focused"));
    Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });

    renderHost();
    raiseRefusal();
    const sourceId = useToastStore.getState().toasts[0].id;

    await user.click(screen.getByRole("button", { name: COPY_NAME }));

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(2));
    const toasts = useToastStore.getState().toasts;
    expect(toasts.map((t) => t.title)).toEqual([
      "Delete session failed",
      "Couldn't copy message: Delete session failed",
    ]);
    // The source toast is the one the operator still needs: same id, same
    // message, still on screen. The dedupe cannot match it — different title.
    expect(toasts[0].id).toBe(sourceId);
    expect(toasts[0].message).toBe(CHAT_DELETE_REFUSAL_MESSAGE);
    expect(screen.getByText(CHAT_DELETE_REFUSAL_MESSAGE)).toBeInTheDocument();
  });
});
