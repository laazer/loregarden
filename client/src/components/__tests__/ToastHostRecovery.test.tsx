import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

import {
  CLASS_B_MESSAGE,
  CLASS_B_OWN_WORKTREE_MESSAGE,
  CLASS_D_MESSAGE,
  COMPOSED_MESSAGE,
  CONFLICT_TARGET_BRANCH,
  LONGEST_EXTERNAL_ID,
  LONGEST_WORKTREE_PATH,
  LONG_MESSAGE_WITHOUT_PATH,
  OWN_WORKTREE_PATH,
  copyButton,
  expectedPathSegments,
  expectedWbrCount,
  flushUnderFakeTimers,
  messageEl,
  messageIn,
  pathPartsIn,
  pushClassB,
  setupWithClipboard,
  showAll,
  toastEl,
  toastEls,
} from "./toastFixtures";

beforeEach(() => {
  useToastStore.getState().clear();
});

/* ------------------------------------------------------------------------- *
 * test-break — adversarial coverage.
 *
 * What the cases above leave open, and why each gap matters:
 *
 *  - The stylesheet had no assertion at all. `.toast__message`'s declaration
 *    block *is* the defect (AC42), and jsdom applies no CSS, so the only way to
 *    pin it in Jest is to read the file. Without this, an implementation that
 *    adds the modifier class and leaves `overflow-wrap: anywhere` in place
 *    passes every behavioural case and ships the mid-token break.
 *  - Expansion and copy feedback were only ever exercised on a stack of one.
 *    A single `expanded` boolean hoisted into `ToastHost` satisfies all of
 *    them and expands all four toasts at once.
 *  - The pause had one reason outstanding at a time. AC33's "never left with
 *    both a running timer and a pause reason outstanding" is only tested by
 *    overlapping two and releasing one.
 *  - AC21's in-flight `disabled` had no case, and AC39's two negative branches
 *    (focus moved somewhere real, stored element gone) had none either.
 * ------------------------------------------------------------------------- */



describe("the stylesheet the defect lives in (AC12, AC25, AC42, AC43)", () => {
  const css = () =>
    readFileSync(resolve(__dirname, "../ToastHost.css"), "utf8").replace(/\s+/g, " ");

  /** The body of one rule, whitespace-collapsed, or null if it is not there. */
  const block = (selector: string): string | null => {
    const match = css().match(
      new RegExp(`${selector.replace(/[.-]/g, "\\$&")}\\s*\\{([^}]*)\\}`),
    );
    return match ? match[1].trim() : null;
  };

  it("has dropped the two declarations that cut the path (AC42)", () => {
    const base = block(".toast__message");
    expect(base).not.toBeNull();
    expect(base).not.toMatch(/overflow-wrap:\s*anywhere/);
    expect(base).not.toMatch(/max-height:\s*5\.8em/);
    expect(css()).not.toMatch(/overflow-wrap:\s*anywhere/);
  });

  it("lets the base block scroll instead of hiding (AC16, AC42)", () => {
    const base = block(".toast__message");
    expect(base).toMatch(/word-break:\s*normal/);
    expect(base).toMatch(/overflow-wrap:\s*break-word/);
    expect(base).toMatch(/max-height:\s*50vh/);
    expect(base).toMatch(/overflow-y:\s*auto/);
  });

  it("expresses the clamp as four lines with a real ellipsis (AC12, AC42)", () => {
    const clamped = block(".toast__message--clamped");
    expect(clamped).not.toBeNull();
    expect(clamped).toMatch(/display:\s*-webkit-box/);
    expect(clamped).toMatch(/-webkit-box-orient:\s*vertical/);
    expect(clamped).toMatch(/-webkit-line-clamp:\s*4/);
    expect(clamped).toMatch(/[^-]line-clamp:\s*4/);
    expect(clamped).toMatch(/overflow:\s*hidden/);
    // Without this the base 50vh competes with the line bound.
    expect(clamped).toMatch(/max-height:\s*none/);
  });

  it("keeps the bound at four lines, not some other number (AC6, AC16)", () => {
    expect(css()).not.toMatch(/line-clamp:\s*(?!4\b)\d/);
  });

  it("makes a path segment unbreakable, with its own last-resort ellipsis (AC48)", () => {
    const part = block(".toast__path-part");
    expect(part).not.toBeNull();
    // The one declaration that suppresses the hyphen soft-wrap AC46 measured.
    expect(part).toMatch(/white-space:\s*nowrap/);
    // The remaining four are the last resort AC15 asked for and could not get
    // from `break-word`: a segment wider than the line box shrinks to it and
    // renders its own ellipsis instead of clipping silently off the edge.
    expect(part).toMatch(/display:\s*inline-block/);
    expect(part).toMatch(/max-width:\s*100%/);
    expect(part).toMatch(/overflow:\s*hidden/);
    expect(part).toMatch(/text-overflow:\s*ellipsis/);
    expect(part).toMatch(/vertical-align:\s*bottom/);
  });

  it("does not reach for a global break rule instead (AC46, AC47, AC55f)", () => {
    const sheet = css();
    // AC46 measured `word-break: keep-all` and `overflow-wrap: normal` as
    // no-ops against the hyphen break, so neither may appear as the fix.
    expect(sheet).not.toMatch(/word-break:\s*keep-all/);
    expect(block(".toast__message")).not.toMatch(/overflow-wrap:\s*normal/);
    // The toast stays 380px and the message stays 12px: no global change.
    expect(block(".toast-host")).toMatch(/width:\s*min\(380px/);
    expect(block(".toast__message")).toMatch(/font-size:\s*12px/);
  });

  it("styles the controls row and nothing more (AC43)", () => {
    const actions = block(".toast__actions");
    expect(actions).not.toBeNull();
    expect(actions).toMatch(/display:\s*flex/);
    expect(actions).toMatch(/gap:\s*10px/);
    expect(actions).toMatch(/margin-top:\s*6px/);
    // The button treatment is reused from .approval-inline-toggle, not
    // reinvented here as a toast-only style.
    expect(css()).not.toMatch(/\.toast__actions\s+button/);
  });

  it("adds no motion and no theme escape hatch (AC25, ts-theme)", () => {
    const sheet = css();
    expect(sheet).not.toContain("@keyframes");
    expect(sheet).not.toContain("prefers-color-scheme");
    expect(block(".toast__message")).not.toMatch(/transition|animation/);
    expect(block(".toast__message--clamped")).not.toMatch(/transition|animation/);
    expect(block(".toast__actions")).not.toMatch(/transition|animation/);
    // A duration literal anywhere in the file would be a new hardcoded token.
    expect(sheet).not.toMatch(/transition[^;]*\d+m?s/);
  });
});

describe("the message node holds text, segment spans and break opportunities (AC49, AC51)", () => {
  it("wraps path segments and nothing else (AC47, AC51)", () => {
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    const elements = Array.from(node.querySelectorAll("*"));
    // 9 segments of the one path token, 8 break opportunities between them,
    // and no wrapper at all around the prose (AC51: a part without `nowrap`
    // is a bare text node).
    expect(elements.filter((el) => el.tagName === "SPAN")).toHaveLength(9);
    expect(elements.filter((el) => el.tagName === "WBR")).toHaveLength(8);
    expect(new Set(elements.map((el) => el.tagName))).toEqual(new Set(["SPAN", "WBR"]));
    expect(elements.every((el) => el.parentElement === node)).toBe(true);
    for (const el of elements) {
      if (el.tagName === "SPAN") expect(el).toHaveClass("toast__path-part");
    }
  });

  it("puts a break opportunity between segments, never inside one (AC51)", () => {
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    for (const wbr of Array.from(node.querySelectorAll("wbr"))) {
      // Every opportunity follows a segment that carries its own separator,
      // so a break lands on "/" and the next segment starts the next line.
      expect(wbr.previousSibling?.textContent?.endsWith("/")).toBe(true);
    }
  });

  it("keeps the children byte-identical across the expand (AC16, AC49)", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    const before = node.innerHTML;

    await user.click(showAll());

    expect(messageEl()).toBe(node);
    expect(node.innerHTML).toBe(before);
    expect(node.textContent).toBe(CLASS_B_MESSAGE);
    expect(node.querySelectorAll("wbr")).toHaveLength(8);
    expect(pathPartsIn(node)).toHaveLength(9);
  });

  it("leaves no break opportunity dangling after a trailing separator (AC50, AC51)", () => {
    const trailing = `${LONGEST_EXTERNAL_ID}: nothing was merged. The worktree directory is ${LONGEST_WORKTREE_PATH}/`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh failed", message: trailing });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(trailing);
    // A split that emits a trailing empty part renders a wbr with nothing
    // after it, which is a break opportunity at the end of a path.
    expect((node.lastChild as Element | null)?.tagName).not.toBe("WBR");
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(trailing));
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(expectedPathSegments(trailing));
    expect(pathPartsIn(node).at(-1)?.textContent).toBe("ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5/");
  });

  it("keeps each path token separate when a message names two (AC47)", () => {
    const two = `${LONGEST_EXTERNAL_ID}: two worktrees were left behind, ${LONGEST_WORKTREE_PATH} and /tmp/loregarden/stale, so nothing was cleaned up by the run.`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Cleanup failed", message: two });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(two);
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(expectedPathSegments(two));
    // No opportunity between the two tokens: the space between them already
    // wraps, and a wbr there would be a break the prose rule already offers.
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(two));
  });

  it("preserves newlines, tabs and runs of spaces (AC13, AC26)", () => {
    const awkward =
      `${LONGEST_EXTERNAL_ID}: nothing was merged.\n\tThe run left two directories behind,   ` +
      `${LONGEST_WORKTREE_PATH}  \nand its sibling.`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Handoff refused", message: awkward });
    });

    expect(messageEl()?.textContent).toBe(awkward);
    expect(messageEl()).toHaveClass("toast__message--clamped");
  });

  it("renders the composed message that no finite clamp can hold (AC6, AC16)", () => {
    expect(COMPOSED_MESSAGE.length).toBeGreaterThan(320);
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Handoff refused", message: COMPOSED_MESSAGE });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(COMPOSED_MESSAGE);
    expect(node).toHaveClass("toast__message--clamped");
    expect(showAll()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy message" })).toBeInTheDocument();

    // AC52 names the composed message alongside class B: the validator's own
    // sentence is appended behind the path, so the path is no longer last and
    // a split that keys off "the tail is the path" is caught here.
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(
      expectedPathSegments(COMPOSED_MESSAGE),
    );
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(COMPOSED_MESSAGE));
    expect(node.textContent).not.toContain("\u200b");
  });
});

describe("state belongs to a toast, not to the stack (AC32, AC37)", () => {
  it("expands only the toast whose control was pressed", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "First", message: CLASS_B_MESSAGE, duration: 0 });
    });
    act(() => {
      pushToast({ tone: "error", title: "Second", message: LONG_MESSAGE_WITHOUT_PATH, duration: 0 });
    });

    const [first, second] = toastEls();
    await user.click(within(first).getByRole("button", { name: "Show all" }));

    expect(within(first).getByRole("button", { name: "Show less" })).toBeInTheDocument();
    expect(within(second).getByRole("button", { name: "Show all" })).toBeInTheDocument();
    expect(messageIn(first)).not.toHaveClass("toast__message--clamped");
    expect(messageIn(second)).toHaveClass("toast__message--clamped");
  });

  it("resets expansion when a repeated failure replaces its twin", async () => {
    // The store dedupes an identical tone+title+message into a fresh row with a
    // new id, so the expanded state must not survive on the id that went away.
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB({ duration: 0 });
    await user.click(showAll());
    expect(messageEl()).not.toHaveClass("toast__message--clamped");

    pushClassB({ duration: 0 });

    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(messageEl()).toHaveClass("toast__message--clamped");
    expect(showAll()).toHaveAttribute("aria-expanded", "false");
  });

  it("targets the newest toast after the store has evicted the oldest (AC39)", () => {
    render(<ToastHost />);
    for (let i = 0; i < 5; i += 1) {
      act(() => {
        pushToast({
          tone: "error",
          title: `Failure ${i}`,
          message: `${CLASS_B_MESSAGE} (${i})`,
          duration: 0,
        });
      });
    }

    // MAX_TOASTS is 4 and eviction is positional.
    expect(toastEls()).toHaveLength(4);

    act(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          code: "KeyT",
          altKey: true,
          bubbles: true,
          cancelable: true,
        }),
      );
    });

    const newest = toastEls()[3];
    expect(newest.contains(document.activeElement)).toBe(true);
    expect(within(newest).getByRole("button", { name: "Dismiss: Failure 4" })).toBeInTheDocument();
  });
});

describe("copy feedback, in flight and after (AC21, AC34, AC35)", () => {
  const writeText = jest.fn();
  let consoleError: jest.SpyInstance;

  beforeEach(() => {
    writeText.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });
    consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("disables the control while the write is in flight (AC21, AC24)", async () => {
    let settle = () => {};
    writeText.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          settle = resolve;
        }),
    );
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    await user.click(copyButton());
    expect(copyButton()).toBeDisabled();

    await act(async () => {
      settle();
    });
    await waitFor(() => expect(copyButton()).not.toBeDisabled());
  });

  it("does not let a second click write twice while the first is in flight", async () => {
    let settle = () => {};
    writeText.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          settle = resolve;
        }),
    );
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const button = copyButton();
    await user.click(button);
    await user.click(button);

    expect(writeText).toHaveBeenCalledTimes(1);
    await act(async () => {
      settle();
    });
  });

  it("renders the feedback region only while feedback is live, then unmounts it (AC35)", async () => {
    jest.useFakeTimers();
    try {
      const user = setupWithClipboard(writeText, { advanceTimers: jest.advanceTimersByTime });
      render(<ToastHost />);
      pushClassB({ duration: 0 });

      // An error toast is role=alert, so at rest there is no status node at all.
      expect(within(toastEl()).queryByRole("status")).not.toBeInTheDocument();

      await user.click(copyButton());
      await flushUnderFakeTimers();
      expect(within(toastEl()).getByRole("status")).toHaveTextContent("Message copied");

      act(() => {
        jest.advanceTimersByTime(1800);
      });
      expect(within(toastEl()).queryByRole("status")).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it("restarts the Copied window on a second copy instead of expiring early", async () => {
    jest.useFakeTimers();
    try {
      const user = setupWithClipboard(writeText, { advanceTimers: jest.advanceTimersByTime });
      render(<ToastHost />);
      pushClassB({ duration: 0 });

      await user.click(copyButton());
      await flushUnderFakeTimers();
      act(() => {
        jest.advanceTimersByTime(1000);
      });
      await user.click(copyButton());
      await flushUnderFakeTimers();

      act(() => {
        jest.advanceTimersByTime(1000);
      });
      expect(within(toastEl()).getByRole("button", { name: "Copied" })).toBeInTheDocument();
      expect(writeText).toHaveBeenCalledTimes(2);

      act(() => {
        jest.advanceTimersByTime(800);
      });
      expect(within(toastEl()).getByRole("button", { name: "Copy message" })).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it("keeps the failure's auto-expand a real expansion, not a dropped class (AC21, AC34)", async () => {
    writeText.mockRejectedValue(new Error("not allowed"));
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    await user.click(copyButton());

    const toast = toastEl();
    await waitFor(() =>
      expect(within(toast).getByRole("button", { name: "Copy failed" })).toBeInTheDocument(),
    );

    // Auto-expanding by removing the modifier alone leaves the toggle out of
    // step with what is on screen: it would still say "Show all".
    const toggle = within(toast).getByRole("button", { name: "Show less" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(messageIn(toast)).toHaveAttribute("tabindex", "0");

    await user.click(toggle);
    expect(messageIn(toast)).toHaveClass("toast__message--clamped");
  });

  it("surfaces the rejection without logging it to the console (AC34)", async () => {
    writeText.mockRejectedValue(new Error("not allowed"));
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    await user.click(copyButton());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copy failed" })).toBeInTheDocument(),
    );

    expect(consoleError).not.toHaveBeenCalled();
  });

  it("fails the same way when the clipboard API is absent entirely (AC34)", async () => {
    // An insecure origin has no navigator.clipboard at all; copyText's try
    // throws synchronously, falls back, and execCommand is undefined in jsdom.
    const user = setupWithClipboard(writeText);
    Reflect.deleteProperty(navigator, "clipboard");
    expect(document.execCommand).toBeUndefined();

    render(<ToastHost />);
    pushClassB({ duration: 0 });

    await user.click(copyButton());

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copy failed" })).toBeInTheDocument(),
    );
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it("keeps copy feedback on the toast that was copied", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "First", message: CLASS_B_MESSAGE, duration: 0 });
    });
    act(() => {
      pushToast({ tone: "error", title: "Second", message: LONG_MESSAGE_WITHOUT_PATH, duration: 0 });
    });

    const [first, second] = toastEls();
    await user.click(within(second).getByRole("button", { name: "Copy message" }));

    expect(writeText).toHaveBeenCalledWith(LONG_MESSAGE_WITHOUT_PATH);
    expect(within(second).getByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Copy message" })).toBeInTheDocument();
    expect(within(first).queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("Alt+T matches the physical key, and restores focus honestly (AC22, AC39)", () => {
  const dispatch = (init: KeyboardEventInit) => {
    const event = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      ...init,
    });
    act(() => {
      window.dispatchEvent(event);
    });
    return event;
  };

  /** Push a sticky class-B refusal and take Alt+T into it; returns its id. */
  const pushAndEnter = () => {
    let id = "";
    act(() => {
      id = pushToast({
        tone: "error",
        title: "Refresh from target failed",
        message: CLASS_B_MESSAGE,
        duration: 0,
      });
    });
    dispatch({ key: "†", code: "KeyT", altKey: true });
    return id;
  };

  it("ignores Alt plus a different physical key that reports t (AC22)", () => {
    // The defect AC22 names in HiveCssFloor: matching event.key means the
    // binding answers to whatever glyph the layout produced, and misses the key
    // the operator actually pressed.
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const event = dispatch({ key: "t", code: "KeyG", altKey: true });

    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(document.body);
  });

  it("ignores KeyT with no Alt at all", () => {
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const event = dispatch({ key: "t", code: "KeyT" });

    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(document.body);
  });

  it("does not steal focus back when the operator moved it somewhere real (AC39)", () => {
    render(
      <>
        <button type="button" data-testid="origin">
          origin
        </button>
        <button type="button" data-testid="elsewhere">
          elsewhere
        </button>
        <ToastHost />
      </>,
    );
    const origin = screen.getByTestId("origin");
    const elsewhere = screen.getByTestId("elsewhere");
    origin.focus();

    const id = pushAndEnter();
    act(() => {
      elsewhere.focus();
    });

    act(() => {
      useToastStore.getState().dismiss(id);
    });

    expect(document.activeElement).toBe(elsewhere);
  });

  it("does not reach for a stored element that has left the document (AC39)", async () => {
    // Owned by the test, not by React: the point is an element that goes away
    // while the toast is up, which a React-rendered node cannot model without
    // React trying to remove the same node twice.
    const origin = document.createElement("button");
    origin.type = "button";
    document.body.appendChild(origin);
    render(<ToastHost />);
    origin.focus();

    const id = pushAndEnter();

    act(() => {
      origin.remove();
    });
    act(() => {
      useToastStore.getState().dismiss(id);
    });

    expect(origin.isConnected).toBe(false);
    // Focus must not land on a node that has left the document: a detached
    // element cannot be read out, operated, or tabbed away from.
    await waitFor(() => expect(document.activeElement).toBe(document.body));
  });

  it("gives focus back when it tabs off the end of the stack to the body (AC23, AC39)", () => {
    // The other half of AC39's restore, and the one the dismiss path cannot
    // cover: ToastHost mounts second-to-last in AppLayout, so Dismiss is at or
    // near the last tabbable element in the document. Tabbing once more from
    // it lands on `document.body`, and without this the operator has no route
    // back to where they were — the "without a mouse" half of AC2.
    render(
      <>
        <button type="button" data-testid="origin">
          origin
        </button>
        <ToastHost />
      </>,
    );
    const origin = screen.getByTestId("origin");
    act(() => {
      origin.focus();
    });

    pushAndEnter();
    const toast = toastEl();
    const controls = toast.querySelectorAll<HTMLButtonElement>("button");
    const last = controls[controls.length - 1];
    act(() => {
      last.focus();
    });
    expect(toast.contains(document.activeElement)).toBe(true);

    // jsdom will not move focus past the end of the document for us, so the
    // focusout a real Tab would have produced is dispatched explicitly — the
    // same technique the copy-refocus cases use below. Only the event, not a
    // `document.body.focus()` beside it: jsdom dispatches focusout *before* it
    // commits `activeElement`, so a body focus here would overwrite whatever
    // the handler had just restored and the assertion would test jsdom's
    // ordering rather than the handler.
    act(() => {
      fireEvent.focusOut(last, { relatedTarget: document.body });
    });

    expect(document.activeElement).toBe(origin);
    // The toast is still up: focus left the stack, nothing was dismissed.
    expect(toastEls()).toHaveLength(1);
  });
});

describe("Escape only consumes the key on the branch that dismisses (AC38)", () => {
  it("prevents the default when it dismisses", () => {
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const control = toastEl().querySelector("button") as HTMLButtonElement;
    control.focus();
    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    act(() => {
      control.dispatchEvent(event);
    });

    expect(event.defaultPrevented).toBe(true);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("leaves every other key from inside the toast alone", () => {
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const control = toastEl().querySelector("button") as HTMLButtonElement;
    control.focus();
    // Not "Esc": React normalizes the legacy key name to "Escape", so it is
    // genuinely the dismiss key by the time a handler sees it.
    for (const key of ["a", "Enter", "Tab", "ArrowDown"]) {
      const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
      act(() => {
        control.dispatchEvent(event);
      });
      expect(event.defaultPrevented).toBe(false);
    }

    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it("catches Escape bubbling from the expanded scroll container", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    await user.click(showAll());
    const message = messageEl() as HTMLElement;
    expect(message).toHaveAttribute("tabindex", "0");

    act(() => {
      message.focus();
    });
    fireEvent.keyDown(message, { key: "Escape" });

    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});

/* ------------------------------------------------------------------------- *
 * test-break, round two — what the re-cut suites still leave open.
 *
 * The cases above are thorough on shape and thin in four places, each of which
 * a wrong implementation passes:
 *
 *  - **AC14 is not actually tested.** Every clipboard case asserts
 *    `writeText` received the store's string — but in jsdom the clamp is CSS
 *    only, so the message node's `textContent` *is* that same string. An
 *    implementation that copies `messageEl().textContent` passes all of them,
 *    and ships the truncated path in the browser the moment the clamp bites.
 *    The only way to separate the two in jsdom is to make the DOM disagree with
 *    the store on purpose.
 *  - **AC60's two named cases are absent.** AC54's focus restore is live code
 *    with no test at all, and AC60 requires one — including the half jsdom will
 *    not produce on its own, since it does not blur a button on `disabled`.
 *  - **Two of AC47's six measured classes never render.** Class D and class B
 *    naming this ticket's own worktree are the two the browser measurement
 *    turned on, and AC58 makes them a reroute condition; class D is also the
 *    only class carrying two separator-bearing tokens.
 *  - **The pause takes each reason once.** Nothing takes the same reason twice,
 *    which is how a counter-based or re-recording pause hands back wrong time.
 * ------------------------------------------------------------------------- */

describe("the clipboard payload cannot have come from the DOM (AC14, AC32)", () => {
  const writeText = jest.fn();

  beforeEach(() => {
    writeText.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  /**
   * Make every DOM-side read of the message return something else.
   *
   * `textContent` and `innerText` are prototype accessors, so an own property
   * on the node shadows them; `getSelection` covers the select-and-read
   * variant. After this, the store's string is the *only* place the full
   * message can be obtained from — which is exactly AC14's claim.
   */
  const poisonDomReads = (node: HTMLElement, replacement: string) => {
    for (const key of ["textContent", "innerText"] as const) {
      Object.defineProperty(node, key, { get: () => replacement, configurable: true });
    }
    const selection = { toString: () => replacement };
    Object.defineProperty(window, "getSelection", {
      value: () => selection,
      configurable: true,
      writable: true,
    });
  };

  it("copies the store's string when the DOM would hand back a truncated one", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const node = messageEl() as HTMLElement;
    // What the clamp shows a sighted operator in Chromium: four lines, cut.
    poisonDomReads(node, CLASS_B_MESSAGE.slice(0, 200));
    expect(node.textContent).not.toBe(CLASS_B_MESSAGE);

    await user.click(copyButton());

    expect(writeText).toHaveBeenCalledWith(CLASS_B_MESSAGE);
    expect(writeText.mock.calls[0][0]).toHaveLength(269);
  });

  it("copies the store's string when the DOM would hand back nothing at all", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    poisonDomReads(messageEl() as HTMLElement, "");

    await user.click(copyButton());

    expect(writeText).toHaveBeenCalledWith(CLASS_B_MESSAGE);
  });

  it("copies the store's string while the message is collapsed and while expanded", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });
    poisonDomReads(messageEl() as HTMLElement, "cut");

    await user.click(copyButton());
    await user.click(showAll());
    await user.click(copyButton());

    expect(writeText.mock.calls.map(([payload]) => payload)).toEqual([
      CLASS_B_MESSAGE,
      CLASS_B_MESSAGE,
    ]);
  });
});

describe("the copy control keeps its focus across the write (AC54, AC60)", () => {
  const writeText = jest.fn();
  let settle: () => void;

  beforeEach(() => {
    settle = () => {};
    writeText.mockReset().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          settle = resolve;
        }),
    );
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  /**
   * What Chrome does and jsdom does not: disabling the focused button blurs it
   * to `<body>`.
   *
   * jsdom leaves `document.activeElement` on a disabled button, so the hole
   * AC54 describes is invisible to the whole suite unless the blur is
   * reproduced here. Reproduced rather than relied on.
   */
  const blurAsChromeWould = () => {
    const control = document.activeElement as HTMLElement;
    act(() => {
      // `control.blur()` is a no-op in jsdom, which treats a disabled button as
      // unfocusable and so never moved focus off it in the first place —
      // precisely the reason this hole was invisible. Focus is moved to the
      // body the way Chrome moves it, and the focusout React would have seen is
      // dispatched with it.
      document.body.tabIndex = -1;
      document.body.focus();
      fireEvent.focusOut(control, { relatedTarget: document.body });
    });
  };

  it("gives focus back after a keyboard copy that Chrome would have blurred", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const button = copyButton();
    act(() => {
      button.focus();
    });
    await user.keyboard("{Enter}");
    expect(writeText).toHaveBeenCalledTimes(1);

    blurAsChromeWould();
    expect(document.body.contains(document.activeElement)).toBe(true);
    expect(toastEl().contains(document.activeElement)).toBe(false);

    await act(async () => {
      settle();
    });

    // Back inside the toast, so Escape still dismisses and Tab does not
    // restart at the top of the page.
    await waitFor(() => expect(toastEl().contains(document.activeElement)).toBe(true));
    expect(document.activeElement).toHaveAccessibleName(/^(Copied|Copy message)$/);
  });

  it("moves focus nowhere when the copy was started with the mouse", async () => {
    setupWithClipboard(writeText);
    render(
      <>
        <button type="button" data-testid="elsewhere">
          elsewhere
        </button>
        <ToastHost />
      </>,
    );
    pushClassB({ duration: 0 });

    const elsewhere = screen.getByTestId("elsewhere");
    act(() => {
      elsewhere.focus();
    });
    // fireEvent, not userEvent: a pointer click on a control the operator was
    // not focused on must not pull focus into the toast afterwards.
    act(() => {
      fireEvent.click(copyButton());
    });

    await act(async () => {
      settle();
    });

    expect(document.activeElement).toBe(elsewhere);
  });

  it("does not yank focus off another control the operator moved to inside the toast", async () => {
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    const button = copyButton();
    act(() => {
      button.focus();
    });
    await user.keyboard("{Enter}");

    // Focus left the copy control but is still inside the toast: nothing is
    // broken, so nothing should be taken back.
    const toggle = showAll();
    act(() => {
      toggle.focus();
    });

    await act(async () => {
      settle();
    });

    await waitFor(() => expect(copyButton()).not.toBeDisabled());
    expect(document.activeElement).toBe(toggle);
  });
});

describe("the two measured classes the browser turned on (AC47, AC58)", () => {
  const cases: Array<[string, string]> = [
    ["class D, the conflict refusal at 263 characters", CLASS_D_MESSAGE],
    ["class B naming this ticket's own worktree", CLASS_B_OWN_WORKTREE_MESSAGE],
  ];

  it("are the measurements recorded for them", () => {
    expect(CLASS_D_MESSAGE).toHaveLength(263);
    expect(OWN_WORKTREE_PATH).toHaveLength(88);
    expect(CLASS_B_OWN_WORKTREE_MESSAGE).toHaveLength(240);
    expect(CLASS_B_OWN_WORKTREE_MESSAGE.length).toBeGreaterThan(220);
  });

  test.each(cases)("%s renders every path segment whole", (_label, message) => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh from target failed", message, duration: 0 });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(message);
    expect(node).toHaveClass("toast__message--clamped");
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(expectedPathSegments(message));
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(message));
    for (const span of pathPartsIn(node)) expect(span.textContent).not.toMatch(/\s/);
  });

  test.each(cases)("%s hands the store's own string to the clipboard", async (_label, message) => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh from target failed", message, duration: 0 });
    });

    await user.click(copyButton());

    expect(writeText).toHaveBeenCalledWith(message);
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("treats class D's target branch as a path token of its own (AC47)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh from target failed", message: CLASS_D_MESSAGE, duration: 0 });
    });

    const texts = pathPartsIn(messageEl() as HTMLElement).map((el) => el.textContent);
    // The branch is neither absolute nor last in the message, so a split keyed
    // on either of those renders it as prose and lets the hyphen break cut it.
    expect(texts.slice(0, 2)).toEqual(["integration/", `${CONFLICT_TARGET_BRANCH.split("/")[1]},`]);
    expect(texts.at(-1)).toBe("ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5.");
  });

  it("keeps the composed message's children byte-identical across the expand (AC52)", async () => {
    const user = userEvent.setup();
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Handoff refused", message: COMPOSED_MESSAGE, duration: 0 });
    });

    const node = messageEl() as HTMLElement;
    const before = node.innerHTML;

    await user.click(showAll());

    expect(messageEl()).toBe(node);
    expect(node.innerHTML).toBe(before);
    expect(node.textContent).toBe(COMPOSED_MESSAGE);
  });
});

describe("assumptions the surface would otherwise inherit by default", () => {
  it("advertises Alt+T on every toast in the stack, not just the newest (AC40)", () => {
    render(<ToastHost />);
    for (const title of ["First", "Second", "Third"]) {
      act(() => {
        pushToast({ tone: "error", title, message: CLASS_B_MESSAGE, duration: 0 });
      });
    }

    const stack = toastEls();
    expect(stack).toHaveLength(3);
    for (const toast of stack) expect(toast).toHaveAttribute("aria-keyshortcuts", "Alt+T");
  });

  it("treats a negative duration exactly as a zero one (AC33)", () => {
    jest.useFakeTimers();
    try {
      render(<ToastHost />);
      pushClassB({ duration: -1 });

      fireEvent.mouseEnter(toastEl());
      fireEvent.mouseLeave(toastEl());
      act(() => {
        jest.advanceTimersByTime(600_000);
      });

      // AC33's branch is `duration <= 0`; a `=== 0` test leaves a negative
      // duration scheduling a timeout that fires immediately.
      expect(screen.getByRole("alert")).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it("renders no message node and no controls for an empty message (AC19a, AC37)", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Save file failed", message: "", duration: 0 });
    });

    expect(messageEl()).toBeNull();
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy message" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Save file failed");
  });

  it("shows no spinner and no busy state while the write is in flight (AC21, AC24)", async () => {
    const writeText = jest.fn().mockImplementation(() => new Promise<void>(() => {}));
    const user = setupWithClipboard(writeText);
    render(<ToastHost />);
    pushClassB({ duration: 0 });

    await user.click(copyButton());

    const toast = toastEl();
    expect(copyButton()).toBeDisabled();
    // AC21: the wait is under ~200ms, so the disabled control is the whole
    // in-flight affordance. A spinner or an aria-busy would be new noise on
    // every toast in the app.
    expect(within(toast).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(toast.querySelector("[aria-busy='true']")).toBeNull();
    expect(copyButton()).toHaveAccessibleName("Copy message");
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("reports success when copyText lands on its execCommand fallback (AC34, AC45c)", async () => {
    // No mock between the component and `copyText` here: the real helper runs,
    // its clipboard attempt throws because there is no API, and the hidden
    // textarea plus execCommand path is what reports back. The mock-heavy
    // cases above cannot tell this path from the primary one.
    const execCommand = jest.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
      writable: true,
    });
    try {
      const user = userEvent.setup();
      // After setup, never before: `userEvent.setup()` installs its own
      // `navigator.clipboard` stub, so deleting it first only has the stub put
      // back and the primary path succeeds — the fallback would never run and
      // the case would assert nothing.
      Reflect.deleteProperty(navigator, "clipboard");
      render(<ToastHost />);
      pushClassB({ duration: 0 });

      await user.click(screen.getByRole("button", { name: "Copy message" }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument(),
      );
      expect(execCommand).toHaveBeenCalledWith("copy");
    } finally {
      Reflect.deleteProperty(document, "execCommand");
    }
  });
});

describe("where AC51's rule and AC52's count disagree (AC51, AC52)", () => {
  /**
   * A path token that ends in a separator, with prose after it.
   *
   * AC51 reads "a `<wbr/>` between part i-1 and part i when part i-1's text
   * ends with `/`", and AC52 counts "per path token, its segment count minus
   * one". Those are the same number everywhere except here: the token's last
   * segment carries a `/`, so the literal rule also puts an opportunity
   * between the path and the prose that follows, one more than the count.
   *
   * AC51's own trailing clause settles it — "a break opportunity exists after
   * every separator and nowhere else **inside a path**" — so the opportunity
   * belongs between two segments of one token and nowhere else. Pinned here
   * rather than left to the implementer, because both readings pass every
   * other case in these suites and only one of them matches AC52.
   */
  const trailingThenProse =
    `${LONGEST_EXTERNAL_ID}: nothing was merged, so the directory ${OWN_WORKTREE_PATH}/ ` +
    `is still on disk and the stage has to be run supervised by a person instead.`;

  it("puts no opportunity between a path's last segment and the prose after it", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh failed", message: trailingThenProse, duration: 0 });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(trailingThenProse);
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(
      expectedPathSegments(trailingThenProse),
    );
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(trailingThenProse));

    // Every opportunity sits between two segments of the same token.
    for (const wbr of Array.from(node.querySelectorAll("wbr"))) {
      expect((wbr.previousSibling as Element | null)?.classList).toContain("toast__path-part");
      expect((wbr.nextSibling as Element | null)?.classList).toContain("toast__path-part");
    }
  });
});
