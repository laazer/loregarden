/**
 * lg-milestone-that-1316 AC50 / AC13 — the break-point split is a pure module.
 *
 * `splitMessageForWrap` exists so `ToastHost` can make each `/`-separated
 * segment of a path unbreakable (`.toast__path-part`, AC47/AC48) and give the
 * line breaker an opportunity between segments (`<wbr/>`, AC51), without
 * touching the text. The one property that makes that safe is join-identity:
 * the parts, concatenated, are the original message byte for byte. If it ever
 * drops or duplicates a character, the clipboard payload (AC14) and the
 * `aria-live` announcement (AC26) both ship a corrupted path — the exact
 * defect this ticket exists to fix, reintroduced one layer down.
 *
 * Supersedes the split-after-every-slash cases this file carried at 78b70b56
 * (AC55a, AC57): a break opportunity after every `/` in the whole message was
 * the earlier design, and prose is no longer split at all.
 */

import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { splitMessageForWrap } from "../toastWrap";

/** AC11/AC59's live maximum, rebuilt from its parts so the numbers stay checkable. */
const LONGEST_EXTERNAL_ID = "lg-milestone-that-12345678901";
const LONGEST_WORKTREE_PATH =
  "/Users/jacobbrandt/workspace/loregarden/.claude/worktrees/.worktrees/ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5";

/** Input class B of `describe_handoff_failure`, at the live maximum volume. */
const CLASS_B_MESSAGE =
  `${LONGEST_EXTERNAL_ID}: this ticket has no target branch to be brought up to, so ` +
  `nothing was merged — run the stage supervised. The worktree is ${LONGEST_WORKTREE_PATH}.`;

/** AC6 / AC16 / AC50 — the validator's own message appended behind the lead. */
const VALIDATOR_SENTENCE =
  " Conditions cannot be auto-fixed: stage_thrash. Auto-fixable: emptied_group, stale_cursor.";
const COMPOSED_MESSAGE = `${CLASS_B_MESSAGE}${VALIDATOR_SENTENCE}`;

const ZWSP = "​";

type Part = { text: string; nowrap: boolean };

/* ------------------------------------------------------------------------- *
 * The oracle.
 *
 * Written from AC50's definition rather than from the implementation, so it
 * constrains the contract and not a shape: a *path token* is a
 * whitespace-delimited token of the message that contains `/`, and its parts
 * are its `/`-segments, each segment carrying its own trailing separator. The
 * oracle says nothing about how the prose between path tokens is cut up,
 * because nothing renders differently either way (AC51: a part without
 * `nowrap` is a bare text node) — so it checks the prose by concatenation.
 * ------------------------------------------------------------------------- */

/** Split one token after every `/`; the separator stays with its segment. */
function segmentsOf(token: string): string[] {
  const parts: string[] = [];
  let start = 0;
  for (let i = 0; i < token.length; i += 1) {
    if (token[i] === "/") {
      parts.push(token.slice(start, i + 1));
      start = i + 1;
    }
  }
  if (start < token.length) parts.push(token.slice(start));
  return parts;
}

function pathTokens(message: string): string[] {
  return message.split(/\s+/).filter((token) => token !== "" && token.includes("/"));
}

/** Every nowrap part the message must produce, in message order. */
function expectedNowrapTexts(message: string): string[] {
  return pathTokens(message).flatMap(segmentsOf);
}

/** Everything that is not inside a path token, concatenated. */
function expectedProse(message: string): string {
  let rest = message;
  for (const token of pathTokens(message)) {
    const at = rest.indexOf(token);
    rest = rest.slice(0, at) + rest.slice(at + token.length);
  }
  return rest;
}

const nowrapTexts = (parts: Part[]) => parts.filter((p) => p.nowrap).map((p) => p.text);
const proseText = (parts: Part[]) =>
  parts
    .filter((p) => !p.nowrap)
    .map((p) => p.text)
    .join("");

it("the AC11/AC59 measurements this file is built on are the ones recorded", () => {
  expect(LONGEST_EXTERNAL_ID).toHaveLength(29);
  expect(LONGEST_WORKTREE_PATH).toHaveLength(117);
  expect(CLASS_B_MESSAGE).toHaveLength(269);
  expect(COMPOSED_MESSAGE.length).toBeGreaterThan(320);
});

/* ------------------------------------------------------------------------- *
 * AC50's three required properties, over AC31's inherited case list plus the
 * composed message AC50 adds.
 * ------------------------------------------------------------------------- */

const cases: Array<[string, string]> = [
  ["the empty message", ""],
  ["a message with no separator at all", "worktree is dirty"],
  ["the 269-character class-B handoff refusal", CLASS_B_MESSAGE],
  ["the composed message no finite clamp can hold", COMPOSED_MESSAGE],
  ["two separators in a row", "a//b"],
  ["a lone separator", "/"],
  ["a message ending in a separator", "/Users/jacobbrandt/workspace/"],
  ["a message that is only separators", "///"],
  ["a bare absolute path", LONGEST_WORKTREE_PATH],
  ["a separator at the very start", "/leading"],
  ["an astral character beside a separator", "/🙂/tail"],
  ["a path between two sentences", "Left behind /tmp/a/b and nothing else."],
  ["two path tokens in one message", "Compare /a/b with /c/d please."],
];

describe("splitMessageForWrap keeps every character (AC50, AC31)", () => {
  test.each(cases)("join-identity holds for %s", (_label, message) => {
    const parts: Part[] = splitMessageForWrap(message);
    expect(parts.map((p) => p.text).join("")).toBe(message);
  });

  test.each(cases)("no part is empty except on the empty message, for %s", (_label, message) => {
    const parts: Part[] = splitMessageForWrap(message);
    if (message === "") {
      expect(parts.map((p) => p.text).join("")).toBe("");
      return;
    }
    expect(parts.every((part) => part.text.length > 0)).toBe(true);
  });

  test.each(cases)("nowrap marks exactly the path segments of %s", (_label, message) => {
    const parts: Part[] = splitMessageForWrap(message);
    expect(nowrapTexts(parts)).toEqual(expectedNowrapTexts(message));
    expect(proseText(parts)).toBe(expectedProse(message));
  });

  test.each(cases)("no nowrap part carries whitespace, for %s", (_label, message) => {
    for (const text of nowrapTexts(splitMessageForWrap(message))) {
      expect(text).not.toMatch(/\s/);
    }
  });

  test.each(cases)(
    "every nowrap part but the last of its token ends with a separator, for %s",
    (_label, message) => {
      const parts: Part[] = splitMessageForWrap(message);
      let index = 0;
      for (const token of pathTokens(message)) {
        const count = segmentsOf(token).length;
        const taken = nowrapTexts(parts).slice(index, index + count);
        for (const text of taken.slice(0, -1)) expect(text.endsWith("/")).toBe(true);
        index += count;
      }
    },
  );

  it("never introduces a zero-width space (AC13)", () => {
    for (const [, message] of cases) {
      expect(splitMessageForWrap(message).map((p) => p.text).join("")).not.toContain(ZWSP);
    }
  });
});

describe("a path token is cut at its separators and nowhere else (AC47, AC50)", () => {
  it("keeps the class-B path whole, segment by segment", () => {
    const parts: Part[] = splitMessageForWrap(CLASS_B_MESSAGE);
    expect(nowrapTexts(parts)).toEqual([
      "/",
      "Users/",
      "jacobbrandt/",
      "workspace/",
      "loregarden/",
      ".claude/",
      "worktrees/",
      ".worktrees/",
      // The sentence period is inside the whitespace-delimited token, which is
      // how the path was measured (AC50) — it is not stripped off.
      "ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5.",
    ]);
  });

  it("marks none of the prose, however long (AC19b)", () => {
    const prose =
      "Conditions cannot be auto-fixed: stage thrash, an emptied group and a stale cursor " +
      "all landed in the same reconcile pass, so the supervisor stopped.";
    expect(nowrapTexts(splitMessageForWrap(prose))).toEqual([]);
  });

  it("returns the appended validator sentence as non-nowrap parts (AC50)", () => {
    const parts: Part[] = splitMessageForWrap(COMPOSED_MESSAGE);
    const lastPath = parts.map((p) => p.nowrap).lastIndexOf(true);
    const trailing = parts.slice(lastPath + 1);

    expect(trailing.length).toBeGreaterThan(0);
    expect(trailing.every((part) => !part.nowrap)).toBe(true);
    expect(trailing.map((part) => part.text).join("")).toBe(VALIDATOR_SENTENCE);
  });

  it("marks a token that merely contains a separator, not only an absolute path", () => {
    // `client/src` is a path the operator opens too; AC50's rule is "contains
    // a separator", not "starts with one".
    expect(nowrapTexts(splitMessageForWrap("see client/src/lib now"))).toEqual([
      "client/",
      "src/",
      "lib",
    ]);
  });

  it("does not let a path token swallow the space before or after it", () => {
    const parts: Part[] = splitMessageForWrap("Left behind /tmp/a and nothing else.");
    expect(nowrapTexts(parts)).toEqual(["/", "tmp/", "a"]);
    expect(proseText(parts)).toBe("Left behind  and nothing else.");
  });
});

describe("the modules that render a path carry no invisible characters (AC13, AC29)", () => {
  const read = (relative: string) => readFileSync(resolve(__dirname, relative), "utf8");

  it("toastWrap.ts contains no U+200B", () => {
    expect(read("../toastWrap.ts")).not.toContain(ZWSP);
  });

  it("ToastHost.tsx contains no U+200B", () => {
    expect(read("../../components/ToastHost.tsx")).not.toContain(ZWSP);
  });

  it("ToastHost.tsx never reads import.meta (AC29)", () => {
    expect(read("../../components/ToastHost.tsx")).not.toMatch(/import\s*\.\s*meta/);
  });

  it("keeps no alias or second code path for the superseded split (AC55a, AC57)", () => {
    // Assembled rather than written out, so this file is not itself a hit for
    // the `grep -rn` AC57 requires to come back empty.
    const superseded = `split${"After"}Slashes`;
    const root = resolve(__dirname, "../..");
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = resolve(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(entry.name)) continue;
        if (readFileSync(full, "utf8").includes(superseded)) {
          hits.push(full.slice(root.length + 1));
        }
      }
    };
    walk(root);

    expect(hits).toEqual([]);
  });
});

/* ------------------------------------------------------------------------- *
 * test-break — adversarial coverage for the split, re-cut onto AC50.
 *
 * The properties above are checked against a hand-picked list, and a
 * hand-picked list is a sample. The implementations that satisfy a sample
 * while corrupting a path are specific and known: a `String.replace` whose
 * replacement string interprets `$&`, `$'` or a backreference; a `split("/")`
 * that re-appends the separator and emits a trailing empty part; a recursive
 * walk that overflows on a long path. Each gets an input below.
 * ------------------------------------------------------------------------- */

describe("a replacement-string implementation is not good enough (AC50)", () => {
  // `"$&"`, `"$'"`, "$`" and `"$1"` are substitution patterns inside the
  // *replacement* argument of String.replace. A split built that way silently
  // rewrites them — in a shell path, into something the operator cannot paste.
  const injections = [
    "/Users/$&/workspace/run",
    "/Users/$'/workspace/run",
    "/Users/$`/workspace/run",
    "/Users/$1/workspace/$2",
    "/Users/$$/workspace/run",
    "/Users/a\\/b/workspace",
  ];

  test.each(injections)("keeps %s byte for byte", (message) => {
    const parts: Part[] = splitMessageForWrap(message);
    expect(parts.map((p) => p.text).join("")).toBe(message);
    expect(nowrapTexts(parts)).toEqual(expectedNowrapTexts(message));
  });
});

describe("whitespace and line breaks are carried through untouched (AC13)", () => {
  const whitespace = [
    "  /Users/jacobbrandt  ",
    "\t/Users/jacobbrandt\t",
    "line one\n/Users/jacobbrandt\nline three",
    "a\r\n/b",
    "/   /   /",
    " ",
    " /tmp/a ",
  ];

  test.each(whitespace)("does not trim or collapse %j", (message) => {
    const parts: Part[] = splitMessageForWrap(message);
    expect(parts.map((p) => p.text).join("")).toBe(message);
    // Whitespace is a token boundary, so it can never land inside a span.
    for (const text of nowrapTexts(parts)) expect(text).not.toMatch(/\s/);
  });

  it("treats three separators split by spaces as three separate path tokens", () => {
    expect(nowrapTexts(splitMessageForWrap("/   /   /"))).toEqual(["/", "/", "/"]);
  });
});

describe("the split is stable and total (AC50, AC51)", () => {
  /** Deterministic LCG: a fuzz failure reproduces from the seed, every run. */
  function makeRandom(seed: number): () => number {
    let state = seed >>> 0;
    return () => {
      state = (state * 1664525 + 1013904223) >>> 0;
      return state / 0x1_0000_0000;
    };
  }

  // Weighted toward "/" so most inputs are separator-dense, and carrying the
  // characters that break a naive implementation: replacement patterns, an
  // escape, an astral pair, a newline, and whitespace so token boundaries get
  // exercised rather than assumed away.
  const ALPHABET = [
    "/", "/", "/", "/",
    "a", "Z", "9", "-", ".", "_", ":",
    " ", " ", "\n", "\t",
    "$", "&", "`", "'", "\\",
    "🙂", "é",
  ];

  it("holds every property over 500 pseudo-random messages (seed 20261020)", () => {
    const random = makeRandom(20_261_020);
    for (let i = 0; i < 500; i += 1) {
      const length = Math.floor(random() * 40);
      let message = "";
      for (let c = 0; c < length; c += 1) {
        message += ALPHABET[Math.floor(random() * ALPHABET.length)];
      }

      const parts: Part[] = splitMessageForWrap(message);
      // `message` is in every failure message so a fuzz red is reproducible.
      expect({ message, joined: parts.map((p) => p.text).join("") }).toEqual({
        message,
        joined: message,
      });
      expect({ message, nowrap: nowrapTexts(parts) }).toEqual({
        message,
        nowrap: expectedNowrapTexts(message),
      });
      expect({ message, prose: proseText(parts) }).toEqual({
        message,
        prose: expectedProse(message),
      });
      if (message !== "") {
        expect({ message, empty: parts.filter((part) => part.text === "").length }).toEqual({
          message,
          empty: 0,
        });
      }
    }
  });

  it("is idempotent: splitting the rejoined parts gives the same parts", () => {
    for (const message of [CLASS_B_MESSAGE, COMPOSED_MESSAGE, LONGEST_WORKTREE_PATH, "a//b", "///", "/"]) {
      const once = splitMessageForWrap(message);
      expect(splitMessageForWrap(once.map((p) => p.text).join(""))).toEqual(once);
    }
  });

  it("survives a pathological message without recursing to death", () => {
    // 20k separators in one token. A recursive or quadratic-concat
    // implementation either throws RangeError here or takes long enough to
    // notice.
    const pathological = "/x".repeat(20_000);
    const parts: Part[] = splitMessageForWrap(pathological);
    expect(parts.map((p) => p.text).join("")).toBe(pathological);
    // 20k separators, each followed by an "x": 20k segments ending in "/"
    // plus the trailing "x".
    expect(parts.filter((p) => p.nowrap)).toHaveLength(20_001);
  });

  it("handles a message that is one unbroken 269-character token", () => {
    const token = "w".repeat(269);
    const parts: Part[] = splitMessageForWrap(token);
    expect(parts.map((p) => p.text).join("")).toBe(token);
    expect(nowrapTexts(parts)).toEqual([]);
  });
});

describe("the dev-only harness and the typing rules around it (AC29, AC30, AC34)", () => {
  const readClient = (relative: string) => readFileSync(resolve(__dirname, relative), "utf8");

  const host = () => readClient("../../components/ToastHost.tsx");

  it("reads IS_DEV from the one module allowed to touch import.meta (AC29)", () => {
    expect(host()).toMatch(/import\s*\{[^}]*\bIS_DEV\b[^}]*\}\s*from\s*["'][^"']*viteEnv["']/);
  });

  it("gates the window hook on IS_DEV and removes it again (AC30)", () => {
    const source = host();
    expect(source).toContain("__loregardenPushToast");
    // The cleanup half is the one with no behavioural reach in jsdom: without a
    // delete, a dev session accumulates a stale pushToast across remounts.
    expect(source).toMatch(/delete\s+[^;\n]*__loregardenPushToast/);
    expect(source).toContain("declare global");
  });

  it("types the hook without a suppression (AC30)", () => {
    const source = host();
    expect(source).not.toMatch(/\bas\s+any\b/);
    expect(source).not.toContain("@ts-ignore");
    expect(source).not.toContain("@ts-expect-error");
  });

  it("never logs a failure to the console instead of showing it (AC34)", () => {
    expect(host()).not.toContain("console.error");
    expect(host()).not.toContain("console.warn");
  });

  it("is referenced nowhere else in the client (AC30)", () => {
    const root = resolve(__dirname, "../..");
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = resolve(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(entry.name)) continue;
        if (/__tests__/.test(full)) continue;
        if (readFileSync(full, "utf8").includes("__loregardenPushToast")) {
          hits.push(full.slice(root.length + 1));
        }
      }
    };
    walk(root);

    expect(hits).toEqual(["components/ToastHost.tsx"]);
  });
});
