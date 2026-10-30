/**
 * S7 — "it failed" and "it worked" must not collapse into one answer.
 *
 * `copyText` threw `document.execCommand("copy")`'s boolean away, so a browser
 * that REFUSES the write resolved as a success: `CopyValueButton` showed
 * "Copied", no toast was pushed, and the operator's clipboard held whatever it
 * held before. That is the silent failure CLAUDE.md's rule is about, and it is
 * also what makes UX9 — "a clipboard refusal is reported" — unreachable in a
 * real browser, where a refusal is `false` rather than a throw.
 */

import { copyText } from "../clipboard";

const writeText = jest.fn<Promise<void>, [string]>();

function setExecCommand(value: unknown) {
  Object.defineProperty(document, "execCommand", { value, configurable: true });
}

/** The textareas the fallback appends; none may survive the call. */
function strayTextareas(): HTMLTextAreaElement[] {
  return Array.from(document.body.querySelectorAll("textarea"));
}

beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
});

afterEach(() => {
  for (const stray of strayTextareas()) stray.remove();
});

it("S7.2 — resolves when the async clipboard API took the write", async () => {
  writeText.mockResolvedValue(undefined);

  await expect(copyText("a path")).resolves.toBeUndefined();

  expect(writeText).toHaveBeenCalledWith("a path");
  expect(strayTextareas()).toHaveLength(0);
});

it("S7.2 — resolves when the textarea fallback reported a copy", async () => {
  writeText.mockRejectedValue(new Error("Document is not focused"));
  setExecCommand(() => true);

  await expect(copyText("a path")).resolves.toBeUndefined();

  expect(strayTextareas()).toHaveLength(0);
});

it("S7.1 — rejects when execCommand REFUSED the write by returning false", async () => {
  // The real-browser shape of a refusal, and the one the old code read as a
  // success. A throw was already handled; `false` was not.
  writeText.mockRejectedValue(new Error("Document is not focused"));
  setExecCommand(() => false);

  await expect(copyText("a path")).rejects.toThrow(Error);
});

it("S7.1 — rejects when execCommand is absent altogether", async () => {
  writeText.mockRejectedValue(new Error("Document is not focused"));
  setExecCommand(undefined);

  await expect(copyText("a path")).rejects.toThrow(Error);
});

it("S7.1 — rejects when execCommand throws", async () => {
  writeText.mockRejectedValue(new Error("Document is not focused"));
  setExecCommand(() => {
    throw new Error("copy is not supported");
  });

  await expect(copyText("a path")).rejects.toThrow(Error);
});

it("S7.1 — names the refusal rather than rejecting with nothing readable", async () => {
  // `describeError` is what renders this, and an Error with an empty message
  // falls through to its name — so the toast would say "Error" and name no
  // surface. The reason has to be on the rejection.
  writeText.mockRejectedValue(new Error("Document is not focused"));
  setExecCommand(() => false);

  await expect(copyText("a path")).rejects.toThrow(/clipboard|copy/i);
});

it("S7.1 — removes its textarea on every branch, including the refusals", async () => {
  writeText.mockRejectedValue(new Error("Document is not focused"));

  for (const execCommand of [
    () => false,
    undefined,
    () => {
      throw new Error("copy is not supported");
    },
  ]) {
    setExecCommand(execCommand);
    await expect(copyText("a path")).rejects.toThrow(Error);
    expect(strayTextareas()).toHaveLength(0);
  }
});

it("S7.1 — does not reach the fallback at all when the async API worked", async () => {
  const execCommand = jest.fn(() => true);
  writeText.mockResolvedValue(undefined);
  setExecCommand(execCommand);

  await copyText("a path");

  expect(execCommand).not.toHaveBeenCalled();
});

describe("the clipboard the browser does not give us at all", () => {
  // `navigator.clipboard` is undefined on an insecure origin, not merely
  // rejecting — so the property access itself throws a TypeError inside the
  // same try. Every case above defines it, which leaves the branch the
  // fallback exists for untested: a developer running the app over plain HTTP,
  // or any WebView that withholds the async API.
  function removeClipboard() {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
  }

  it("S7.2 — falls through to the textarea and resolves when that worked", async () => {
    removeClipboard();
    setExecCommand(() => true);

    await expect(copyText("a path")).resolves.toBeUndefined();

    expect(writeText).not.toHaveBeenCalled();
    expect(strayTextareas()).toHaveLength(0);
  });

  it("S7.1 — rejects, and leaves no textarea, when that is refused too", async () => {
    removeClipboard();
    setExecCommand(() => false);

    await expect(copyText("a path")).rejects.toThrow(/clipboard|copy/i);

    expect(strayTextareas()).toHaveLength(0);
  });
});

it("S7.1 — a refusal names the clipboard rather than the value it was given", async () => {
  // `describeError` puts this string in a toast. The message the operator reads
  // must not be the path they were trying to copy: a rejection carrying the
  // value reads as though the value were the problem, and an 86-character path
  // in a toast title is its own defect.
  writeText.mockRejectedValue(new Error("Document is not focused"));
  setExecCommand(() => false);

  const thrown: unknown = await copyText("/Users/op/.worktrees/chat-a0a1f1c1").catch(
    (error: unknown) => error,
  );

  expect(thrown).toBeInstanceOf(Error);
  expect((thrown as Error).message).not.toContain("/Users/op");
});
