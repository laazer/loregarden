import fs from "node:fs";
import path from "node:path";

import { ApiError } from "../../api/client";
import * as toastStoreModule from "../toastStore";
import {
  describeError,
  endSentence,
  pushClaimedToast,
  pushToast,
  releaseToast,
  toastActionFailed,
  toastWarning,
  useToastStore,
} from "../toastStore";

const store = () => useToastStore.getState();
const toasts = () => useToastStore.getState().toasts;
const ids = () => toasts().map((t) => t.id);

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

it("S2 — refreshes a repeat of the same failure in place rather than re-raising it", () => {
  toastActionFailed("Start run", new Error("boom"));
  // Read from the STORE, not from `toastActionFailed`'s return value. Since
  // lg-external-harness-1212 the fire-and-forget route returns the toast's id,
  // so `second === first` would now be TRUE for a repeat and asserting it would
  // pin the dedupe only as far as that one string: the toast's identity is
  // `toasts[0].id`, read back from the store, and its index and revision are
  // what a refresh has to move or keep.
  const firstId = toasts()[0].id;
  const firstRevision = toasts()[0].revision;

  toastActionFailed("Start run", new Error("boom"));

  expect(toasts()).toHaveLength(1);
  expect(toasts()[0].id).toBe(firstId);
  expect(toasts()[0].revision).toBeGreaterThan(firstRevision);
});

it("S2 — the refresh keeps the toast's place in the stack and takes the new duration", () => {
  store().push({ title: "Oldest", message: "a" });
  store().push({ title: "Newest", message: "b" });
  const [oldestId, newestId] = ids();

  store().push({ title: "Oldest", message: "a", duration: 1234 });

  expect(toasts().map((t) => t.title)).toEqual(["Oldest", "Newest"]);
  expect(ids()).toEqual([oldestId, newestId]);
  expect(toasts()[0].duration).toBe(1234);
});

it("S14 — revision is monotonic and deterministic, never a clock or a random", () => {
  const dateNow = jest.spyOn(Date, "now");
  const random = jest.spyOn(Math, "random");
  try {
    useToastStore.getState().push({ title: "One", message: "a" });
    const one = useToastStore.getState().toasts[0].revision;
    useToastStore.getState().push({ title: "Two", message: "b" });
    const two = useToastStore.getState().toasts[1].revision;

    expect(Number.isInteger(one)).toBe(true);
    expect(two).toBeGreaterThan(one);
    expect(dateNow).not.toHaveBeenCalled();
    expect(random).not.toHaveBeenCalled();
  } finally {
    dateNow.mockRestore();
    random.mockRestore();
  }
});

it("S14 — a claim id comes from the same monotonic seq, not a clock or a random", () => {
  // The claim is the third thing minted from `seq` (after the toast id and
  // `revision`), and it has to be as deterministic as those two: the suite runs
  // under fake timers and asserts exact arithmetic on `revision`.
  //
  // lg-external-harness-1212 moves claim minting to `pushClaimed`; `push` mints
  // none at all, so this determinism is only observable on the claimed route.
  const dateNow = jest.spyOn(Date, "now");
  const random = jest.spyOn(Math, "random");
  try {
    const first = store().pushClaimed({ tone: "info", title: "Preparing", duration: 0 });
    const second = store().pushClaimed({ tone: "info", title: "Preparing", duration: 0 });

    expect(typeof first).toBe("string");
    expect(first).not.toBe(second);
    expect(dateNow).not.toHaveBeenCalled();
    expect(random).not.toHaveBeenCalled();
  } finally {
    dateNow.mockRestore();
    random.mockRestore();
  }
});

it("S2 — a push that differs in tone, title or message is a new toast, not a refresh", () => {
  // The oracle is the set of TOAST ids read back from the store, which collapses
  // under a dedupe key narrowed to ignore `tone` or `message`. Not the return
  // values: under lg-external-harness-1209 they were per-call claims (distinct
  // for every push, deduped or not) and under lg-external-harness-1212 they are
  // toast ids, and in neither regime does a set over them fail for a narrowed
  // key — the first because it never collapses, the second because it collapses
  // exactly as the store does.
  const base = { tone: "error" as const, title: "Start run failed", message: "boom" };
  store().push(base);
  store().push({ ...base, tone: "warning" });
  store().push({ ...base, title: "Stop run failed" });
  store().push({ ...base, message: "boom " });

  expect(toasts()).toHaveLength(4);
  expect(new Set(ids()).size).toBe(4);
  expect(toasts().map((t) => [t.tone, t.title, t.message])).toEqual([
    ["error", "Start run failed", "boom"],
    ["warning", "Start run failed", "boom"],
    ["error", "Stop run failed", "boom"],
    ["error", "Start run failed", "boom "],
  ]);
});

describe("S2 — how long an error toast stays up", () => {
  it("S2.1/S2.2 — an error is sticky by default", () => {
    toastActionFailed("Delete session", new ApiError(409, "a path the operator needs"));

    expect(useToastStore.getState().toasts[0].duration).toBe(0);
  });

  it("S2.1 — the other three tones keep the durations they had", () => {
    const state = useToastStore.getState();
    state.push({ tone: "warning", title: "Warning" });
    state.push({ tone: "success", title: "Success" });
    state.push({ tone: "info", title: "Info" });

    expect(
      Object.fromEntries(useToastStore.getState().toasts.map((t) => [t.tone, t.duration])),
    ).toEqual({ warning: 7000, success: 4000, info: 5000 });
  });

  it("S2.3 — a caller can still ask for a bounded one", () => {
    toastActionFailed("Load tickets", new ApiError(503, "database is locked"), { duration: 9000 });

    expect(useToastStore.getState().toasts[0].duration).toBe(9000);
  });

  it("S2.5 — an explicit duration wins over the default, including an explicit zero", () => {
    const state = useToastStore.getState();
    state.push({ tone: "info", title: "Sticky info", duration: 0 });
    state.push({ tone: "error", title: "Bounded error", duration: 9000 });

    const durations = useToastStore.getState().toasts;
    expect(durations.map((t) => t.duration)).toEqual([0, 9000]);
  });
});

it("S8 — keeps distinct failures side by side, capped at four", () => {
  for (const n of [1, 2, 3, 4, 5]) {
    toastActionFailed(`Action ${n}`, new Error("boom"));
  }

  const titles = useToastStore.getState().toasts.map((t) => t.title);
  expect(titles).toEqual(["Action 2 failed", "Action 3 failed", "Action 4 failed", "Action 5 failed"]);
});

it("S6 — dismisses only the toast asked for", () => {
  store().push({ title: "Keep" });
  store().push({ title: "Drop" });
  const [keep, drop] = ids();

  store().dismiss(drop);

  expect(ids()).toEqual([keep]);
});

it("S8 — a refresh at the cap evicts nothing", () => {
  // The in-place update and the cap meet here. An implementation that appends
  // the refreshed copy and then slices to MAX_TOASTS keeps the right length
  // while silently dropping a DIFFERENT failure — the oldest one — and reads
  // as correct in every test that pushes fewer than four toasts.
  for (const n of [1, 2, 3, 4]) {
    store().push({ title: `Action ${n} failed`, message: `detail ${n}` });
  }
  const before = ids();

  store().push({ title: "Action 1 failed", message: "detail 1" });

  expect(ids()).toEqual(before);
  expect(toasts().map((t) => t.title)).toEqual([
    "Action 1 failed",
    "Action 2 failed",
    "Action 3 failed",
    "Action 4 failed",
  ]);
});

it("S2 — a refresh bumps only its own revision", () => {
  // `revision` is what `useAutoDismiss` keys its deadline on, so bumping every
  // toast's revision on any push would restart all four deadlines whenever one
  // failure repeats — and a toast the operator is reading would get a fresh
  // clock it did not earn, or lose the pause it did.
  useToastStore.getState().push({ title: "Untouched", message: "a" });
  useToastStore.getState().push({ title: "Repeating", message: "b" });
  const before = useToastStore.getState().toasts.map((t) => t.revision);

  useToastStore.getState().push({ title: "Repeating", message: "b" });

  const after = useToastStore.getState().toasts.map((t) => t.revision);
  expect(after[0]).toBe(before[0]);
  expect(after[1]).toBeGreaterThan(before[1]);
});

it("S14 — a dismissed toast's id is not handed out again", () => {
  // `seq` is monotonic, so this holds by construction — asserted because the
  // in-place refresh makes ids long-lived, and a counter reset or a
  // `toasts.length`-derived id would start reusing them. A reused id lands on
  // React's key for a node the operator is pointing at.
  //
  // Read from the store rather than from the return value, so this stays an
  // assertion about the toast's identity across both regimes: a claim id under
  // lg-external-harness-1209 (distinct every call, so it could never catch a
  // recycled toast id) and the toast id itself under lg-external-harness-1212.
  store().push({ title: "One", message: "a" });
  const firstId = toasts()[0].id;
  store().dismiss(firstId);
  store().push({ title: "Two", message: "b" });

  expect(toasts()[0].id).not.toBe(firstId);
});

describe("the sticky in-flight claim (lg-external-harness-1209)", () => {
  const IN_FLIGHT = {
    tone: "info" as const,
    title: "Preparing terminal command",
    duration: 0,
  };

  it("S1 — every claimed push mints its own claim, including one that dedupes onto a live toast", () => {
    // The defect's first half, exactly as filed: two overlapping pushes of the
    // same in-flight toast used to hand both callers the SAME string, so the
    // first to settle retracted the second's indicator.
    //
    // lg-external-harness-1212 moves this to `pushClaimedToast`. The minting is
    // what changed hands, not the guarantee: a caller that will release still
    // gets a handle nobody else holds. The fire-and-forget route mints nothing,
    // which is A1/A2 and is asserted in its own block below.
    const a = pushClaimedToast({ ...IN_FLIGHT });
    const b = pushClaimedToast({ ...IN_FLIGHT });

    expect(typeof a).toBe("string");
    expect(a).not.toBe(b);
    expect(toasts()).toHaveLength(1);
  });

  it("S3 — a release drops one claim and removes the toast only when the last one goes", () => {
    // The defect's second half, and the fix. Written as `release(a)` and not
    // `dismiss(a)`: `a` is a claim id, `dismiss` takes a toast id, so the
    // literal `dismiss(a)` would be a no-op on an unknown id and this test
    // would pass for the wrong reason. Under lg-external-harness-1212 the two
    // vocabularies are disjoint by prefix as well as by value, and A4 pins that
    // directly.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    const b = store().pushClaimed({ ...IN_FLIGHT });
    const toastId = toasts()[0].id;

    store().release(a);

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].id).toBe(toastId);

    store().release(b);

    expect(toasts()).toHaveLength(0);
  });

  it("S9 — releaseToast is the module-level route to the same thing", () => {
    const a = pushClaimedToast({ ...IN_FLIGHT });
    const b = pushClaimedToast({ ...IN_FLIGHT });

    releaseToast(a);
    expect(toasts()).toHaveLength(1);

    releaseToast(b);
    expect(toasts()).toHaveLength(0);
  });

  it("S4 — a release is idempotent and cannot take a claim it already gave up", () => {
    // A refcount would not have this: two decrements from one caller would take
    // the count to zero and remove the toast the other caller is still waiting
    // on. Distinct claim ids make it true by construction, which is the reason
    // claim ids were chosen over a count — so it is pinned, not assumed.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    store().pushClaimed({ ...IN_FLIGHT });

    store().release(a);
    store().release(a);
    store().release(a);

    expect(toasts()).toHaveLength(1);
  });

  it("S5 — an unknown claim is a silent no-op", () => {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    const error = jest.spyOn(console, "error").mockImplementation(() => {});
    try {
      const live = store().pushClaimed({ ...IN_FLIGHT });
      const elsewhere = store().pushClaimed({ tone: "error", title: "Elsewhere" });
      const before = ids();

      // Never minted, and empty — neither names a claim on anything.
      store().release("claim-never-minted");
      store().release("");
      expect(ids()).toEqual(before);

      // A claim on ANOTHER toast takes that toast and nothing else: a release
      // that searched by claim count rather than by claim identity would take
      // whichever toast it reached first.
      store().release(elsewhere);
      expect(toasts().map((t) => t.title)).toEqual(["Preparing terminal command"]);

      // Already released, so now unknown.
      store().release(elsewhere);
      expect(toasts()).toHaveLength(1);

      store().release(live);
      expect(toasts()).toHaveLength(0);
      expect(warn).not.toHaveBeenCalled();
      expect(error).not.toHaveBeenCalled();
    } finally {
      warn.mockRestore();
      error.mockRestore();
    }
  });

  it("S6/UX7 — the operator's route stays unconditional with live claims outstanding", () => {
    // `ToastHost` binds one `dismiss` for both the close button and the
    // auto-dismiss deadline. If `dismiss` were made to consult a claim set, a
    // twice-raised failure would need two close presses — a WCAG 2.2.1
    // regression on the ticket that exists to satisfy 2.2.1.
    store().pushClaimed({ ...IN_FLIGHT });
    store().pushClaimed({ ...IN_FLIGHT });

    store().dismiss(toasts()[0].id);

    expect(toasts()).toHaveLength(0);
  });

  it("S7/UX7 — a release after the operator dismissed does not resurrect the toast", () => {
    const a = store().pushClaimed({ ...IN_FLIGHT });
    store().dismiss(toasts()[0].id);

    store().release(a);

    expect(toasts()).toEqual([]);
  });

  it("S7 — a release after clear() does not resurrect the toast", () => {
    const a = store().pushClaimed({ ...IN_FLIGHT });
    store().clear();

    store().release(a);

    expect(toasts()).toEqual([]);
  });

  it("S7/S8/UX9 — a claimed toast is still evictable, and the stale claim's release is a no-op", () => {
    // Live claims buy no exemption from the cap, matching lg-external-harness-
    // 1203 UX14's refusal to exempt the toast under the pointer. The operator-
    // visible consequence is accepted: under a burst of four newer toasts the
    // in-flight indicator can vanish before the action settles.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    for (const n of [1, 2, 3, 4]) {
      store().push({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
    }
    expect(toasts().map((t) => t.title)).toEqual([
      "Action 1 failed",
      "Action 2 failed",
      "Action 3 failed",
      "Action 4 failed",
    ]);
    const after = ids();

    store().release(a);

    expect(ids()).toEqual(after);
  });

  it("UX8 — a release never takes a stranger's toast", () => {
    // The sequence an identity-blind refcount gets wrong: A pushes, the
    // operator closes that toast, B pushes an IDENTICAL one and gets a fresh
    // toast with one claim, then A settles. A decrement would take B's.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    const firstToastId = toasts()[0].id;
    store().dismiss(firstToastId);

    store().pushClaimed({ ...IN_FLIGHT });
    const secondToastId = toasts()[0].id;
    expect(secondToastId).not.toBe(firstToastId);

    store().release(a);

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].id).toBe(secondToastId);
  });

  it("UX5/UX6 — an overlapping push changes nothing the operator can see", () => {
    // No "(2)", no counter, no claim count in the text: those three fields ARE
    // the dedupe key, so changing them would split the twin and put the second
    // toast back on screen. The unchanged id AND index are what make
    // `AnimatePresence` play neither an exit nor an enter.
    store().pushClaimed({ ...IN_FLIGHT, message: "bringing its worktree up to its target branch." });
    const before = toasts()[0];

    store().pushClaimed({ ...IN_FLIGHT, message: "bringing its worktree up to its target branch." });

    const list = toasts();
    expect(list).toHaveLength(1);
    expect(list[0].id).toBe(before.id);
    expect(list.indexOf(list[0])).toBe(0);
    expect([list[0].tone, list[0].title, list[0].message]).toEqual([
      before.tone,
      before.title,
      before.message,
    ]);
  });

  it("UX3 — four tickets' byte-identical preparing toasts are one toast with four claims", () => {
    // Measured on the live database (1595 tickets, 2026-10-16): 1531 carry no
    // `branch`, so `movingTree` renders the literal "its worktree", and the
    // preparing message names only the stage. 845 open tickets at `triage`
    // therefore produce the same tone/title/message triple — the collision is
    // board-wide, not one double-clicked ticket. `MAX_TOASTS = 4` is the stack
    // size it happens inside.
    const preparing = {
      ...IN_FLIGHT,
      message: "Preparing terminal command for Triage — bringing its worktree up to its target branch.",
    };
    const claims = [1, 2, 3, 4].map(() => store().pushClaimed({ ...preparing }));

    expect(new Set(claims).size).toBe(4);
    expect(toasts()).toHaveLength(1);
    const toastId = toasts()[0].id;

    for (const claim of claims.slice(0, 3)) {
      store().release(claim);
      expect(toasts()).toHaveLength(1);
      expect(toasts()[0].id).toBe(toastId);
    }

    store().release(claims[3]);
    expect(toasts()).toHaveLength(0);
  });
});

describe("S9 — the two vocabularies are named", () => {
  const SRC = path.resolve(__dirname, "..", "..");

  it("exports releaseToast and no longer exports dismissToast", () => {
    const exported = toastStoreModule as unknown as Record<string, unknown>;

    expect(typeof exported.releaseToast).toBe("function");
    // No alias and no deprecation shim: the compatibility posture is
    // `internal`, so the one caller is migrated in the same change.
    expect(Object.keys(exported)).not.toContain("dismissToast");
  });

  it("A8 — names the claimed push as a second function, not an option on the first", () => {
    // Extended for lg-external-harness-1212. Two functions, deliberately: an
    // `{ claimed: true }` option would make the MEANING of the returned string
    // depend on an argument value, which TypeScript can only express with
    // overloads and which no call site asked for.
    const exported = toastStoreModule as unknown as Record<string, unknown>;

    expect(typeof exported.pushToast).toBe("function");
    expect(typeof exported.pushClaimedToast).toBe("function");
    expect(typeof exported.toastWarning).toBe("function");
    expect(typeof exported.toastActionFailed).toBe("function");
  });

  it("A8 — the store exposes exactly the six members, and no claimed option", () => {
    const state = useToastStore.getState() as unknown as Record<string, unknown>;

    expect(Object.keys(state).sort()).toEqual(
      ["clear", "dismiss", "push", "pushClaimed", "release", "toasts"].sort(),
    );
  });

  it("A8 — a fire-and-forget push returns the toast's own id, and a repeat returns the same one", () => {
    // The return TYPE is the half of A8 a signature cannot pin: both routes
    // return `string`, so only behaviour distinguishes "the toast's id" from
    // "this call's claim". A deduped repeat returning a fresh string is the
    // lg-external-harness-1209 regime, and is what this rejects.
    const first = pushToast({ tone: "error", title: "Queue error", duration: 0 });

    expect(first).toBe(toasts()[0].id);
    expect(first).toMatch(/^toast-/);

    const second = pushToast({ tone: "error", title: "Queue error", duration: 0 });

    expect(second).toBe(first);
    expect(toasts()).toHaveLength(1);
  });

  it("A8 — toastWarning and toastActionFailed return a toast id and mint nothing", () => {
    // The 81 discarding sites reach the store through these two as often as
    // through `pushToast`, and a claim minted inside either is as unreleasable
    // as one minted directly.
    const warned = toastWarning("Check tree", new Error("dirty"), "Check tree failed");
    const failed = toastActionFailed("Delete ticket", new Error("boom"));

    expect(ids()).toEqual([warned, failed]);
    expect(toasts().map((t) => t.claims)).toEqual([[], []]);
  });

  it("has no production call site left calling dismissToast", () => {
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__" && entry.name !== "test") walk(full);
          continue;
        }
        if (!/\.tsx?$/.test(entry.name)) continue;
        if (fs.readFileSync(full, "utf8").includes("dismissToast")) {
          offenders.push(path.relative(SRC, full));
        }
      }
    };
    walk(SRC);

    expect(offenders).toEqual([]);
  });

  it("A9 — the one claimed triple is unreachable from a fire-and-forget site", () => {
    // A claimed push that deduped onto a toast an unclaimed caller raised would
    // let the claimed caller's release take that toast. lg-external-harness-1212
    // pins that as UNREACHABLE rather than guarding it with a flag: there is
    // exactly one claimed call site and no fire-and-forget site pushes its
    // title. If a later change adds one, this goes red and the semantics get
    // decided then, deliberately, rather than drifting.
    //
    // Scanned over the production tree, like the `dismissToast` test above and
    // for the same reason: the claim is about the whole tree, and nothing in one
    // unit test's module graph can see it.
    //
    // Judged per OCCURRENCE of the title, not per file. `terminalCommands.ts`
    // legitimately carries both routes — its success and warning toasts are
    // fire-and-forget — so a file-level answer cannot see a `pushToast` of the
    // claimed title added inside that same file, which is the drift this exists
    // to catch. The route is read as the nearest push-like call opening before
    // the literal, which is exact at every site in this tree because the title
    // is an argument of the call that carries it.
    const CLAIMED_TITLE = "Preparing terminal command";
    const CLAIMED = ["pushClaimedToast(", "pushClaimed("];
    const UNCLAIMED = ["pushToast(", "toastActionFailed(", "toastWarning(", "push("];

    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__" && entry.name !== "test") walk(full);
          continue;
        }
        if (/\.tsx?$/.test(entry.name)) files.push(full);
      }
    };
    walk(SRC);

    const claimed = new Set<string>();
    const unclaimed = new Set<string>();
    for (const full of files) {
      const source = fs.readFileSync(full, "utf8");
      const rel = path.relative(SRC, full);
      for (let at = source.indexOf(CLAIMED_TITLE); at !== -1; at = source.indexOf(CLAIMED_TITLE, at + 1)) {
        const before = source.slice(0, at);
        // The longest token wins, so `pushClaimedToast(` is never read as the
        // `push(` inside it and `pushToast(` is never read as `push(`.
        const nearest = [...CLAIMED, ...UNCLAIMED]
          .map((token) => ({ token, at: before.lastIndexOf(token) }))
          .filter((hit) => hit.at !== -1)
          .sort((a, b) => b.at - a.at || b.token.length - a.token.length)[0];
        if (!nearest) continue;
        // By FILE, not by line. The title appears twice at the one site — once
        // as the `title` argument and once inside the message naming the stage
        // — and a line number is churn that says nothing about the route.
        if (CLAIMED.includes(nearest.token)) claimed.add(rel);
        else unclaimed.add(rel);
      }
    }

    expect([...claimed]).toEqual(["lib/terminalCommands.ts"]);
    expect([...unclaimed]).toEqual([]);
  });
});

// lg-external-harness-1212. The claim set this ticket bounds, over the route
// that 81 of the tree's 82 push sites take: fire-and-forget. The block above
// exercises the claimed route, where a claim is minted BECAUSE somebody will
// release it; everything here is about the other route, where nobody will.
describe("lg-external-harness-1212 — a caller that never releases mints nothing", () => {
  // The exact triple `queueNotifications.ts` raises on a `queue_error` event
  // with no `data.message`: the constant fallback supplies the message, which
  // is what makes byte-identical repeats the common case rather than the
  // unlucky one, and `duration: 0` is what keeps the deduped toast on screen
  // for the growth to happen inside.
  const QUEUE_ERROR = {
    tone: "error" as const,
    title: "Queue error",
    message: "The queue reported a failure.",
    duration: 0,
  };

  const IN_FLIGHT = {
    tone: "info" as const,
    title: "Preparing terminal command",
    duration: 0,
  };

  it("A1 — a sticky toast pushed N times carries a claim set bounded independent of N", () => {
    // The filing's repro. Post-lg-external-harness-1209 and pre-fix this
    // reaches `claims.length === N` (measured to 20000 by the reviewer who
    // filed it); the assertion is on `claims.length`, never on `toasts.length`,
    // which is 1 in both regimes and so pins nothing.
    //
    // Asserted as exactly 0 rather than "small": a cap on appends would bound
    // the COUNT of unreleasable claims without removing any, and that is the
    // alternative A2 rules out. Run at two values of N so a constant bound is
    // distinguishable from a slow-growing one.
    for (let i = 0; i < 1000; i += 1) pushToast({ ...QUEUE_ERROR });

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([]);

    for (let i = 0; i < 2000; i += 1) pushToast({ ...QUEUE_ERROR });

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([]);
  });

  it("A1 — the same holds through toastActionFailed and toastWarning", () => {
    // The 82 sites are 50 `pushToast` + 29 `toastActionFailed` + 3
    // `toastWarning`. A fix applied to `push` alone would leave the other two
    // minting, and they reach the same sticky-error default.
    for (let i = 0; i < 1000; i += 1) {
      toastActionFailed("Delete ticket", new Error("ticket has an active run"));
    }
    for (let i = 0; i < 1000; i += 1) {
      toastWarning("Check tree", new Error("dirty"), "Check tree failed");
    }

    expect(toasts().map((t) => t.claims)).toEqual([[], []]);
  });

  it("A6 — a repeat does not rebuild the claim array, so the cost is not quadratic", () => {
    // Structural, not on a clock: the quadratic term in the filing's numbers
    // (N=1000 → 11ms, N=5000 → 72ms, N=20000 → 1173ms; 5x the events cost
    // 6.5x) comes from the dedupe branch rebuilding the whole claim array on
    // every push. If the array reference is unchanged across repeats, no
    // per-push allocation proportional to the prior count happened — which is
    // the property, and it holds at every N rather than at a measured one.
    pushToast({ ...QUEUE_ERROR });
    const first = toasts()[0].claims;

    for (let i = 0; i < 1000; i += 1) {
      pushToast({ ...QUEUE_ERROR });
      expect(Object.is(toasts()[0].claims, first)).toBe(true);
    }
  });

  it("A6 — and the toast itself still refreshes, so the identity check is not vacuous", () => {
    // `Object.is` on `claims` would also hold if the dedupe branch stopped
    // updating the toast at all — which would satisfy A6 by doing nothing and
    // break S2. The refresh is asserted alongside it.
    pushToast({ ...QUEUE_ERROR });
    const before = toasts()[0];

    pushToast({ ...QUEUE_ERROR });

    const after = toasts()[0];
    expect(after.id).toBe(before.id);
    expect(after.revision).toBeGreaterThan(before.revision);
    expect(Object.is(after.claims, before.claims)).toBe(true);
  });

  it("A6 — a generous wall-clock ceiling, as a smoke check and not the oracle", () => {
    // Deliberately loose. The pre-fix cost at this N was 1173ms and grows as
    // N²; a tight `elapsed < X` on a shared machine running parallel jest
    // workers is a flaky test rather than a verified criterion, so the number
    // here is ~8x the slowest pre-fix measurement and the identity assertions
    // above are what actually pin the complexity.
    const started = performance.now();
    for (let i = 0; i < 20_000; i += 1) pushToast({ ...QUEUE_ERROR });
    const elapsed = performance.now() - started;

    expect(toasts()[0].claims).toEqual([]);
    expect(elapsed).toBeLessThan(10_000);
  });

  it("A5 — release cannot take a toast that holds no claims", () => {
    // `release` looks a claim up by the toast HOLDING it, so a toast with an
    // empty set matches nothing and the no-op holds by construction. A "no
    // claims means remove" branch would break this and let an unrelated
    // caller's release take a toast nobody claimed — which, after A1, is every
    // toast the 81 sites raise.
    pushToast({ tone: "error", title: "Keep me", message: "first" });
    pushToast({ ...QUEUE_ERROR });
    const before = ids();
    const elsewhere = store().pushClaimed({ ...IN_FLIGHT });

    store().release(elsewhere);
    store().release("claim-never-minted");
    store().release("");
    store().release(before[1]);

    expect(ids()).toEqual(before);
    expect(toasts()[1].claims).toEqual([]);
  });

  it("A5 — dismiss, the cap and clear() are still the only ways an unclaimed toast leaves", () => {
    pushToast({ ...QUEUE_ERROR });
    store().dismiss(toasts()[0].id);
    expect(toasts()).toHaveLength(0);

    pushToast({ ...QUEUE_ERROR });
    for (const n of [1, 2, 3, 4]) {
      pushToast({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
    }
    expect(toasts().map((t) => t.title)).not.toContain("Queue error");

    pushToast({ ...QUEUE_ERROR });
    store().clear();
    expect(toasts()).toHaveLength(0);
  });

  it("A4 — the two vocabularies stay disjoint over a mixed run", () => {
    // A claim derived from the toast's own id, or a set collapsed into a count,
    // passes every pair-of-claims sequence and starts confusing the two here.
    const claims: string[] = [];
    for (const n of [1, 2, 3, 4, 5, 6]) {
      claims.push(store().pushClaimed({ ...IN_FLIGHT }));
      pushToast({ ...QUEUE_ERROR });
      pushToast({ tone: "error", title: `Action ${n} failed`, message: `d${n}` });
      if (toasts().length > 2) store().dismiss(ids()[0]);
    }

    for (const claim of claims) expect(claim).toMatch(/^claim-/);
    for (const id of ids()) expect(id).toMatch(/^toast-/);
    expect(new Set(claims).size).toBe(claims.length);
    for (const id of ids()) expect(claims).not.toContain(id);
  });

  it("A4 — dismiss(claimId) and release(toastId) are both no-ops", () => {
    const claim = store().pushClaimed({ ...IN_FLIGHT });
    pushToast({ ...QUEUE_ERROR });
    const before = ids();

    store().dismiss(claim);
    expect(ids()).toEqual(before);

    for (const id of before) store().release(id);
    expect(ids()).toEqual(before);

    store().release(claim);
    expect(toasts().map((t) => t.title)).toEqual(["Queue error"]);
  });

  it("A8 — a fire-and-forget repeat onto a claimed toast leaves the claim set alone", () => {
    // The regression lg-external-harness-1209's own comment warns about, now
    // reachable from the other direction: `claims` must not be a field of the
    // refreshed bundle, or this push resets the set to nothing and the caller
    // still awaiting the server loses the toast its release would have taken.
    const claim = store().pushClaimed({ ...IN_FLIGHT });
    const toastId = toasts()[0].id;
    const before = toasts()[0].claims;
    expect(before).toEqual([claim]);

    const returned = pushToast({ ...IN_FLIGHT, duration: 4321 });

    expect(returned).toBe(toastId);
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].claims).toEqual([claim]);
    expect(toasts()[0].duration).toBe(4321);

    store().release(claim);
    expect(toasts()).toHaveLength(0);
  });

  it("UD1 — the toast is not bounded by leaving: after N pushes it is still sticky and still there", () => {
    // A1 and A6 are both satisfiable by making the toast expire, auto-dismiss
    // or evict itself, and the store's own `DEFAULT_DURATION` comment pins
    // error stickiness to WCAG 2.2.1 (Timing Adjustable, Level A) in those
    // words. So the claim set may only be bounded by what is minted or
    // released — never by shortening the toast's life.
    for (let i = 0; i < 1000; i += 1) pushToast({ ...QUEUE_ERROR });

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].duration).toBe(0);
    expect(toasts()[0].tone).toBe("error");
    expect(toasts()[0].claims).toEqual([]);

    // And it still goes on the first press of the operator's route, whatever
    // the claim count is.
    store().dismiss(toasts()[0].id);
    expect(toasts()).toHaveLength(0);
  });
});


// test-break (lg-external-harness-1209). The designed suite releases claims in
// the order they were minted, which is the one order a claim set that the
// dedupe branch CLOBBERS still survives: if a repeat keeps only the newest
// claim, `release(a)` is a no-op and `release(b)` removes the toast — the exact
// assertions S3 and UX3 make. C2 names that spread-order regression as the one
// that puts this defect straight back for repeated failures, so it needs a
// release order that can see it, plus the other identity confusions a claim set
// makes newly possible.
describe("test-break — a claim set that only survives one release order", () => {
  const IN_FLIGHT = {
    tone: "info" as const,
    title: "Preparing terminal command",
    message: "Preparing terminal command for Triage — bringing its worktree up to its target branch.",
    duration: 0,
  };

  it("the SECOND caller settling first does not take the first caller's indicator", () => {
    // S3/UX1 in the other release order. A dedupe branch written
    // `{ ...toast, ...raised, claims: [claim] }` — or one that lets a `claims`
    // field inside `raised` overwrite the set — keeps only the newest claim,
    // so this release empties it and the toast goes while caller A is still
    // awaiting the server. Green under the correct fix, red under C2's
    // regression, which the push-order sequences cannot tell apart.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    const b = store().pushClaimed({ ...IN_FLIGHT });
    const toastId = toasts()[0].id;

    store().release(b);

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].id).toBe(toastId);

    store().release(a);

    expect(toasts()).toHaveLength(0);
  });

  it("holds every claim through four refreshes, released in a shuffled order", () => {
    // Order-independence over a claim set bigger than the pair the criteria
    // use, in a fixed (not random) order so a failure reproduces exactly. UX3's
    // board-wide collision is the volume: every open ticket's preparing toast
    // is this toast's twin, so the live claim count is bounded by how many
    // callers overlap, not by two.
    const claims = [0, 1, 2, 3, 4, 5].map(() => store().pushClaimed({ ...IN_FLIGHT }));
    const toastId = toasts()[0].id;

    expect(new Set(claims).size).toBe(6);
    expect(toasts()).toHaveLength(1);

    for (const i of [3, 0, 5, 1, 4]) {
      store().release(claims[i]);
      expect(toasts()).toHaveLength(1);
      expect(toasts()[0].id).toBe(toastId);
    }

    store().release(claims[2]);

    expect(toasts()).toHaveLength(0);
  });

  it("keeps both claims when the refresh happens at the cap", () => {
    // C2 and C3 meet here: the claimed toast is the OLDEST of four, so the
    // refresh is the one push that must neither evict (S8) nor reset the claim
    // set (C2). An implementation that appends the refreshed copy and slices
    // loses the original's claims along with its place.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    for (const n of [1, 2, 3] as const) {
      store().push({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
    }
    const before = ids();

    const b = store().pushClaimed({ ...IN_FLIGHT });

    expect(ids()).toEqual(before);

    store().release(a);
    expect(ids()).toEqual(before);

    store().release(b);
    expect(ids()).toEqual(before.slice(1));
  });

  it("counts claims per toast, not once for the whole stack", () => {
    // A single module-level count — or a count on the store rather than on the
    // Toast — passes every pair-of-claims-on-one-toast sequence in the
    // criteria. Two different in-flight toasts, two claims each, interleaved:
    // each toast must outlive its own last claim and nobody else's.
    const other = { tone: "info" as const, title: "Preparing orchestrate command", duration: 0 };
    const a1 = store().pushClaimed({ ...IN_FLIGHT });
    const b1 = store().pushClaimed({ ...other });
    const a2 = store().pushClaimed({ ...IN_FLIGHT });
    const b2 = store().pushClaimed({ ...other });
    expect(toasts()).toHaveLength(2);
    const [preparingId, orchestrateId] = ids();

    store().release(a1);
    store().release(b1);
    expect(ids()).toEqual([preparingId, orchestrateId]);

    store().release(a2);
    expect(ids()).toEqual([orchestrateId]);

    store().release(b2);
    expect(toasts()).toHaveLength(0);
  });

  it("will not take a toast handed its own id as a claim, or a claim handed to dismiss", () => {
    // The two vocabularies must not be interchangeable in either direction.
    // `release(toastId)` is the mistake an implementation that stores claims as
    // `[toast.id, …]` makes true, and it is also exactly what the filed AC1's
    // literal `dismiss(a)` would be if `dismiss` ever started consulting
    // claims — one fix away from the trivially-passing test A3 warns about.
    const a = store().pushClaimed({ ...IN_FLIGHT });
    const b = store().pushClaimed({ ...IN_FLIGHT });
    const toastId = toasts()[0].id;
    expect([a, b]).not.toContain(toastId);

    store().release(toastId);
    expect(ids()).toEqual([toastId]);

    store().dismiss(a);
    store().dismiss(b);
    expect(ids()).toEqual([toastId]);

    store().release(a);
    store().release(b);
    expect(toasts()).toHaveLength(0);
  });

  it("mints claims that never collide with a toast id, over a mixed run", () => {
    // Determinism and disjointness together (S14, C1). A claim derived from
    // `toasts.length`, or one that reuses the toast's own id with a suffix the
    // store later strips, passes the two-claim sequences and starts colliding
    // once toasts are dismissed and re-pushed under the cap.
    const claims: string[] = [];
    for (const n of [1, 2, 3, 4, 5, 6] as const) {
      claims.push(store().pushClaimed({ ...IN_FLIGHT }));
      claims.push(store().pushClaimed({ tone: "error", title: `Action ${n} failed`, message: `d${n}` }));
      if (toasts().length > 2) store().dismiss(ids()[0]);
    }

    expect(new Set(claims).size).toBe(claims.length);
    for (const id of ids()) expect(claims).not.toContain(id);
  });

  it("changes nothing the operator can see when one of two callers settles", () => {
    // UX1 says the indicator spans every caller; UX5/UX6 say an overlapping
    // push is invisible. The release half of that is unpinned: a `release`
    // implemented by removing and re-pushing the toast would satisfy every
    // length assertion in the criteria while moving its place in the stack,
    // bumping the `revision` `useAutoDismiss` keys its deadline on, and
    // remounting the DOM node under the operator's pointer.
    store().push({ tone: "error", title: "Action 1 failed", message: "detail 1" });
    const a = store().pushClaimed({ ...IN_FLIGHT });
    const b = store().pushClaimed({ ...IN_FLIGHT });
    const before = toasts()[1];
    const beforeIds = ids();

    store().release(a);

    expect(ids()).toEqual(beforeIds);
    const after = toasts()[1];
    expect([after.id, after.tone, after.title, after.message]).toEqual([
      before.id,
      before.tone,
      before.title,
      before.message,
    ]);
    expect(after.duration).toBe(before.duration);
    expect(after.revision).toBe(before.revision);

    store().release(b);
    expect(toasts().map((t) => t.title)).toEqual(["Action 1 failed"]);
  });

  it("cannot take a fresh twin after its own toast was evicted or cleared", () => {
    // UX8 pins this for the `dismiss` route only. The other two routes a toast
    // leaves by (S7) each produce the same trap: the stale claim's dedupe key
    // is byte-identical to the fresh toast's, so an implementation that looks a
    // claim up by key rather than by the toast holding it takes a stranger's
    // toast — on a board where every open ticket's preparing toast is that
    // twin (UX3).
    const evicted = store().pushClaimed({ ...IN_FLIGHT });
    for (const n of [1, 2, 3, 4] as const) {
      store().push({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
    }
    expect(toasts().map((t) => t.tone)).not.toContain("info");

    store().pushClaimed({ ...IN_FLIGHT });
    const freshId = toasts()[toasts().length - 1].id;

    store().release(evicted);

    expect(ids()).toContain(freshId);

    store().clear();
    const cleared = store().pushClaimed({ ...IN_FLIGHT });
    store().clear();
    store().pushClaimed({ ...IN_FLIGHT });
    const afterClearId = toasts()[0].id;

    store().release(cleared);

    expect(ids()).toEqual([afterClearId]);
  });
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
