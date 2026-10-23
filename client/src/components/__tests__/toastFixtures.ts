/**
 * Shared fixtures and query helpers for the ToastHost suites.
 *
 * lg-milestone-that-1316 is measured at the live maximum volume (AC11), and the
 * two suites that assert on it — `ToastHost.test.tsx` and
 * `ToastHostRecovery.test.tsx` — must be measured on the *same* string, or a
 * byte-for-byte assertion in one and a length assertion in the other can drift
 * apart silently. This module is the single definition of those numbers.
 *
 * Not a suite: `jest.config.cjs` collects `__tests__/**\/*.(test|spec).(ts|tsx)`
 * only, so this file is imported, never run.
 */

import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { type ToastInput, pushToast } from "../../state/toastStore";

/** AC11's live maximum, rebuilt from its parts so the numbers stay checkable. */
export const LONGEST_EXTERNAL_ID = "lg-milestone-that-12345678901";
export const LONGEST_WORKTREE_PATH =
  "/Users/jacobbrandt/workspace/loregarden/.claude/worktrees/.worktrees/ticket-01-milestone-bootstrap-lg-bootst-e41e9bb5";

/**
 * Input class B of `describe_handoff_failure` at the live maximum volume: a
 * 29-character external id plus the 117-character longest worktree path, 269
 * characters. This is the string the ticket was measured on (AC26); a fixture
 * path shorter than 88 characters does not exercise the clamp (AC5).
 */
export const CLASS_B_MESSAGE =
  `${LONGEST_EXTERNAL_ID}: this ticket has no target branch to be brought up to, so ` +
  `nothing was merged — run the stage supervised. The worktree is ${LONGEST_WORKTREE_PATH}.`;

/** AC19b — long enough to clamp, and carrying no separator to break on. */
export const LONG_MESSAGE_WITHOUT_PATH =
  "Conditions cannot be auto-fixed: stage thrash, an emptied group and a stale cursor all " +
  "landed in the same reconcile pass, so the supervisor stopped before writing anything back " +
  "and is waiting for a person to say which of them it should trust.";

export const ZWSP = "​";

/** AC36 / AC18 — the threshold, restated here so a change to it fails a test. */
export const LONG_TOAST_MESSAGE_CHARS = 160;
export const SHORT_MESSAGE = "w".repeat(LONG_TOAST_MESSAGE_CHARS);
export const JUST_OVER_THRESHOLD = "w".repeat(LONG_TOAST_MESSAGE_CHARS + 1);

export const messageEl = () => document.querySelector(".toast__message") as HTMLElement | null;
export const toastEl = () => document.querySelector(".toast") as HTMLElement;
export const showAll = () => screen.getByRole("button", { name: "Show all" });
export const copyButton = () => screen.getByRole("button", { name: /^(Copy message|Copied|Copy failed)$/ });

/**
 * Flush a settled promise under fake timers.
 *
 * React 19 schedules its commit through the host scheduler, which fake timers
 * freeze, so a state update made in a `.then` after the click's act scope has
 * closed is not in the DOM yet when the next line reads it.
 */
export async function flushUnderFakeTimers() {
  // Three passes: the copy chain is then -> finally, each a separate microtask
  // turn, and React needs a scheduler tick after each to commit.
  for (let pass = 0; pass < 3; pass += 1) {
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
  }
}

/**
 * `userEvent.setup()` installs its own `navigator.clipboard` stub (so that
 * `user.copy()` works), which silently replaces a mock defined before it —
 * `copyText` then writes to userEvent's stub and the assertion sees no call.
 * The mock has to go in after setup, which is what this exists to guarantee.
 */
export function setupWithClipboard(
  writeText: jest.Mock,
  options?: Parameters<typeof userEvent.setup>[0],
) {
  const user = userEvent.setup(options);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return user;
}

export function pushClassB(overrides: Partial<ToastInput> = {}) {
  act(() => {
    pushToast({ tone: "error", title: "Refresh from target failed", message: CLASS_B_MESSAGE, ...overrides });
  });
}

export const toastEls = () => Array.from(document.querySelectorAll(".toast")) as HTMLElement[];
export const messageIn = (toast: HTMLElement) =>
  toast.querySelector(".toast__message") as HTMLElement;

/**
 * AC6 / AC16 — the lead with the validator's own message appended behind it,
 * past any finite clamp, which is the case the clamp height cannot fix.
 */
export const COMPOSED_MESSAGE =
  `${CLASS_B_MESSAGE} Conditions cannot be auto-fixed: stage_thrash. ` +
  `Auto-fixable: emptied_group, stale_cursor.`;

/* ------------------------------------------------------------------------- *
 * AC47 / AC50 — the segments a path token must be rendered in.
 *
 * Written from the criterion rather than imported from `toastWrap`, so the
 * component suites check the rendered DOM against the spec and not against the
 * module they are rendering. If both drift the same way, these fail.
 * ------------------------------------------------------------------------- */

/** Split one token after every `/`; the separator stays with its segment. */
export function segmentsOf(token: string): string[] {
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

/** A *path token* is a whitespace-delimited token containing `/` (AC47). */
export function pathTokensOf(message: string): string[] {
  return message.split(/\s+/).filter((token) => token !== "" && token.includes("/"));
}

/** Every `.toast__path-part` the message must produce, in message order. */
export function expectedPathSegments(message: string): string[] {
  return pathTokensOf(message).flatMap(segmentsOf);
}

/**
 * AC52 — the `<wbr/>` count: per path token, its segment count minus one.
 *
 * A token that ends in `/` is the one case worth saying out loud: its last
 * segment carries the separator, and there is no part after it inside the
 * token, so no break opportunity dangles off the end of a path.
 */
export function expectedWbrCount(message: string): number {
  return pathTokensOf(message).reduce((total, token) => total + segmentsOf(token).length - 1, 0);
}

export const pathPartsIn = (node: HTMLElement) =>
  Array.from(node.querySelectorAll(".toast__path-part")) as HTMLElement[];

/* ------------------------------------------------------------------------- *
 * test-break — the measured message classes AC47 and AC58 name that nothing
 * rendered yet.
 *
 * AC58 makes "a collapsed last visible line that ends mid-identifier for any of
 * AC47's six measured classes" the one surviving reroute condition on the break
 * axis, so all six have to be reachable in jsdom. Four were: class B at the live
 * maximum, the composed message, a long message with no separator, and a short
 * one. The two below are the pair that was only ever measured in a browser.
 * ------------------------------------------------------------------------- */

/**
 * This ticket's own worktree, read off `git worktree list --porcelain` on
 * 2026-10-20: 88 characters, which is also the live median.
 *
 * AC58 records this class at 233 characters. It is **240** here, because the
 * worktree directory name it was measured against ended `-dfa5f489` and this
 * branch's checkout ends `-044eba29` — the suffix is 8 hex either way, so the
 * difference is the slug, not the measurement. Re-measured rather than copied
 * forward, which AC53 and AC59 both ask for; nothing in the design turns on the
 * count, and the assertions below are on the segments, not the length.
 */
export const OWN_WORKTREE_PATH =
  "/Users/jacobbrandt/workspace/.worktrees/ticket-loregarden-lg-milestone-that-131-044eba29";

/** Class B naming this ticket's own worktree — the case that failed at implement. */
export const CLASS_B_OWN_WORKTREE_MESSAGE =
  `${LONGEST_EXTERNAL_ID}: this ticket has no target branch to be brought up to, so ` +
  `nothing was merged — run the stage supervised. The worktree is ${OWN_WORKTREE_PATH}.`;

/**
 * Input class D of `describe_handoff_failure` — `conflict_lead`, built from the
 * byte-pinned wording in `server/tests/handoff_lead_helpers.py`, at 263
 * characters (AC47, AC58).
 *
 * The one class that names **two** tokens containing a separator: the target
 * branch as well as the worktree. So it is also the case that catches a split
 * keyed on "the path is the absolute one" or "the path is last" — the branch is
 * neither, and AC47's rule is "a whitespace-delimited token containing `/`".
 */
export const CONFLICT_TARGET_BRANCH = "integration/lg-milestone-that-1316-fixup";
export const CLASS_D_MESSAGE =
  `${LONGEST_EXTERNAL_ID}: resolve the conflict with ${CONFLICT_TARGET_BRANCH}, or run ` +
  `the stage supervised — the worktree is ${LONGEST_WORKTREE_PATH}.`;
