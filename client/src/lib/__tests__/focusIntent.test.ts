import {
  FOCUS_INTENT_MS,
  focusIfIdle,
  focusOnOperatorIntent,
  isIdleFocus,
  recordFocusIntent,
} from "../focusIntent";

/**
 * The focus-permission rule, alone — no panel, no query client, no React.
 * Covers AC-S2 of lg-milestone-that-2205.
 *
 * The rule the whole ticket turns on: a move to a surface's heading is
 * permitted when **either**
 *
 *   (a) an unexpired, unconsumed operator intent names this key, and the
 *       element that recorded it is still `document.activeElement` — the
 *       operator asked for this surface and has not since moved the keyboard;
 *   (b) `document.activeElement === document.body` — a cold deep link, a
 *       reload, a remount, a successful Retry whose own button unmounted:
 *       nothing of the operator's is taken,
 *
 * and declined otherwise. Arm (a) exists because the source list and the detail
 * panel are siblings mounted at the same time, so after a row click the row's
 * `<a>` is still mounted and still holds focus and a `<body>`-only guard would
 * decline the main path; arm (b) exists because it is the cold-mount case.
 *
 * Two things here are contracts on the implementation, not incidental:
 *
 * 1. **The clock is read through `Date.now()` at call time.** The module keeps a
 *    local `now()` indirection over `Date.now` so expiry is testable; these
 *    tests inject their clock with `jest.spyOn(Date, "now")`, which only works
 *    if the indirection calls `Date.now()` rather than capturing it at module
 *    load. That is the point of the indirection.
 * 2. **The stamp is consumed on the first attempt, honoured or declined.** Hence
 *    the `afterEach` drain below: `focusOnOperatorIntent(key, null)` reads and
 *    clears the stamp before it ever looks at the target, so it is both the
 *    cheapest way to stop one test's stamp leaking into the next *and* an
 *    assertion that clearing really is unconditional — if the module only
 *    cleared on the honoured path, the leaked stamp would fail the key-mismatch
 *    and single-consume cases below.
 */

/** A focusable element in the document, so `document.activeElement` can hold it. */
function focusable(id: string): HTMLElement {
  const element = document.createElement("a");
  element.id = id;
  element.setAttribute("href", "#");
  element.textContent = id;
  document.body.append(element);
  return element;
}

/** A target with its `focus()` spied, so a call and its arguments are both readable. */
function target(id: string): { element: HTMLElement; focus: jest.SpyInstance } {
  const element = document.createElement("h2");
  element.id = id;
  element.tabIndex = -1;
  document.body.append(element);
  return { element, focus: jest.spyOn(element, "focus") };
}

afterEach(() => {
  // Drains any stamp a test left behind; see the docstring above.
  focusOnOperatorIntent("drain", null);
  document.body.innerHTML = "";
  jest.restoreAllMocks();
});

describe("FOCUS_INTENT_MS", () => {
  it("is 5000ms, sized to outlast an api.referenceSource round trip", () => {
    // Deliberately not beluga's 800, which is sized to a requestAnimationFrame
    // retry. The cost of being too generous is nothing; the cost of expiring
    // before the fetch it waits for is a declined move on the main path.
    expect(FOCUS_INTENT_MS).toBe(5000);
  });
});

describe("focusOnOperatorIntent — arm (a), an operator intent", () => {
  it("honours a stamp whose arming element still holds focus, and returns true", () => {
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    expect(document.activeElement).toBe(row);

    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });

  it("focuses with no preventScroll, so the browser still scrolls a heading below the fold into view", () => {
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);

    focusOnOperatorIntent("abc", heading.element);

    // No options object at all: `{ preventScroll: true }` would leave the
    // operator focused on a heading they cannot see.
    expect(heading.focus.mock.calls[0]).toHaveLength(0);
  });

  it("consumes the stamp on the first attempt, so a second attempt with the same key declines", () => {
    const row = focusable("rl-source-abc");
    const first = target("rl-detail-heading");
    const second = target("rl-detail-heading-again");
    row.focus();
    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("abc", first.element)).toBe(true);
    row.focus();

    expect(focusOnOperatorIntent("abc", second.element)).toBe(false);
    expect(second.focus).not.toHaveBeenCalled();
  });

  it("declines a stamp naming a different key, and that decline consumes it too", () => {
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("xyz", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();

    // A decline is permanent, not deferred to the next read: the stamp for
    // "abc" is gone, even though nothing honoured it.
    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
  });

  it("declines when the operator moved focus after arming", () => {
    const row = focusable("rl-source-abc");
    const elsewhere = focusable("rl-search");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);
    // The realistic case: a row click, then Tab or a click away before the
    // fetch lands. The intent was real; the operator has since moved on, and
    // the focus is now theirs.
    elsewhere.focus();

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(elsewhere);
  });

  it("declines when the arming element has left the tree and focus is elsewhere", () => {
    const row = focusable("rl-source-abc");
    const elsewhere = focusable("rl-search");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);
    row.remove();
    // Focus is parked on another control of the operator's, so arm (b) cannot
    // rescue this: the only reason it could be permitted was arm (a), and the
    // element that armed it is detached.
    elsewhere.focus();

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
  });

  it("can never satisfy arm (a) from a `from: null` stamp, and falls to arm (b)", () => {
    // A site with no activation element (a programmatic navigation) passes
    // null. `document.activeElement` is never null, so arm (a) is unreachable
    // for such a stamp by construction; the site relies on arm (b).
    const elsewhere = focusable("rl-search");
    const declined = target("rl-detail-heading");
    elsewhere.focus();
    recordFocusIntent("abc", null);

    expect(focusOnOperatorIntent("abc", declined.element)).toBe(false);
    expect(declined.focus).not.toHaveBeenCalled();

    const permitted = target("rl-detail-heading-idle");
    recordFocusIntent("abc", null);
    elsewhere.blur();
    expect(document.activeElement).toBe(document.body);

    expect(focusOnOperatorIntent("abc", permitted.element)).toBe(true);
    expect(permitted.focus).toHaveBeenCalledTimes(1);
  });
});

describe("focusOnOperatorIntent — expiry", () => {
  it("permits at exactly FOCUS_INTENT_MS after the stamp", () => {
    const clock = jest.spyOn(Date, "now");
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();

    clock.mockReturnValue(1_000_000);
    recordFocusIntent("abc", row);
    clock.mockReturnValue(1_000_000 + FOCUS_INTENT_MS);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });

  it("declines one millisecond past FOCUS_INTENT_MS, leaving focus on the arming element", () => {
    const clock = jest.spyOn(Date, "now");
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();

    clock.mockReturnValue(1_000_000);
    recordFocusIntent("abc", row);
    clock.mockReturnValue(1_000_000 + FOCUS_INTENT_MS + 1);

    // The cost of expiry, stated: a declined move. Focus stays on the row link
    // the operator activated — a visible non-event, never a wrong focus.
    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(row);
  });
});

describe("focusOnOperatorIntent — arm (b) and the target", () => {
  it("permits a move when nothing of the operator's holds focus, with no stamp at all", () => {
    const heading = target("rl-detail-heading");
    expect(document.activeElement).toBe(document.body);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });

  it("declines a null target", () => {
    expect(document.activeElement).toBe(document.body);
    expect(focusOnOperatorIntent("abc", null)).toBe(false);
  });

  it("declines a target that has left the tree, even on an otherwise permitted move", () => {
    const heading = target("rl-detail-heading");
    heading.element.remove();
    expect(document.activeElement).toBe(document.body);

    // Focusing a detached node moves focus to the document rather than to the
    // heading, which is a focus taken and nothing gained.
    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
  });

  it("declines a stamp whose arming element is `document.body` itself", () => {
    const heading = target("rl-detail-heading");
    recordFocusIntent("abc", document.body);
    const elsewhere = focusable("rl-search");
    elsewhere.focus();

    // `from === document.body` is excluded from arm (a) explicitly: it would
    // otherwise make arm (a) fire on exactly the state arm (b) is for, with no
    // operator behind it.
    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
  });
});

describe("the idle predicate, shared with useRestoreFocusOnSettle", () => {
  it("reports idle only when `<body>` holds focus", () => {
    expect(isIdleFocus()).toBe(true);
    const elsewhere = focusable("rl-search");
    elsewhere.focus();
    expect(isIdleFocus()).toBe(false);
    elsewhere.blur();
    expect(isIdleFocus()).toBe(true);
  });

  it("focusIfIdle focuses an idle document and takes nothing otherwise", () => {
    const landing = target("rl-detail-heading");
    focusIfIdle(landing.element);
    expect(landing.focus).toHaveBeenCalledTimes(1);

    const elsewhere = focusable("rl-search");
    elsewhere.focus();
    focusIfIdle(landing.element);
    expect(landing.focus).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(elsewhere);
  });

  it("focusIfIdle tolerates a null or undefined target", () => {
    expect(() => focusIfIdle(null)).not.toThrow();
    expect(() => focusIfIdle(undefined)).not.toThrow();
  });
});
