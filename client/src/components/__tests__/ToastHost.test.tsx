import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";
import {
  CLASS_B_MESSAGE,
  JUST_OVER_THRESHOLD,
  LONGEST_EXTERNAL_ID,
  LONGEST_WORKTREE_PATH,
  LONG_MESSAGE_WITHOUT_PATH,
  LONG_TOAST_MESSAGE_CHARS,
  SHORT_MESSAGE,
  ZWSP,
  copyButton,
  expectedPathSegments,
  expectedWbrCount,
  flushUnderFakeTimers,
  messageEl,
  pathPartsIn,
  pushClassB,
  setupWithClipboard,
  showAll,
  toastEl,
} from "./toastFixtures";

beforeEach(() => {
  useToastStore.getState().clear();
});

it("shows no toast until something fails", () => {
  render(<ToastHost />);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("removes a dismissed toast once its exit has played, under the app's motion providers", async () => {
  const user = userEvent.setup();
  render(
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <ToastHost />
      </MotionConfig>
    </LazyMotion>,
  );

  act(() => {
    pushToast({ tone: "error", title: "Save file failed" });
  });
  await user.click(screen.getByRole("button", { name: "Dismiss: Save file failed" }));

  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("announces a failure as an alert", () => {
  render(<ToastHost />);

  act(() => {
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
  });

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Start run failed");
  expect(alert).toHaveTextContent("worktree is dirty");
});

it("dismisses on the close button", async () => {
  const user = userEvent.setup();
  render(<ToastHost />);

  act(() => {
    pushToast({ tone: "error", title: "Save file failed" });
  });

  await user.click(screen.getByRole("button", { name: "Dismiss: Save file failed" }));

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(0);
});

it("auto-dismisses after its duration", () => {
  jest.useFakeTimers();
  try {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "info", title: "Queued", duration: 2000 });
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

it("keeps a zero-duration toast up until it is dismissed", () => {
  jest.useFakeTimers();
  try {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Reload failed", duration: 0 });
    });

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

/* ------------------------------------------------------------------------- *
 * lg-milestone-that-1316 — a clamped message shows its cut, and the operator
 * can recover the text.
 *
 * The six cases above are the surface as it stood before this ticket and must
 * keep passing unchanged (AC35): the feedback live region renders only while
 * copy feedback is non-idle, so a resting toast still has exactly one `status`
 * node and the two bare `getByRole("status")` assertions stay unambiguous.
 * ------------------------------------------------------------------------- */

describe("the measurements this suite is built on (AC11, AC26)", () => {
  it("are the ones recorded on the ticket", () => {
    expect(LONGEST_EXTERNAL_ID).toHaveLength(29);
    expect(LONGEST_WORKTREE_PATH).toHaveLength(117);
    expect(CLASS_B_MESSAGE).toHaveLength(269);
    expect(LONG_MESSAGE_WITHOUT_PATH.length).toBeGreaterThan(LONG_TOAST_MESSAGE_CHARS);
    expect(LONG_MESSAGE_WITHOUT_PATH).not.toContain("/");
  });
});

describe("a cut is visible, and only when there is one (AC1, AC12, AC37)", () => {
  it("clamps the 269-character class-B refusal", () => {
    render(<ToastHost />);
    pushClassB();

    expect(messageEl()).toHaveClass("toast__message--clamped");
  });

  it("offers both recovery controls on it (AC10, AC20)", () => {
    render(<ToastHost />);
    pushClassB();

    expect(showAll()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy message" })).toBeInTheDocument();
  });

  it("leaves a short message with neither modifier nor controls (AC7, AC19a)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Save file failed", message: "worktree is dirty" });
    });

    expect(messageEl()).not.toHaveClass("toast__message--clamped");
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy message" })).not.toBeInTheDocument();
  });

  it("treats the threshold as exclusive: 160 characters is short, 161 is long (AC18, AC27, AC36)", () => {
    const { unmount } = render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "At the threshold", message: SHORT_MESSAGE });
    });
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
    unmount();

    useToastStore.getState().clear();
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Over the threshold", message: JUST_OVER_THRESHOLD });
    });
    expect(screen.getByRole("button", { name: "Show all" })).toBeInTheDocument();
  });

  it("measures the message alone, not the title (AC36)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "t".repeat(200), message: "worktree is dirty" });
    });

    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
  });
});

describe("the path is never broken mid-identifier (AC3, AC13, AC47, AC51)", () => {
  it("announces the message byte for byte, with no zero-width space anywhere", () => {
    render(<ToastHost />);
    pushClassB();

    // The message node's own text is the contract: the alert also carries the
    // title, the tone glyph and the control labels.
    expect(messageEl()?.textContent).toBe(CLASS_B_MESSAGE);
    expect(screen.getByRole("alert").textContent).toContain(CLASS_B_MESSAGE);
    expect(screen.getByRole("alert").textContent).not.toContain(ZWSP);
  });

  it("wraps each path segment in its own unbreakable span (AC47, AC49, AC52)", () => {
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    const segments = expectedPathSegments(CLASS_B_MESSAGE);
    expect(segments).toHaveLength(9);
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(segments);
    // `white-space: nowrap` is the one declaration that suppresses the hyphen
    // soft-wrap inside a worktree directory name (AC46), and it needs an
    // element to hang off — so the span's job is to hold exactly one segment.
    for (const span of pathPartsIn(node)) expect(span.textContent).not.toMatch(/\s/);
  });

  it("gives the breaker an opportunity between segments and nowhere else (AC51, AC52)", () => {
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(CLASS_B_MESSAGE));
    expect(expectedWbrCount(CLASS_B_MESSAGE)).toBe(8);
    // The prose keeps wrapping at its own spaces: no wbr lands in it, and no
    // span wraps it (AC47 — path segments are made unbreakable, nothing else).
    expect(node.textContent).toBe(CLASS_B_MESSAGE);
  });

  it("leaves a long message that names no path entirely unwrapped (AC19b)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Handoff refused", message: LONG_MESSAGE_WITHOUT_PATH });
    });

    const node = messageEl() as HTMLElement;
    expect(node.querySelectorAll("wbr")).toHaveLength(0);
    expect(pathPartsIn(node)).toHaveLength(0);
    expect(node).toHaveClass("toast__message--clamped");
    expect(showAll()).toBeInTheDocument();
  });

  it("leaves a short message unwrapped too, path or not (AC7, AC19a)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Save file failed", message: "worktree is dirty" });
    });

    const node = messageEl() as HTMLElement;
    expect(pathPartsIn(node)).toHaveLength(0);
    expect(node.querySelectorAll("wbr")).toHaveLength(0);
  });
});

describe("expanding recovers the text without re-announcing the alert (AC16, AC32, AC40)", () => {
  it("flips a className and nothing else", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB();

    const alertBefore = screen.getByRole("alert");
    const nodeBefore = messageEl();
    expect(nodeBefore).toHaveClass("toast__message--clamped");

    await user.click(showAll());

    expect(messageEl()).toBe(nodeBefore);
    expect(screen.getByRole("alert")).toBe(alertBefore);
    expect(messageEl()).not.toHaveClass("toast__message--clamped");
    expect(messageEl()?.textContent).toBe(CLASS_B_MESSAGE);
  });

  it("names its state and toggles back", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB();

    expect(showAll()).toHaveAttribute("aria-expanded", "false");
    await user.click(showAll());

    const less = screen.getByRole("button", { name: "Show less" });
    expect(less).toHaveAttribute("aria-expanded", "true");

    await user.click(less);
    expect(showAll()).toHaveAttribute("aria-expanded", "false");
    expect(messageEl()).toHaveClass("toast__message--clamped");
  });

  it("makes the expanded scroll container keyboard reachable, and labels neither state", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB();

    expect(messageEl()).not.toHaveAttribute("tabindex");
    expect(messageEl()).not.toHaveAttribute("aria-label");

    await user.click(showAll());

    expect(messageEl()).toHaveAttribute("tabindex", "0");
    expect(messageEl()).not.toHaveAttribute("aria-label");
  });

  it("does not move focus when it expands (AC23)", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB();

    const toggle = showAll();
    await user.click(toggle);

    // Focus must stay on the control the operator pressed, not jump to the
    // expanded scroll container.
    expect(document.activeElement).toBe(toggle);
  });
});

describe("the clipboard gets the store's string, not the DOM's (AC14, AC21)", () => {
  const writeText = jest.fn();

  beforeEach(() => {
    writeText.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("copies the untruncated message while it is still clamped", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB();

    expect(messageEl()).toHaveClass("toast__message--clamped");
    await user.click(copyButton());

    expect(writeText).toHaveBeenCalledWith(CLASS_B_MESSAGE);
    expect(writeText.mock.calls[0][0]).not.toContain(ZWSP);
  });

  it("confirms in place, reverts after 1800ms, and pushes no second toast", async () => {
    jest.useFakeTimers();
    try {
      const user = setupWithClipboard(writeText, { advanceTimers: jest.advanceTimersByTime });
      render(<ToastHost />);
      pushClassB({ duration: 0 });

      await user.click(copyButton());
      await flushUnderFakeTimers();

      const toast = toastEl();
      expect(within(toast).getByRole("button", { name: "Copied" })).toBeInTheDocument();
      expect(within(toast).getByRole("status")).toHaveTextContent("Message copied");
      expect(useToastStore.getState().toasts).toHaveLength(1);

      act(() => {
        jest.advanceTimersByTime(1800);
      });
      expect(within(toastEl()).getByRole("button", { name: "Copy message" })).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it("says so when the copy fails, and expands so the text can be selected (AC34)", async () => {
    // `copyText` only rejects when its execCommand fallback also throws, which
    // is what jsdom does: document.execCommand is undefined there.
    writeText.mockRejectedValue(new Error("not allowed"));
    expect(document.execCommand).toBeUndefined();

    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB();

    await user.click(copyButton());

    const toast = toastEl();
    await waitFor(() =>
      expect(within(toast).getByRole("button", { name: "Copy failed" })).toBeInTheDocument(),
    );
    expect(messageEl()).not.toHaveClass("toast__message--clamped");
    expect(within(toast).getByRole("status")).toHaveTextContent(/copy failed|could not be copied/i);
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it("leaves one status node on a resting info toast (AC35)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "info", title: "Queued", message: CLASS_B_MESSAGE, duration: 0 });
    });

    expect(screen.getByRole("status")).toBe(toastEl());
  });
});

describe("markup and tab order of the controls row (AC20, AC43)", () => {
  it("puts the row last inside the toast body, after the message", () => {
    render(<ToastHost />);
    pushClassB();

    const body = document.querySelector(".toast__body") as HTMLElement;
    expect(body.lastElementChild).toHaveClass("toast__actions");
    expect(body.lastElementChild?.previousElementSibling).toHaveClass("toast__message");
  });

  it("orders the controls Show all, Copy message, Dismiss", () => {
    render(<ToastHost />);
    pushClassB();

    const names = Array.from(toastEl().querySelectorAll("button")).map(
      (b) => b.getAttribute("aria-label") ?? b.textContent,
    );
    expect(names).toEqual(["Show all", "Copy message", "Dismiss: Refresh from target failed"]);
  });

  it("uses the shared inline-toggle treatment rather than a toast-only style", () => {
    render(<ToastHost />);
    pushClassB();

    expect(showAll()).toHaveClass("approval-inline-toggle");
    expect(screen.getByRole("button", { name: "Copy message" })).toHaveClass(
      "approval-inline-toggle",
    );
  });
});

describe("Alt+T is the keyboard route into the stack (AC22, AC39, AC40)", () => {
  const altT = () => {
    const event = new KeyboardEvent("keydown", {
      key: "†", // what macOS actually delivers for Option+T — AC22
      code: "KeyT",
      altKey: true,
      bubbles: true,
      cancelable: true,
    });
    act(() => {
      window.dispatchEvent(event);
    });
    return event;
  };

  it("advertises itself on the toast", () => {
    render(<ToastHost />);
    pushClassB();

    expect(toastEl()).toHaveAttribute("aria-keyshortcuts", "Alt+T");
  });

  it("focuses the first control of the newest toast and consumes the key", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Older", message: CLASS_B_MESSAGE, duration: 0 });
    });
    act(() => {
      pushToast({ tone: "error", title: "Newest", message: CLASS_B_MESSAGE, duration: 0 });
    });

    const event = altT();

    expect(event.defaultPrevented).toBe(true);
    const newest = document.querySelectorAll(".toast")[1];
    expect(newest.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toHaveAccessibleName("Show all");
  });

  it("falls through to Dismiss when the message is short enough to need no controls", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Save file failed", message: "worktree is dirty", duration: 0 });
    });

    altT();

    expect(document.activeElement).toHaveAccessibleName("Dismiss: Save file failed");
  });

  it("does nothing, and consumes nothing, when the stack is empty", () => {
    render(<ToastHost />);

    const event = altT();

    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(document.body);
  });

  it("ignores Alt+T carrying Ctrl or Meta", () => {
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    act(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          code: "KeyT",
          altKey: true,
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        }),
      );
    });

    expect(document.activeElement).toBe(document.body);
  });

  it("returns focus where it came from once the toast is dismissed (AC23)", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button" data-testid="origin">
          origin
        </button>
        <ToastHost />
      </>,
    );
    const origin = screen.getByTestId("origin");
    origin.focus();

    pushClassB({ duration: 0 });
    altT();
    expect(document.activeElement).not.toBe(origin);

    await user.click(screen.getByRole("button", { name: "Dismiss: Refresh from target failed" }));

    await waitFor(() => expect(document.activeElement).toBe(origin));
  });
});

describe("Escape is scoped to the toast, not the window (AC23, AC38)", () => {
  it("dismisses the toast focus is inside, and only that one", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Older", message: CLASS_B_MESSAGE, duration: 0 });
    });
    act(() => {
      pushToast({ tone: "error", title: "Newest", message: CLASS_B_MESSAGE, duration: 0 });
    });

    const newest = document.querySelectorAll(".toast")[1] as HTMLElement;
    (newest.querySelector("button") as HTMLButtonElement).focus();
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" });

    const titles = useToastStore.getState().toasts.map((t) => t.title);
    expect(titles).toEqual(["Older"]);
  });

  it("does not swallow Escape from the page behind it", () => {
    render(
      <>
        <button type="button" data-testid="behind">
          behind
        </button>
        <ToastHost />
      </>,
    );
    pushClassB({ duration: 0 });

    const behind = screen.getByTestId("behind");
    behind.focus();
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    act(() => {
      behind.dispatchEvent(event);
    });

    expect(event.defaultPrevented).toBe(false);
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });
});

describe("the dismiss timer pauses while the operator is reading (AC4, AC17, AC33)", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("holds while the pointer is over the toast, then resumes with the time that was left", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      jest.advanceTimersByTime(4000);
    });
    fireEvent.mouseEnter(toastEl());

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.mouseLeave(toastEl());

    // 5000 ms were left; the full 9000 is never restarted from scratch.
    act(() => {
      jest.advanceTimersByTime(4999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("floors the resumed wait at 3000ms so a toast never vanishes on pointer-leave", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      jest.advanceTimersByTime(8000);
    });
    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseLeave(toastEl());

    act(() => {
      jest.advanceTimersByTime(2999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("holds while focus is inside the toast (the hover rule's keyboard half)", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      (toastEl().querySelector("button") as HTMLButtonElement).focus();
    });

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("holds for as long as the message is expanded, and resumes on collapse", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    fireEvent.click(showAll());
    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    act(() => {
      jest.advanceTimersByTime(9000);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("leaves a zero-duration toast exactly as it was: no timer, no pause bookkeeping", () => {
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    fireEvent.mouseEnter(toastEl());
    fireEvent.mouseLeave(toastEl());
    act(() => {
      jest.advanceTimersByTime(600_000);
    });

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("still auto-dismisses an untouched toast at its stated duration", () => {
    render(<ToastHost />);
    pushClassB({ duration: 9000 });

    act(() => {
      jest.advanceTimersByTime(8999);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the dev-only browser harness stays out of the shipped build (AC29, AC30)", () => {
  it("exposes nothing on window when IS_DEV is false", () => {
    render(<ToastHost />);
    pushClassB();

    expect((window as unknown as Record<string, unknown>).__loregardenPushToast).toBeUndefined();
  });
});
