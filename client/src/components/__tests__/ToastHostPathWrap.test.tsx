/**
 * lg-milestone-that-1316 AC3/AC13/AC47/AC51 — a path in a toast is never broken
 * mid-identifier.
 *
 * Restored from 1316's landing (`9b4974188`), which its integration branch lost
 * before the tree was published (lg-milestone-that-2622). Only the path-wrap
 * half is restored: the clamp, expand and keyboard affordances 1316 also built
 * conflict with lg-external-harness-954's fence (`toastCopyStylesheets.test.ts`
 * S3.6) and that fence stands, so the 4-line clamp and the copy control are the
 * route to the full text and these cases assert none of 1316's controls.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, render, screen } from "@testing-library/react";

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
  ZWSP,
  expectedPathSegments,
  expectedWbrCount,
  messageEl,
  pathPartsIn,
  pushClassB,
} from "./toastFixtures";

beforeEach(() => {
  useToastStore.getState().clear();
});

describe("the measurements this suite is built on (AC11)", () => {
  it("are the ones recorded on the ticket", () => {
    expect(LONGEST_EXTERNAL_ID).toHaveLength(29);
    expect(LONGEST_WORKTREE_PATH).toHaveLength(117);
    expect(CLASS_B_MESSAGE).toHaveLength(269);
    expect(CLASS_D_MESSAGE).toHaveLength(263);
    expect(OWN_WORKTREE_PATH).toHaveLength(88);
    expect(CLASS_B_OWN_WORKTREE_MESSAGE).toHaveLength(240);
  });
});

describe("the path is never broken mid-identifier (AC3, AC13, AC47, AC51)", () => {
  it("announces the message byte for byte, with no zero-width space anywhere", () => {
    render(<ToastHost />);
    pushClassB();

    expect(messageEl()?.textContent).toBe(CLASS_B_MESSAGE);
    expect(screen.getByRole("alert").textContent).toContain(CLASS_B_MESSAGE);
    expect(screen.getByRole("alert").textContent).not.toContain(ZWSP);
  });

  it("wraps each path segment in its own unbreakable span, and nothing else", () => {
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    const elements = Array.from(node.querySelectorAll("*"));
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(
      expectedPathSegments(CLASS_B_MESSAGE),
    );
    expect(pathPartsIn(node)).toHaveLength(9);
    // Prose is a bare text node; the only elements are segment spans and the
    // break opportunities between them, all direct children of the message.
    expect(new Set(elements.map((el) => el.tagName))).toEqual(new Set(["SPAN", "WBR"]));
    expect(elements.every((el) => el.parentElement === node)).toBe(true);
    for (const span of pathPartsIn(node)) expect(span.textContent).not.toMatch(/\s/);
  });

  it("puts a break opportunity after each separator inside a path, and nowhere else", () => {
    render(<ToastHost />);
    pushClassB();

    const node = messageEl() as HTMLElement;
    const wbrs = Array.from(node.querySelectorAll("wbr"));
    expect(wbrs).toHaveLength(expectedWbrCount(CLASS_B_MESSAGE));
    expect(wbrs).toHaveLength(8);
    for (const wbr of wbrs) {
      expect(wbr.previousSibling?.textContent?.endsWith("/")).toBe(true);
      expect((wbr.previousSibling as Element | null)?.classList).toContain("toast__path-part");
      expect((wbr.nextSibling as Element | null)?.classList).toContain("toast__path-part");
    }
  });

  it("leaves a message that names no path entirely unwrapped", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Handoff refused", message: LONG_MESSAGE_WITHOUT_PATH });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(LONG_MESSAGE_WITHOUT_PATH);
    expect(node.querySelectorAll("*")).toHaveLength(0);
  });

  it("leaves no break opportunity dangling after a trailing separator", () => {
    const trailing = `${LONGEST_EXTERNAL_ID}: nothing was merged. The worktree directory is ${LONGEST_WORKTREE_PATH}/`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh failed", message: trailing });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(trailing);
    expect((node.lastChild as Element | null)?.tagName).not.toBe("WBR");
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(trailing));
    expect(pathPartsIn(node).at(-1)?.textContent).toBe(
      "ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5/",
    );
  });

  it("puts no opportunity between a path's last segment and the prose after it", () => {
    const trailingThenProse =
      `${LONGEST_EXTERNAL_ID}: nothing was merged, so the directory ${OWN_WORKTREE_PATH}/ ` +
      `is still on disk and the stage has to be run supervised by a person instead.`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh failed", message: trailingThenProse });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(trailingThenProse);
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(trailingThenProse));
  });

  it("keeps each path token separate when a message names two", () => {
    const two = `${LONGEST_EXTERNAL_ID}: two worktrees were left behind, ${LONGEST_WORKTREE_PATH} and /tmp/loregarden/stale, so nothing was cleaned up by the run.`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Cleanup failed", message: two });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(two);
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(expectedPathSegments(two));
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(two));
  });

  it("preserves newlines, tabs and runs of spaces", () => {
    const awkward =
      `${LONGEST_EXTERNAL_ID}: nothing was merged.\n\tThe run left two directories behind,   ` +
      `${LONGEST_WORKTREE_PATH}  \nand its sibling.`;
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Handoff refused", message: awkward });
    });

    expect(messageEl()?.textContent).toBe(awkward);
  });
});

describe("the measured message classes (AC47, AC52, AC58)", () => {
  const cases: Array<[string, string]> = [
    ["class D, the conflict refusal at 263 characters", CLASS_D_MESSAGE],
    ["class B naming this ticket's own worktree", CLASS_B_OWN_WORKTREE_MESSAGE],
    ["the composed message, with the validator's sentence behind the path", COMPOSED_MESSAGE],
  ];

  test.each(cases)("%s renders every path segment whole", (_label, message) => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh from target failed", message, duration: 0 });
    });

    const node = messageEl() as HTMLElement;
    expect(node.textContent).toBe(message);
    expect(pathPartsIn(node).map((el) => el.textContent)).toEqual(expectedPathSegments(message));
    expect(node.querySelectorAll("wbr")).toHaveLength(expectedWbrCount(message));
    for (const span of pathPartsIn(node)) expect(span.textContent).not.toMatch(/\s/);
  });

  it("treats class D's target branch as a path token of its own", () => {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Refresh from target failed", message: CLASS_D_MESSAGE, duration: 0 });
    });

    const texts = pathPartsIn(messageEl() as HTMLElement).map((el) => el.textContent);
    expect(texts.slice(0, 2)).toEqual(["integration/", `${CONFLICT_TARGET_BRANCH.split("/")[1]},`]);
    expect(texts.at(-1)).toBe("ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5.");
  });

  it("hands the copy control the store's string, not the rendered parts", () => {
    render(<ToastHost />);
    pushClassB();

    // The copy control (lg-external-harness-1203) is built from `toast.message`;
    // the spans and `<wbr>`s must not have changed what it is given.
    expect(screen.getByRole("button", { name: /^Copy / })).toBeInTheDocument();
    expect(messageEl()?.textContent).toBe(CLASS_B_MESSAGE);
  });
});

describe("the stylesheet makes each segment unbreakable (AC46)", () => {
  const css = readFileSync(resolve(__dirname, "../ToastHost.css"), "utf8").replace(
    /\/\*[\s\S]*?\*\//g,
    "",
  );

  it("gives .toast__path-part white-space: nowrap", () => {
    const rule = css.match(/\.toast__path-part\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).toMatch(/white-space:\s*nowrap/);
  });
});
