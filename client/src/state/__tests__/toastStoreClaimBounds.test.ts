/**
 * test-break (lg-external-harness-1212) — the ways a bounded claim set can
 * still be wrong.
 *
 * The designed suite pins each criterion with a scripted sequence. This file
 * is the adversarial half: the mutations, interleavings and degenerate inputs
 * that satisfy every one of those scripts and still break the invariant the
 * ticket is about — *the store never holds a claim nobody is holding*.
 *
 * Three of these exist because a criterion, read literally, rewards the wrong
 * implementation:
 *
 *  - A6 asks for the claim array to be the SAME REFERENCE across repeats. The
 *    cheapest way to satisfy that everywhere is `toast.claims.push(claim)`,
 *    which mutates a snapshot a zustand selector has already rendered. Nothing
 *    in the designed suite reads an array it captured earlier, so nothing
 *    there can see it.
 *  - A5 is asserted with the claim set empty; A4 with it holding one. Neither
 *    reaches a `release` that matches a claim by prefix, by substring, or
 *    against a field that is not the claim set — which a toast carrying a
 *    claim-shaped title walks straight into.
 *  - A9 pins "a claimed push onto a toast a fire-and-forget caller raised" as
 *    UNREACHABLE, so nothing asserts what it does. The spec states the intended
 *    semantics in its assumptions; unasserted, they are free to drift before
 *    the day A9 goes red.
 */
import {
  pushClaimedToast,
  pushToast,
  releaseToast,
  toastActionFailed,
  useToastStore,
} from "../toastStore";

const store = () => useToastStore.getState();
const toasts = () => useToastStore.getState().toasts;
const ids = () => toasts().map((t) => t.id);
const allClaims = () => toasts().flatMap((t) => t.claims);

// The server-driven triple from `queueNotifications.ts`, and the one claimed
// triple in the tree (`terminalCommands.ts`).
const QUEUE_ERROR = {
  tone: "error" as const,
  title: "Queue error",
  message: "The queue reported a failure.",
  duration: 0,
};
const IN_FLIGHT = {
  tone: "info" as const,
  title: "Preparing terminal command",
  message: "bringing its worktree up to its target branch.",
  duration: 0,
};

beforeEach(() => {
  useToastStore.getState().clear();
});

describe("test-break — the claim set is state, so it may not be mutated in place", () => {
  it("a claimed push leaves an array a prior reader already holds untouched", () => {
    // A6's `Object.is` assertion is satisfied by mutating the array rather than
    // by not rebuilding it, and a mutating implementation passes every
    // assertion in the designed suite — all of which read `claims` AFTER the
    // push. zustand hands a selector the array by reference, so a mutation is
    // a rendered value changing under React with no notification: the toast
    // the operator sees and the toast the store holds stop agreeing.
    const first = pushClaimedToast({ ...IN_FLIGHT });
    const snapshot = toasts()[0].claims;
    const toastBefore = toasts()[0];
    expect(snapshot).toEqual([first]);

    const second = pushClaimedToast({ ...IN_FLIGHT });

    expect(snapshot).toEqual([first]);
    expect(toasts()[0].claims).toEqual([first, second]);
    expect(Object.is(toasts()[0].claims, snapshot)).toBe(false);
    // And the toast object itself is replaced, or a memoized row never
    // re-renders for the change.
    expect(Object.is(toasts()[0], toastBefore)).toBe(false);
  });

  it("a release leaves an array a prior reader already holds untouched", () => {
    const first = pushClaimedToast({ ...IN_FLIGHT });
    const second = pushClaimedToast({ ...IN_FLIGHT });
    const snapshot = toasts()[0].claims;

    releaseToast(first);

    expect(snapshot).toEqual([first, second]);
    expect(toasts()[0].claims).toEqual([second]);
  });

  it("two toasts never share one claim set", () => {
    // A module-level `const NO_CLAIMS: string[] = []` handed to every new
    // fire-and-forget toast is a tempting way to make A6 hold from the first
    // push. Immutably updated it is harmless; mutated — see above — it makes
    // every unclaimed toast in the stack carry the next claim minted anywhere.
    pushToast({ ...QUEUE_ERROR });
    pushToast({ tone: "error", title: "Delete ticket failed", message: "active run" });
    expect(toasts().map((t) => t.claims)).toEqual([[], []]);

    const claim = pushClaimedToast({ ...IN_FLIGHT });

    expect(toasts().map((t) => t.claims)).toEqual([[], [], [claim]]);

    releaseToast(claim);

    expect(toasts().map((t) => t.claims)).toEqual([[], []]);
    expect(toasts()).toHaveLength(2);
  });
});

describe("test-break — a release matches a claim, not a string that resembles one", () => {
  it("a toast whose own title and message are claim-shaped is not release-removable", () => {
    // `release` must look a claim up by the toast HOLDING it. An implementation
    // that searches any other field — or the serialized toast — takes a toast
    // nobody claimed, and after A1 that is every toast the 81 discarding sites
    // raise. Degenerate, but it is the input a defect of that shape needs and
    // nothing stops a message from carrying a claim id: `describeError` puts a
    // server string on screen verbatim.
    pushToast({ tone: "error", title: "claim-1", message: "claim-2" });
    toastActionFailed("Copy terminal command", new Error("claim-3 was refused"));
    const before = ids();

    for (const candidate of ["claim-1", "claim-2", "claim-3", "claim-3 was refused"]) {
      store().release(candidate);
    }

    expect(ids()).toEqual(before);
    expect(allClaims()).toEqual([]);
  });

  it("a claim is matched whole, never by prefix, suffix, case or substring", () => {
    const claim = pushClaimedToast({ ...IN_FLIGHT });
    const near = [
      claim.slice(0, -1),
      `${claim}0`,
      claim.toUpperCase(),
      claim.replace("claim-", ""),
      ` ${claim}`,
      `${claim} `,
    ];

    for (const candidate of near) store().release(candidate);

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([claim]);

    store().release(claim);

    expect(toasts()).toHaveLength(0);
  });
});

describe("test-break — the dedupe key's degenerate inputs still mint nothing", () => {
  it("an omitted, an empty and an explicitly undefined message are one toast holding nothing", () => {
    // `message` defaults to `""`, so these three are byte-identical on the
    // dedupe key. A fix applied at the `pushToast` wrapper rather than in the
    // store would be visible here, where one of the three arrives with the
    // field present and undefined.
    pushToast({ tone: "error", title: "Queue error", duration: 0 });
    pushToast({ tone: "error", title: "Queue error", message: "", duration: 0 });
    pushToast({ tone: "error", title: "Queue error", message: undefined, duration: 0 });

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([]);
  });

  it("an empty title and an empty message mint nothing, repeated", () => {
    for (let i = 0; i < 100; i += 1) pushToast({ tone: "error", title: "", message: "" });

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([]);
  });

  it("duration is not part of the key, so a repeat at a new duration still mints nothing", () => {
    const id = pushToast({ ...QUEUE_ERROR });

    for (const duration of [0, 1, 9000, 0]) {
      expect(pushToast({ ...QUEUE_ERROR, duration })).toBe(id);
    }

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].duration).toBe(0);
    expect(toasts()[0].claims).toEqual([]);
  });
});

describe("test-break — the hazard A9 pins as unreachable, asserted rather than assumed", () => {
  it("a claimed push onto a toast a fire-and-forget caller raised appends its claim", () => {
    // A9 keeps this out of the production tree by scanning for the claimed
    // title; it does not say what the store does if a later change puts it
    // there. The spec states the semantics in its assumptions, so they are
    // pinned here: the claim is appended to the EXISTING toast and returned.
    // Anything else — a second toast, or a claim returned without being
    // recorded — is a leak the A9 scan would not catch, because the scan is
    // about call sites and this is about the store.
    const id = pushToast({ ...IN_FLIGHT });
    expect(toasts()[0].claims).toEqual([]);

    const claim = pushClaimedToast({ ...IN_FLIGHT });

    expect(claim).toMatch(/^claim-/);
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].id).toBe(id);
    expect(toasts()[0].claims).toEqual([claim]);
  });

  it("and releasing it takes a toast the claimed caller did not raise — which is why A9 exists", () => {
    // Recorded deliberately as the cost of the unreachability argument: the
    // fire-and-forget caller holds no handle, so it cannot object. If A9 ever
    // goes red, this is the behaviour whoever makes it reachable has to decide
    // about, and it is written down rather than rediscovered.
    pushToast({ ...IN_FLIGHT });
    const claim = pushClaimedToast({ ...IN_FLIGHT });

    releaseToast(claim);

    expect(toasts()).toHaveLength(0);
  });
});

describe("test-break — the bound survives a flood between two claimed callers", () => {
  it("2000 fire-and-forget repeats either side of a second claim change neither claim", () => {
    // The mixed regime the designed A6 does not reach: its identity assertion
    // runs over an empty set, where a `claims: [...toast.claims]` copy is
    // caught only because a fresh `[]` is a fresh reference. Once the set is
    // non-empty the same mutant is invisible to it, and quadratic again for
    // exactly the overlapping-caller case the claim ledger exists for.
    const first = pushClaimedToast({ ...IN_FLIGHT });
    const array = toasts()[0].claims;

    for (let i = 0; i < 2000; i += 1) {
      pushToast({ ...IN_FLIGHT });
      expect(Object.is(toasts()[0].claims, array)).toBe(true);
    }

    const second = pushClaimedToast({ ...IN_FLIGHT });
    const grown = toasts()[0].claims;
    expect(grown).toEqual([first, second]);

    for (let i = 0; i < 2000; i += 1) {
      pushToast({ ...IN_FLIGHT });
      expect(Object.is(toasts()[0].claims, grown)).toBe(true);
    }

    // Released in the order the designed suite cannot use — newest first.
    releaseToast(second);
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([first]);

    releaseToast(first);
    expect(toasts()).toHaveLength(0);
  });
});

describe("test-break — nothing survives clear(), so there is no ledger beside the store", () => {
  it("the same scripted run twice in one process produces the same shape", () => {
    // The spec forbids a second owner of this state ("no new module, no second
    // store, no ledger outside the state tree"), because a ledger that
    // `clear()` does not reach becomes a second answer to "does this toast
    // still exist?". A module-level map keyed by dedupe triple is the natural
    // way to write a bound, and it survives `clear()` — so the second run of an
    // identical script would not match the first.
    const script = () => {
      const held = pushClaimedToast({ ...IN_FLIGHT });
      for (let i = 0; i < 50; i += 1) pushToast({ ...QUEUE_ERROR });
      pushToast({ tone: "warning", title: "Check tree", message: "dirty" });
      store().release(held);
      for (let i = 0; i < 50; i += 1) pushToast({ ...QUEUE_ERROR });
      return toasts().map((t) => ({
        tone: t.tone,
        title: t.title,
        message: t.message,
        duration: t.duration,
        claims: t.claims.length,
      }));
    };

    const firstRun = script();
    store().clear();
    const secondRun = script();

    expect(secondRun).toEqual(firstRun);
    expect(firstRun.every((t) => t.claims === 0)).toBe(true);
  });
});

describe("test-break — STRESS: the invariant under a seeded mixed workload", () => {
  // Deterministic by construction. A clock or `Math.random()` would make a
  // failure unreproducible, which for a defect whose whole shape is "it only
  // grows under a particular interleaving" is the difference between a bug
  // report and a guess. The seed is fixed; a failure replays exactly.
  const lcg = (seed: number) => {
    let state = seed >>> 0;
    return () => {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return state / 0x100000000;
    };
  };

  // Small enough that the dedupe branch is the common path rather than the
  // unlucky one, which is where the growth this ticket bounds happens.
  const TRIPLES = [
    QUEUE_ERROR,
    IN_FLIGHT,
    { tone: "error" as const, title: "Delete ticket failed", message: "active run" },
    { tone: "warning" as const, title: "Check tree", message: "dirty" },
    { tone: "success" as const, title: "Copied", message: "" },
  ];

  it("over 5000 operations the store never holds a claim nobody is holding", () => {
    const random = lcg(20_251_212);
    const outstanding = new Set<string>();
    const everMinted = new Set<string>();
    const stale: string[] = [];

    const check = (step: number) => {
      const where = `after step ${step}`;
      const seen = new Set<string>();
      let total = 0;
      for (const toast of toasts()) {
        expect(`${where}: ${toast.id}`).toMatch(/toast-\d+$/);
        for (const claim of toast.claims) {
          expect(`${where}: ${claim}`).toMatch(/claim-\d+$/);
          // Held by somebody: the whole thesis of the ticket. A fire-and-forget
          // push that mints, or a cap that keeps 32 unreleasable claims, fails
          // here on the first repeat rather than at some tuned N.
          expect(outstanding.has(claim)).toBe(true);
          // In one place only, and once.
          expect(seen.has(claim)).toBe(false);
          seen.add(claim);
          total += 1;
        }
        // The two vocabularies stay disjoint even after thousands of mints.
        expect(toast.claims).not.toContain(toast.id);
      }
      expect(total).toBeLessThanOrEqual(outstanding.size);
      expect(toasts().length).toBeLessThanOrEqual(4);
      for (const id of ids()) expect(everMinted.has(id)).toBe(false);
    };

    for (let step = 0; step < 5000; step += 1) {
      const roll = random();
      const triple = TRIPLES[Math.floor(random() * TRIPLES.length)];

      if (roll < 0.55) {
        pushToast({ ...triple });
      } else if (roll < 0.7) {
        const claim = pushClaimedToast({ ...triple });
        expect(outstanding.has(claim)).toBe(false);
        expect(everMinted.has(claim)).toBe(false);
        outstanding.add(claim);
        everMinted.add(claim);
      } else if (roll < 0.85) {
        const live = [...outstanding];
        if (live.length) {
          const claim = live[Math.floor(random() * live.length)];
          releaseToast(claim);
          outstanding.delete(claim);
          stale.push(claim);
        }
      } else if (roll < 0.9 && stale.length) {
        // A settled caller's second `finally`, and a claim whose toast the
        // operator already took: both must be silent no-ops.
        releaseToast(stale[Math.floor(random() * stale.length)]);
      } else if (roll < 0.96) {
        const live = ids();
        if (live.length) store().dismiss(live[Math.floor(random() * live.length)]);
      } else {
        store().clear();
      }

      check(step);
    }

    // Everything outstanding settles, as every caller's `finally` eventually
    // does. Nothing is left holding a claim, and no stale claim resurrects a
    // toast on the way.
    for (const claim of [...outstanding]) releaseToast(claim);
    expect(allClaims()).toEqual([]);
    for (const claim of everMinted) releaseToast(claim);
    expect(allClaims()).toEqual([]);
  });
});
