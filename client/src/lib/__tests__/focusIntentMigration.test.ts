import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createQueryClient } from "../../api/queryClient";

/**
 * The parts of lg-milestone-that-2205 that only a reading of the tree can
 * check: the ninth activation site, the three settled rejections, and the claim
 * that the guard has exactly one copy.
 *
 * Everything else in this ticket is pinned by behaviour, and behaviour is
 * better. These four are here because each is a requirement whose *violation
 * produces no observable difference*, so no behavioural test can exist for it:
 *
 *  - **The ninth site** (`ReferenceLibraryTab`'s add-form `onDone`) passes
 *    `from: null`, and a null-armed stamp can never satisfy arm (a) — so the
 *    call changes nothing a test can see. `referenceFocusIntentSites.test.tsx`
 *    covers the other eight behaviourally and says so; this is the only thing
 *    that keeps the ninth from being quietly dropped.
 *  - **`refetchOnWindowFocus: false`** on the query client (AC-S9, constraint 4)
 *    is the cheap-looking fix. It makes AC-S1 and AC-S6(d) pass by removing the
 *    refetch rather than by fixing the focus move — and it changes the freshness
 *    of every query in the app to do it. A suite that builds its own
 *    `QueryClient` per test, as the designed one does, cannot see it at all.
 *  - **`[source.data]`** must be gone from the panel (AC-S3). An implementation
 *    that *adds* the permission rule while leaving the old effect in place
 *    passes most of the behavioural suite, because the rule declines the moves
 *    the old effect would make — until the one case where it permits.
 *  - **One copy of the guard** (AC-S9, constraint 2). Two copies behave
 *    identically on the day they are written, which is the entire problem: the
 *    ticket exists because `useRestoreFocusOnSettle` had the rule and the panel
 *    had its own idea.
 *
 * Reading the tree is the established idiom for this kind of claim here; see
 * `test/__tests__/polledReadFailurePosture.test.ts` and
 * `components/__tests__/dialogsTrapFocus.test.ts`. A textual check has false
 * positives and false negatives, and the trade is accepted for the same reason
 * it is accepted there: the alternative is no check.
 */

const SRC = join(__dirname, "..", "..");

function read(relative: string): string {
  return readFileSync(join(SRC, relative), "utf8");
}

/**
 * Every `recordFocusIntent(<first argument>, …)` in a file, in source order,
 * normalised so the check is about *which value* is stamped and not about how
 * the implementer spelled the narrowing. `item.source!.id`, `item.source?.id`
 * and `item.source.id` are the same site; stamping `selectedId` there is not.
 */
function stampedKeys(relative: string): string[] {
  return [...read(relative).matchAll(/recordFocusIntent\(\s*([^,]+?)\s*,/g)].map((match) =>
    match[1].replace(/[!\s]/g, "").replace(/\?\./g, "."),
  );
}

/**
 * AC-S4's nine sites, as the first argument each one stamps.
 *
 * The expectation is the **set of arguments**, not a count. A count nets a site
 * that was dropped against one that was added, which is the one thing this must
 * not do; the arguments also catch the likelier mistake, which is a handler
 * stamping something ambient (`selectedId`, `sourceId`) instead of the id of
 * the row, pass or result the operator actually activated.
 */
const NINE_SITES: Record<string, string[]> = {
  // The row `<Link>`: the main path, and the one AC-S5(a) exercises end to end.
  "components/memory/ReferenceLibraryTable.tsx": ["row.id"],
  // The add-form's `onDone`. No activation element, hence `null`; see the
  // docstring — this entry is the only check there is on this site.
  "components/memory/ReferenceLibraryTab.tsx": ["result.source.id"],
  // A bulk-add result's link, and the refused-duplicate warning's "Open it in
  // the library".
  "components/memory/ReferenceSourceForm.tsx": ["item.source.id", "refusedId"],
  // The in-flight re-review's link, and a taken-out source's link.
  "components/memory/ReferenceAutoRereview.tsx": ["run.source_id", "source.id"],
  // "Reading now", and "Next review".
  "components/memory/ReferenceQueueStatus.tsx": ["pass.source_id", "next.source_id"],
  // The programmatic navigation, whose one caller is a chat action button that
  // stays mounted — which is why this site may read `document.activeElement`.
  "lib/useAppNavigation.ts": ["sourceId"],
};

describe("AC-S4 — all nine activation sites, read off the tree", () => {
  it("stamps nine intents across six files, and no file is half-migrated", () => {
    const found = Object.fromEntries(
      Object.keys(NINE_SITES).map((file) => [file, stampedKeys(file)]),
    );
    expect(found).toEqual(NINE_SITES);
    expect(Object.values(found).flat()).toHaveLength(9);
  });

  it("gives the add-form's elementless site a null arming element", () => {
    // `null`, not `document.activeElement`: at the moment the form reports
    // success its own submit button is on its way out, and stamping a control
    // that is about to detach would arm an intent arm (a) must then reject on
    // `isConnected`. The site is meant to rely on arm (b).
    expect(read("components/memory/ReferenceLibraryTab.tsx")).toMatch(
      /recordFocusIntent\(\s*result\.source\.id\s*,\s*null\s*\)/,
    );
  });
});

describe("AC-S3 — the panel no longer reads the payload's identity", () => {
  const panel = () => read("components/memory/ReferenceSourceDetail.tsx");

  it("has no effect whose dependency is the query payload", () => {
    // The defect itself: `useEffect(… , [source.data])`. Any effect in this file
    // depending on the payload object is the same class of bug whether or not it
    // moves focus, so the check is on the dependency rather than on the body.
    expect(panel()).not.toMatch(/\[\s*source\.data\s*\]/);
    expect(panel()).not.toContain("heading.current?.focus()");
  });

  it("drives the move from the hook, keyed on the library's own status", () => {
    expect(panel()).toMatch(
      /useFocusOnOperatorIntent\(\s*sourceId\s*,\s*source\.isSuccess\s*,\s*heading\s*\)/,
    );
  });

  it("keys the hook's effect on exactly the key and the ready flag", () => {
    const hook = read("hooks/useFocusOnOperatorIntent.ts");
    expect(hook).toMatch(/\}\s*,\s*\[\s*key\s*,\s*ready\s*\]\s*\)/);
    // A ref, not state: state would re-render, and a boolean could not tell a
    // new source from the one already latched.
    expect(hook).toMatch(/useRef<\s*string\s*\|\s*null\s*>\(null\)/);
  });
});

describe("AC-S9 — the guard has one copy, and the query client was not touched", () => {
  it("imports the idle guard into useRestoreFocusOnSettle rather than declaring one", () => {
    const hook = read("hooks/useRestoreFocusOnSettle.ts");
    expect(hook).toContain('from "../lib/focusIntent"');
    expect(hook).not.toMatch(/function focusIfIdle/);
    // Still used in both places it was used before: the landing, and the armed
    // control. The extraction is a move, not a behaviour change.
    expect(hook).toMatch(/focusIfIdle\(\s*landing\?\.\(\)\s*\)/);
    expect(hook).toMatch(/focusIfIdle\(\s*node\s*\)/);
  });

  it("leaves the idle predicate written exactly once in the tree", () => {
    // Two copies behave the same on the day they are written. This ticket is
    // what happens afterwards.
    const owners = ["lib/focusIntent.ts"];
    const others = [
      "hooks/useRestoreFocusOnSettle.ts",
      "components/memory/ReferenceSourceDetail.tsx",
      "hooks/useFocusOnOperatorIntent.ts",
    ];
    owners.forEach((file) => expect(read(file)).toContain("document.activeElement === document.body"));
    others.forEach((file) => {
      expect(read(file)).not.toContain("=== document.body");
      expect(read(file)).not.toContain("!== document.body");
    });
  });

  it("sets no query defaults, so react-query's own freshness rules still apply", () => {
    // `refetchOnWindowFocus: false` here would make AC-S1 and AC-S6(d) pass by
    // deleting the refetch instead of fixing the focus move, and would change
    // every query in the app to do it. Read off the real client rather than the
    // file, so a default introduced anywhere — `defaultOptions`,
    // `setDefaultOptions`, a wrapper — is caught.
    const defaults = createQueryClient().getDefaultOptions();
    expect(defaults.queries?.refetchOnWindowFocus).toBeUndefined();
    expect(defaults.queries?.staleTime).toBeUndefined();
    expect(defaults.queries?.structuralSharing).toBeUndefined();
  });

  it("adds no id-comparison guard in the panel's data branch", () => {
    // The third settled rejection (AC-S12). `sourceId` is in the `queryKey` and
    // `ReferenceLibraryTab.tsx:248` passes `key={selectedId}`, so a mismatch
    // branch is unreachable — and an unreachable branch cannot carry a test,
    // which is the whole reason it was rejected rather than added.
    const panel = read("components/memory/ReferenceSourceDetail.tsx");
    expect(panel).not.toMatch(/source\.data\.source\.id\s*[!=]==\s*sourceId/);
    expect(panel).not.toMatch(/sourceId\s*[!=]==\s*source\.data/);
  });
});

describe("AC-S2 — the window is the ticket's number, with its reason beside it", () => {
  it("states why 5000 and not beluga's 800, on the constant itself", () => {
    // R5 is a tunable with a silent failure mode: a round trip slower than the
    // window declines the move. The number is only defensible with the reason
    // next to it, so the reason is part of the deliverable.
    const module = read("lib/focusIntent.ts");
    const comment = module.slice(0, module.indexOf("FOCUS_INTENT_MS"));
    expect(module).toMatch(/FOCUS_INTENT_MS = 5000/);
    expect(comment).toMatch(/800|beluga|round trip/i);
  });
});
