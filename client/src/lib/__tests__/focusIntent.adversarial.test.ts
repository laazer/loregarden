import {
  FOCUS_INTENT_MS,
  focusOnOperatorIntent,
  isIdleFocus,
  recordFocusIntent,
} from "../focusIntent";

/**
 * The permission rule under adversarial input — the cases
 * `focusIntent.test.ts` leaves open, for lg-milestone-that-2205.
 *
 * That suite covers each clause of AC-S2 once. This one attacks the clauses
 * that only have meaning *in combination*: the order the steps run in, the
 * single-slot store two activations share, the boundaries of the key and the
 * clock, and whether the two permission arms are really disjoint or merely
 * sequential. Every case here is written to die against a specific wrong
 * implementation that passes the whole of `focusIntent.test.ts`:
 *
 * | Case | Wrong implementation it kills |
 * |---|---|
 * | consumes before it checks the target | the target guard hoisted above the clear, so a declined attempt leaves the stamp for the next read to honour |
 * | the single slot | a store keyed by `key` (a Map), so a second activation does not invalidate the first |
 * | arm (b) survives a dead stamp | `if (stampValid) {…} else return false`, which makes the arms sequential and breaks every cold mount that happens to carry a stale stamp |
 * | an empty-string key | `if (stamp?.key && stamp.key === key)`, a truthiness guard |
 * | a backwards clock | `Math.abs(now() - at)`, or a two-sided window |
 * | re-arming refreshes the clock | a store that keeps the first stamp's timestamp |
 *
 * Determinism: the rule reads `document.activeElement` and `Date.now()` and
 * holds exactly one mutable slot, so the only ordering hazard is that slot
 * leaking between cases. The `afterEach` drain below is the same one
 * `focusIntent.test.ts` uses, and for the same two reasons: it clears the slot,
 * and it only *can* clear it if the clear really is unconditional.
 */

function focusable(id: string): HTMLElement {
  const element = document.createElement("a");
  element.id = id;
  element.setAttribute("href", "#");
  document.body.append(element);
  return element;
}

function target(id: string): { element: HTMLElement; focus: jest.SpyInstance } {
  const element = document.createElement("h2");
  element.id = id;
  element.tabIndex = -1;
  document.body.append(element);
  // Inert, so a permitted move does not itself change `document.activeElement`
  // and silently satisfy or break the next assertion in the same case.
  return { element, focus: jest.spyOn(element, "focus").mockImplementation(() => {}) };
}

afterEach(() => {
  focusOnOperatorIntent("drain", null);
  document.body.innerHTML = "";
  jest.restoreAllMocks();
});

describe("the step order: the stamp is consumed before the target is looked at", () => {
  it("spends the stamp on an attempt whose target had not mounted yet", () => {
    // AC-S2 fixes the order: read and clear the stamp, *then* return false for a
    // null target. An implementation that checks the target first would defer
    // the decline, and the operator's next successful read — a background
    // refetch they did not ask for — would find a live stamp and take focus.
    // That is the ticket's own defect wearing the fix's clothes.
    const row = focusable("rl-source-abc");
    row.focus();
    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("abc", null)).toBe(false);

    const heading = target("rl-detail-heading");
    row.focus();
    expect(focusOnOperatorIntent("abc", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
  });

  it("spends the stamp on an attempt whose target had already left the tree", () => {
    const row = focusable("rl-source-abc");
    const detached = target("rl-detail-heading");
    detached.element.remove();
    row.focus();
    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("abc", detached.element)).toBe(false);

    const live = target("rl-detail-heading-live");
    row.focus();
    expect(focusOnOperatorIntent("abc", live.element)).toBe(false);
    expect(live.focus).not.toHaveBeenCalled();
  });
});

describe("one slot: two activations cannot both be outstanding", () => {
  it("replaces the first stamp, so the source the operator asked for second is the one honoured", () => {
    // The operator clicks a row, then clicks another before either fetch lands.
    // Only one of the two panels may take focus, and it must be the second —
    // the source they are now looking at.
    const first = focusable("rl-source-abc");
    const second = focusable("rl-source-xyz");
    const secondHeading = target("rl-detail-heading-xyz");

    first.focus();
    recordFocusIntent("abc", first);
    second.focus();
    recordFocusIntent("xyz", second);

    expect(focusOnOperatorIntent("xyz", secondHeading.element)).toBe(true);
    expect(secondHeading.focus).toHaveBeenCalledTimes(1);
  });

  it("declines the abandoned first source even while its own activation link still holds focus", () => {
    // The hazard a per-key store would hide: both stamps live, both panels
    // mount, and whichever resolves first takes the focus. With one slot the
    // first source is already unreachable.
    const first = focusable("rl-source-abc");
    const second = focusable("rl-source-xyz");
    const firstHeading = target("rl-detail-heading-abc");

    first.focus();
    recordFocusIntent("abc", first);
    recordFocusIntent("xyz", second);
    // Focus is still the operator's own first link, so arm (b) cannot rescue it.
    expect(document.activeElement).toBe(first);

    expect(focusOnOperatorIntent("abc", firstHeading.element)).toBe(false);
    expect(firstHeading.focus).not.toHaveBeenCalled();
  });

  it("re-arming the same key refreshes its clock rather than keeping the first stamp", () => {
    const clock = jest.spyOn(Date, "now");
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();

    clock.mockReturnValue(1_000_000);
    recordFocusIntent("abc", row);
    // The operator clicks the same row again, well past the first stamp's
    // window. The second click is a fresh ask, not a stale one.
    clock.mockReturnValue(1_000_000 + FOCUS_INTENT_MS * 4);
    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });
});

describe("the two arms are disjoint, not sequential", () => {
  it("permits on arm (b) even though a stamp existed and failed arm (a)", () => {
    // The combination nothing in `focusIntent.test.ts` reaches: a stale or
    // mismatched stamp *and* an idle document. Arm (b) owes nothing to arm (a),
    // so the cold mount is permitted and the dead stamp is merely consumed. An
    // implementation that returns early on a failed arm (a) breaks every cold
    // deep link that happens to follow an abandoned activation.
    const clock = jest.spyOn(Date, "now");
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    clock.mockReturnValue(1_000_000);
    recordFocusIntent("abc", row);
    clock.mockReturnValue(1_000_000 + FOCUS_INTENT_MS + 1);
    row.blur();
    expect(isIdleFocus()).toBe(true);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });

  it("permits on arm (b) with a stamp naming a different key", () => {
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);
    row.blur();

    expect(focusOnOperatorIntent("xyz", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });

  it("permits on arm (b) when the arming element has been removed from the tree", () => {
    // A row list that re-rendered away under the operator: the `<a>` that armed
    // the intent is gone, the browser left focus on `<body>`, and nothing of
    // theirs is taken by the move.
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);
    row.remove();
    expect(isIdleFocus()).toBe(true);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });
});

describe("boundaries of the key and the clock", () => {
  it("honours an empty-string key like any other, rather than reading it as no stamp", () => {
    // `sourceId` is a UUID in the app, so this is a guard against the *shape* of
    // the implementation rather than a reachable state: a truthiness test on
    // `stamp.key` instead of a comparison declines here and nowhere else, which
    // is the kind of bug that surfaces only once a caller passes a falsy id.
    const row = focusable("rl-source-empty");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("", row);

    expect(focusOnOperatorIntent("", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });

  it("does not let an empty-string key match a real one", () => {
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();
    recordFocusIntent("abc", row);

    expect(focusOnOperatorIntent("", heading.element)).toBe(false);
    expect(heading.focus).not.toHaveBeenCalled();
  });

  it("permits when the clock has gone backwards since the stamp", () => {
    // The window is one-sided by construction (`now() - at <= FOCUS_INTENT_MS`),
    // so a clock that stepped back — an NTP correction mid-fetch — reads as
    // negative elapsed and is permitted. That is the right way round: the cost
    // of permitting is a focus move the operator did ask for, and the cost of
    // declining is the main path silently failing on a machine whose clock
    // moved. An implementation reaching for `Math.abs` gets this backwards.
    const clock = jest.spyOn(Date, "now");
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    row.focus();

    clock.mockReturnValue(1_000_000);
    recordFocusIntent("abc", row);
    clock.mockReturnValue(1_000_000 - 60_000);

    expect(focusOnOperatorIntent("abc", heading.element)).toBe(true);
    expect(heading.focus).toHaveBeenCalledTimes(1);
  });
});

describe("determinism", () => {
  it("gives the same answer for the same state, ten times over", () => {
    // The rule holds mutable module state, so "it worked once" and "it works"
    // are different claims. Re-arming each round is the point: the slot is
    // spent every time and must be reusable every time.
    const row = focusable("rl-source-abc");
    const heading = target("rl-detail-heading");
    const honoured: boolean[] = [];
    const repeated: boolean[] = [];

    for (let round = 0; round < 10; round += 1) {
      row.focus();
      recordFocusIntent("abc", row);
      honoured.push(focusOnOperatorIntent("abc", heading.element));
      row.focus();
      repeated.push(focusOnOperatorIntent("abc", heading.element));
    }

    expect(honoured).toEqual(Array(10).fill(true));
    expect(repeated).toEqual(Array(10).fill(false));
    expect(heading.focus).toHaveBeenCalledTimes(10);
  });
});
