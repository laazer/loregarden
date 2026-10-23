import { ApiError } from "../../api/client";
import { describeError, endSentence, toastActionFailed, useToastStore } from "../toastStore";

beforeEach(() => {
  useToastStore.getState().clear();
});

it("reports an action failure with the server's detail", () => {
  toastActionFailed("Delete ticket", new ApiError(409, "ticket has an active run"));

  const [toast] = useToastStore.getState().toasts;
  expect(toast.tone).toBe("error");
  expect(toast.title).toBe("Delete ticket failed");
  expect(toast.message).toBe("ticket has an active run");
});

it("falls back to the status when the server sends no detail", () => {
  expect(describeError(new ApiError(500, ""))).toBe("Request failed (500)");
});

it("describes a thrown non-error rather than rendering nothing", () => {
  expect(describeError(undefined)).toBe("Unexpected error");
  expect(describeError("offline")).toBe("offline");
});

it("replaces a repeat of the same failure instead of stacking copies", () => {
  const first = toastActionFailed("Start run", new Error("boom"));
  const second = toastActionFailed("Start run", new Error("boom"));

  const { toasts } = useToastStore.getState();
  expect(toasts).toHaveLength(1);
  // A new id, so the auto-dismiss timer restarts: the failure is still current.
  expect(toasts[0].id).toBe(second);
  expect(second).not.toBe(first);
});

it("keeps distinct failures side by side, capped at four", () => {
  for (const n of [1, 2, 3, 4, 5]) {
    toastActionFailed(`Action ${n}`, new Error("boom"));
  }

  const titles = useToastStore.getState().toasts.map((t) => t.title);
  expect(titles).toEqual(["Action 2 failed", "Action 3 failed", "Action 4 failed", "Action 5 failed"]);
});

it("dismisses only the toast asked for", () => {
  const keep = useToastStore.getState().push({ title: "Keep" });
  const drop = useToastStore.getState().push({ title: "Drop" });

  useToastStore.getState().dismiss(drop);

  expect(useToastStore.getState().toasts.map((t) => t.id)).toEqual([keep]);
});

/**
 * lg-milestone-that-1434 AC1. `endSentence` exists because concatenating a
 * message we do not control with a sentence we do is a contract, and the two
 * live call sites both broke it: the server's detail carries no terminator (it
 * builds one line by design), so `… at line 4, column 12 Fix that file.` and
 * `Failed to fetch Nothing is known about…` both render today.
 *
 * The rule is asserted in order, and the two clauses a naive implementation
 * gets wrong are pinned separately: it must not left-trim (the detail renders
 * `pre-wrap`, so leading indentation is content) and must not collapse
 * internal whitespace (the real detail is 4-7 lines — AC8).
 */
describe("endSentence", () => {
  it("terminates a server detail that ends in no punctuation", () => {
    expect(endSentence("the document is not valid YAML at line 4, column 12")).toBe(
      "the document is not valid YAML at line 4, column 12.",
    );
    expect(endSentence("Failed to fetch")).toBe("Failed to fetch.");
  });

  it.each([
    ["Already terminated.", "a period"],
    ["Really?", "a question mark"],
    ["Stop!", "an exclamation mark"],
    ["It went on…", "an ellipsis"],
    ["2 validation errors for OrchestrationProfileModel:", "a colon"],
    ["one thing;", "a semicolon"],
  ])("leaves %s alone, because it already ends in %s", (text) => {
    expect(endSentence(text)).toBe(text);
  });

  it("right-trims before deciding, and appends only one terminator", () => {
    expect(endSentence("trailing space ")).toBe("trailing space.");
    expect(endSentence("already done.  ")).toBe("already done.");
    expect(endSentence("line\t\n ")).toBe("line.");
  });

  it("returns nothing for nothing, rather than a bare period", () => {
    expect(endSentence("")).toBe("");
    expect(endSentence("   \n\t ")).toBe("");
  });

  it("keeps the newlines and the indentation of a multi-line detail", () => {
    const detail =
      "/repo/x.yaml could not be read as an orchestration profile: 1 validation error\ngates.commands.0\n  Input should be a valid string";

    expect(endSentence(detail)).toBe(`${detail}.`);
  });

  /**
   * Adversarial. The terminator test is on the LAST character, not on whether
   * the text contains one anywhere: every realistic detail on this route has
   * internal periods (an absolute path, a version number, a pydantic docs
   * URL), so an `includes` or an unanchored regex reports every one of them as
   * already terminated and the defect ships unchanged.
   */
  it("looks at the last character only, not at punctuation anywhere in the text", () => {
    expect(endSentence("/repo/x/default.yaml could not be read")).toBe(
      "/repo/x/default.yaml could not be read.",
    );
    expect(endSentence("visit https://errors.pydantic.dev/2.11/v/string_type")).toBe(
      "visit https://errors.pydantic.dev/2.11/v/string_type.",
    );
    expect(endSentence("it failed. then it failed again")).toBe("it failed. then it failed again.");
  });

  /**
   * Adversarial. The right-trim must take every kind of trailing whitespace,
   * not just spaces: a `/ +$/` implementation passes "already done.  " above
   * and then appends a second period to a detail whose last line is a bare
   * newline — which is how a multi-line server detail actually arrives.
   */
  it("right-trims newlines and tabs after a terminator, not only spaces", () => {
    expect(endSentence("terminated.\n")).toBe("terminated.");
    expect(endSentence("terminated.\t\n  ")).toBe("terminated.");
    expect(endSentence("errors for OrchestrationProfileModel:\n")).toBe(
      "errors for OrchestrationProfileModel:",
    );
  });

  /**
   * Adversarial, and the assumption AC1 states rather than proves: a detail
   * ending in a closing bracket, quote or digit is NOT treated as terminated.
   * Pinned from the real pydantic shape, whose echoed `input_value` line ends
   * in `]`, so a reviewer who disagrees has a named case to contradict.
   */
  it.each([
    ["input_value=['ruff', 'check'], input_type=list]", "a closing bracket"],
    ['the key "gates" is not a mapping"', "a quote"],
    ["expected ',' or ']'", "a quoted bracket"],
    ["at line 4, column 12", "a digit"],
  ])("terminates %s, which ends in %s", (text) => {
    expect(endSentence(text)).toBe(`${text}.`);
  });

  /** Adversarial. Applying it twice must change nothing — both call sites may. */
  it("is idempotent over every shape above", () => {
    for (const text of [
      "unterminated",
      "terminated.",
      "trailing space ",
      "",
      "   ",
      "a\nb",
      "ends in a colon:",
    ]) {
      expect(endSentence(endSentence(text))).toBe(endSentence(text));
    }
  });

  it("does not left-trim: a pre-wrap detail's leading indentation is content", () => {
    expect(endSentence("  indented")).toBe("  indented.");
    expect(endSentence("\n  indented.")).toBe("\n  indented.");
  });
});
