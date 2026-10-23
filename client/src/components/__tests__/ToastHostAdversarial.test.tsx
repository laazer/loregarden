/**
 * S3, S4, S5 and S7 under the mutations the per-criterion cases leave open.
 *
 * Each case here is a wrong implementation that passes every stated criterion:
 *
 * - `copyText(toast.message.trim())` passes S3.3, because the pinned 239-char
 *   refusal has no leading or trailing whitespace. A refusal that does is a
 *   server-composed string away, and a trimmed path is a path the operator's
 *   shell takes and then cannot find.
 * - Pushing the "Couldn't copy" toast is allowed to move focus, because S5.3
 *   only ever copies successfully and S7.5 never looks at focus. The operator
 *   who hit a refusal is the one who most needs their place kept.
 * - An exemption for the toast under the pointer satisfies S7.5 more
 *   comfortably than the cap does, and S7.6 forbids exactly that while
 *   asserting nothing about it.
 * - Recording the opener on EVERY focusin passes S5.4, which enters the toast
 *   once.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "fs";
import path from "path";

import { pushToast, useToastStore } from "../../state/toastStore";
import { focusIn, pointerIn } from "../../test/hoverAndFocus";
import { installPointerEvents } from "../../test/pointerEvents";
import { ToastHost } from "../ToastHost";

installPointerEvents();

const writeText = jest.fn<Promise<void>, [string]>();

beforeEach(() => {
  jest.clearAllMocks();
  writeText.mockResolvedValue(undefined);
  useToastStore.getState().clear();
});

/** `userEvent.setup()` installs a clipboard of its own, so ours comes after. */
function setupUser() {
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return user;
}

const copyButton = (title: string) => screen.getByRole("button", { name: `Copy message: ${title}` });

describe("S3.3 — verbatim means verbatim", () => {
  it("copies a message whose own edges are whitespace, untouched", async () => {
    // The assertion the pinned fixture cannot make. `describeError` hands over
    // whatever the server sent; nothing between it and the clipboard is
    // entitled to normalise it, and a path that lost a trailing space or a
    // newline is a path that resolves to nothing.
    const user = setupUser();
    const message = "  /Users/op/.worktrees/chat-a0a1f1c1  \n  Commit or discard it.  ";
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Delete session failed", message });
    });

    await user.click(copyButton("Delete session failed"));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toBe(message);
  });

  it("copies the message and not the title, the glyph or the rendered text", async () => {
    // `.textContent` of the toast is title + message, and reads correct in a
    // render assertion while handing the operator a string their shell cannot
    // use. The value is the message alone.
    const user = setupUser();
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Delete session failed", message: "/Users/op/worktree" });
    });

    await user.click(copyButton("Delete session failed"));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toBe("/Users/op/worktree");
  });
});

describe("S5 — a clipboard refusal keeps the operator's place", () => {
  it("S5.1/S7.5 — the refusal toast appearing does not take focus off the copy button", async () => {
    // S5.3 is asserted on the path where the copy worked. The path where it did
    // not mounts a second toast, and a toast that takes focus on mount would
    // take it from the control the operator is about to press again.
    const user = setupUser();
    writeText.mockRejectedValue(new Error("Document is not focused"));
    Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });

    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Delete session failed", message: "/Users/op/worktree" });
    });

    const copy = copyButton("Delete session failed");
    await user.click(copy);

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(2));
    expect(document.activeElement).toBe(copy);
  });
});

describe("S5.4 — the opener is the first one, not the latest", () => {
  it("restores focus to where focus first came from, after a second entry", async () => {
    // "the element that held focus at the FIRST focusin arriving from outside
    // the toast" (S5.4). An implementation that overwrites on every focusin
    // sends the operator back to whatever they touched last, which after a
    // round trip through another control is not where they were working.
    const user = setupUser();
    render(
      <>
        <button type="button">Delete chat</button>
        <button type="button">Something else</button>
        <ToastHost />
      </>,
    );
    act(() => {
      pushToast({ tone: "error", title: "Delete session failed", message: "/Users/op/worktree" });
    });

    const opener = screen.getByRole("button", { name: "Delete chat" });
    const other = screen.getByRole("button", { name: "Something else" });
    const copy = copyButton("Delete session failed");

    act(() => opener.focus());
    act(() => copy.focus());
    act(() => other.focus());
    act(() => copy.focus());

    await user.click(screen.getByRole("button", { name: "Dismiss: Delete session failed" }));

    expect(opener).toHaveFocus();
  });
});

describe("S7.6/UX14 — the cap is not coded around", () => {
  it("evicts the oldest toast even while the pointer is on it", () => {
    // Stated as an accepted limit, which means an implementation that quietly
    // exempts the hovered toast is wrong in the direction nobody checks: the
    // eviction order would start depending on where the mouse is, and the
    // newest failure — the one tied to what the operator just did — would be
    // the one dropped.
    render(<ToastHost />);
    act(() => {
      for (const n of [1, 2, 3, 4]) {
        pushToast({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
      }
    });
    const oldest = screen.getByText("detail 1").closest(".toast")!;
    act(() => pointerIn(oldest));
    act(() => focusIn(screen.getByRole("button", { name: "Copy message: Action 1 failed" })));

    act(() => {
      pushToast({ tone: "error", title: "Couldn't copy message: Action 1 failed", message: "refused" });
    });

    const titles = useToastStore.getState().toasts.map((t) => t.title);
    expect(titles).toEqual([
      "Action 2 failed",
      "Action 3 failed",
      "Action 4 failed",
      "Couldn't copy message: Action 1 failed",
    ]);
    expect(screen.queryByText("detail 1")).not.toBeInTheDocument();
  });
});

describe("the seams, read off the source", () => {
  const SRC = path.resolve(__dirname, "../..");

  function source(file: string): string {
    return fs
      .readFileSync(path.join(SRC, file), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");
  }

  it("S1.9 — the host subscribes to no focus or pointer event on the document", () => {
    // The hook's own source is already fenced. The escape hatch the fence does
    // not cover is putting the listener in the host instead, which `ToastHost`
    // is one `window.addEventListener("resize")` away from looking normal with.
    const host = source("components/ToastHost.tsx");
    for (const escape of ["focusin", "focusout", "pointerover", "pointerout", ":focus-within"]) {
      expect(host).not.toContain(escape);
    }
    expect(host.match(/addEventListener\((["'])([a-z]+)\1/g) ?? []).toEqual([
      'addEventListener("resize"',
    ]);
  });

  it("S5.2 — no key handler is added to the toast or its host", () => {
    const host = source("components/ToastHost.tsx");
    expect(host).not.toMatch(/onKeyDown|onKeyUp|onKeyPress/);
  });

  it("S2.3 — queryClient is the only caller that passes a duration to toastActionFailed", () => {
    // The optional third argument exists for UX5's one exception. Reaching for
    // it elsewhere is how a sticky error toast quietly becomes a 9-second one
    // again, one call site at a time, with every stated criterion still green.
    //
    // The arguments are read with a paren scan rather than a regex: a regex
    // bounded by `;` runs past a call that ends a block without one, and one
    // bounded by `)` stops inside `describeError(error)`.
    function callsWithThreeArguments(text: string): number {
      let found = 0;
      for (const match of text.matchAll(/\btoastActionFailed\(/g)) {
        let depth = 1;
        let commas = 0;
        for (let i = match.index! + match[0].length; i < text.length && depth > 0; i += 1) {
          const char = text[i];
          // String and template spans are opaque: a title reads
          // `Added ${name}, but saving its gates` and its comma is prose, not
          // an argument separator. Counting it reported a second caller.
          if (char === '"' || char === "'" || char === "`") {
            i += 1;
            while (i < text.length && text[i] !== char) i += text[i] === "\\" ? 2 : 1;
            continue;
          }
          if ("([{".includes(char)) depth += 1;
          else if (")]}".includes(char)) depth -= 1;
          else if (char === "," && depth === 1) commas += 1;
        }
        if (commas >= 2) found += 1;
      }
      return found;
    }

    const callers: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        const full = path.join(dir, entry.name);
        const relative = path.relative(SRC, full);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__" && entry.name !== "test") walk(full);
          continue;
        }
        if (!/\.tsx?$/.test(entry.name)) continue;
        // The declaration itself has three parameters; it is not a call site.
        if (relative === "state/toastStore.ts") continue;
        if (callsWithThreeArguments(source(relative)) > 0) callers.push(relative);
      }
    };
    walk(SRC);

    expect(callers).toEqual(["api/queryClient.ts"]);
  });
});
