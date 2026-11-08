/**
 * lg-milestone-that-2187 AC8 / R6 — every polling surface's error-branch
 * posture, checked against the source tree rather than against a list.
 *
 * The defect this guards is not one surface branching on `isError` above its
 * data render. It is a FOURTH surface shipping with a fresh posture, which is
 * exactly how this tree came to have four of them. So the fixture is the
 * repository, following `components/__tests__/dialogsTrapFocus.test.ts`, whose
 * docstring answers this same question in so many words: "A per-component test
 * would have to be written twenty-eight times and would still say nothing about
 * the twenty-ninth."
 *
 * The baseline is a SET OF PATHS, not a count, and it may only shrink:
 *  - a reported path absent from the baseline FAILS — a new polling surface
 *    shipped a fresh posture; use `usePolledReadFailure`;
 *  - a baseline path no longer reported FAILS — it is fixed; remove the entry.
 * A count would net a new divergent surface against an old one fixed in the
 * same commit, which is the one thing this must not do. And because nothing may
 * be ADDED and a stale entry fails, it is not an exemption list: the distinction
 * `dialogsTrapFocus` refuses an allowlist for.
 *
 * REJECTED alternative, with its reason: `ts_ux_states_check.cjs` check 10. It
 * would duplicate this predicate in CJS beside the TypeScript one in the hook —
 * the divergence AC6 exists to stop — and it is diff-scoped and
 * `ux-ok:`-waivable, so the fourth surface AC8 names could ship behind a
 * one-line waiver. This test cannot be waived per line and gives the
 * enumeration AC8 asks for (spec D3).
 *
 * STATED LIMIT, so nobody reads a green census as covering more than it does:
 * this predicate catches the *error-branch posture*, which is AC8's stated
 * purpose. It does NOT catch a status-gated CONTROL — `InitiativeBoard.tsx:186`
 * and `:190`'s `board.isSuccess` lines carry `.length` and read as compliant —
 * and that half is pinned per-surface by the AC5/UX3(f) tests instead. The
 * predicate is textual and will have both false positives and false negatives;
 * that is accepted, because a false positive costs one baseline line with a
 * reason, which is the intended friction.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..", "..");

/* ------------------------------------------------------------------ *
 * The predicate. Exported so the self-tests below can run it over an
 * inline source string rather than by editing a real file — the only
 * way R6-2/R6-3/R6-4 can be pinned without a test that mutates the
 * tree it is scanning.
 * ------------------------------------------------------------------ */

export interface PostureSite {
  path: string;
  line: number;
  text: string;
}

/**
 * `const NAME = useQuery(` … through its balanced closing paren.
 *
 * The optional `<…>` is the type-argument form, `useQuery<Row[]>({…})`. It is
 * the same declaration AC8 names and differs only by an annotation, so an
 * opener requiring a bare `useQuery(` would hide the surface entirely — not
 * report it clean, which is a different and worse answer. `[^(]*` cannot cross
 * the call's own paren, so the annotation is bounded.
 */
const POLLED_QUERY = /^\s*const\s+[A-Za-z_$][\w$]*\s*=\s*useQuery\s*(<[^(]*>)?\s*\(/;
/** Any query/mutation options object: a status read inside one is an option, not a branch. */
const QUERY_OPTIONS = /=\s*use(Query|Mutation|Queries|InfiniteQuery)\s*(<[^(]*>)?\s*\(/;
/** A binding or prop whose name says it carries an error or a message, not a render decision. */
const ERROR_SHAPED = /(error|message)/i;

function balancedSpans(lines: string[], opener: RegExp): { start: number; end: number; body: string }[] {
  const spans: { start: number; end: number; body: string }[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!opener.test(lines[i])) continue;
    let depth = 0;
    let end = i;
    for (let j = i; j < lines.length; j += 1) {
      for (const ch of lines[j]) {
        if (ch === "(") depth += 1;
        else if (ch === ")") depth -= 1;
      }
      end = j;
      if (depth <= 0) break;
    }
    spans.push({ start: i, end, body: lines.slice(i, end + 1).join("\n") });
  }
  return spans;
}

/**
 * Every line in `source` that decides a render from a POLLED query's status
 * with no content term on the same condition.
 *
 * The query identifier is BOUND first: a bare line-grep for `isError` reports
 * mutation results and non-polled queries, and at the time of writing differed
 * from this by nine files. A reader who reproduces the wrong number and
 * concludes the predicate is broken is the failure mode that note prevents.
 */
export function findPostureSites(path: string, source: string): PostureSite[] {
  const lines = source.split("\n");

  const polled: string[] = [];
  for (const span of balancedSpans(lines, POLLED_QUERY)) {
    if (!/\brefetchInterval\s*:/.test(span.body)) continue;
    const name = /const\s+([A-Za-z_$][\w$]*)/.exec(span.body);
    if (name) polled.push(name[1]);
  }
  if (polled.length === 0) return [];

  const optionSpans = balancedSpans(lines, QUERY_OPTIONS);
  const sites: PostureSite[] = [];

  for (const name of polled) {
    const status = new RegExp(`\\b${name}\\.(isError|isSuccess)\\b`);
    const data = new RegExp(`\\b${name}\\.data\\b`);
    const bareStatus = new RegExp(`^${name}\\.(isError|isSuccess)$`);

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (!status.test(line)) continue;
      // A comment about the posture is not the posture.
      if (/^\s*(\/\/|\*|\/\*)/.test(line)) continue;
      // Inside a query's options object: the `refetchInterval: q.isError ? false
      // : 4000` stop-polling idiom, and `enabled: !q.isError` beside it. Those
      // are query configuration, not a render branch.
      if (optionSpans.some((span) => i >= span.start && i <= span.end)) continue;
      // A hook dependency array re-states a condition; it does not make one.
      if (/^\s*\}\s*,\s*\[/.test(line)) continue;
      // A content term on the same condition is the compliant posture.
      if (data.test(line) || /\.length\b/.test(line)) continue;
      // Building a message rather than branching a render.
      if (/describeError/.test(line)) continue;
      // A bare continuation line whose expression runs into describeError below.
      if (bareStatus.test(line.trim()) && /describeError/.test(lines.slice(i + 1, i + 4).join("\n"))) {
        continue;
      }
      // Passed to an error-shaped prop or binding rather than branching.
      const before = line.slice(0, line.search(status));
      const prev = (lines[i - 1] ?? "").trim();
      const keyed =
        /([A-Za-z_$][\w$]*)\s*[:=]\s*[^=]*$/.exec(before) ?? /([A-Za-z_$][\w$]*)\s*[:=]\s*$/.exec(prev);
      if (keyed && ERROR_SHAPED.test(keyed[1])) continue;

      sites.push({ path, line: i + 1, text: line.trim() });
    }
  }
  return sites;
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      return entry === "__tests__" || entry === "node_modules" ? [] : sourceFiles(path);
    }
    return /\.tsx?$/.test(entry) ? [path] : [];
  });
}

function scanTree(): PostureSite[] {
  return sourceFiles(SRC).flatMap((path) =>
    findPostureSites(path.slice(SRC.length + 1), readFileSync(path, "utf8")),
  );
}

/* ------------------------------------------------------------------ *
 * The frozen baseline.
 *
 * Measured 2026-11-05 at commit 913f5a894 (the tree the spec stage read),
 * reproduced by this module's own predicate:
 *
 *   node -e '…findPostureSites over client/src…'   → 15 files / 19 sites
 *
 * lg-milestone-that-2187 fixes four of them — InitiativeBoard, McpActivityFeed,
 * MergeQueueSection and InitiativePlanPage — so the baseline it lands is
 * 11 files / 15 sites.
 *
 * NOTE ON THE SPEC'S NUMBER. The spec's R6 says "16 files / 20 sites, dropping
 * to 12 / 16", and names `pages/McpGatewayPage.tsx` among the 12. That file's
 * only `refetchInterval` query is `telemetry` (:102-106), and
 * `telemetry.isError`/`isSuccess` is never read anywhere in it — its `:201`
 * branch is `servers.isError`, a query with no `refetchInterval`. So it carries
 * no bound-query site and cannot be in the baseline. The spec's instruction for
 * this case is explicit — fix the predicate, do not widen the baseline — and
 * the predicate is not what is wrong here: the census over-counted one file.
 * Every other path and per-file count in the spec's list reproduces exactly.
 *
 * Each reason is in AC9's form: what the OPERATOR GETS INSTEAD, never the
 * author's comfort. The follow-up that fixes all eleven is
 * lg-milestone-that-2248.
 * ------------------------------------------------------------------ */

export const KNOWN_DIVERGENT_POSTURES: Record<string, string> = {
  "components/RunLedgerPanel.tsx":
    "pre-existing: a failed 5s poll blanks the run ledger; the next successful poll restores every row. lg-milestone-that-2248.",
  "components/SubtreeAutopilotCard.tsx":
    "pre-existing: a failed poll replaces the subtree autopilot's state with an error line; the next tick brings the card back. lg-milestone-that-2248.",
  "components/chat/primitives/TicketListPrimitive.tsx":
    "pre-existing: a failed parent read hides the ticket list behind a missing-parent notice; the list returns when the parent reads. lg-milestone-that-2248.",
  "components/dashboard/ChildStageBoard.tsx":
    "pre-existing: a failed 4s poll blanks the child stage board and the run list; the next successful poll restores both. lg-milestone-that-2248.",
  "components/dashboard/TicketCIAutoFix.tsx":
    "pre-existing: a failed poll replaces the CI auto-fix status with an error line; the next poll shows the real status again. lg-milestone-that-2248.",
  "components/dashboard/TicketOutputs.tsx":
    "pre-existing: a failed poll blanks the output feed and the ledger; the next successful poll restores the output already produced. lg-milestone-that-2248.",
  "components/dashboard/TicketPullRequestPanel.tsx":
    "pre-existing: a failed lookup hides the PR panel; the panel and its link return on the next poll. lg-milestone-that-2248.",
  "components/dashboard/TicketTimeline.tsx":
    "pre-existing: a failed poll blanks the timeline and its feed; the next successful poll redraws the whole history. lg-milestone-that-2248.",
  "components/memory/ReferenceLibraryTab.tsx":
    "pre-existing: a failed 60s poll empties the reference library pane and toasts on every tick; the next poll restores the sources. Also filed as lg-milestone-that-2247.",
  "components/studio/TicketStudioPanel.tsx":
    "pre-existing: a failed session read drops the studio panel's session view; reselecting or the next poll restores it. lg-milestone-that-2248.",
  "pages/WorkItemsPage.tsx":
    "pre-existing: a failed 15s poll on the tree or the approvals read toasts and leaves the work-items view without its reason column; the next poll fills it back in. lg-milestone-that-2248.",
};

/** The four this ticket fixes. They must be gone from the report, not from the baseline. */
const FIXED_BY_THIS_TICKET = [
  "components/initiatives/plan/InitiativeBoard.tsx",
  "components/mcp/McpActivityFeed.tsx",
  "components/mergeQueue/MergeQueueRow.tsx",
  "pages/InitiativePlanPage.tsx",
];

describe("polled-read failure posture", () => {
  const sites = scanTree();
  const reported = [...new Set(sites.map((site) => site.path))].sort();
  const baseline = Object.keys(KNOWN_DIVERGENT_POSTURES).sort();

  it("finds the tree's polling surfaces at all", () => {
    // A scan that quietly matched nothing would pass every assertion below —
    // the failure mode a crashed gate greps as zero findings.
    expect(sites.length).toBeGreaterThan(10);
    expect(reported.length).toBeGreaterThan(5);
  });

  it("R6-1 — reports exactly the frozen baseline", () => {
    // A SET, not a count. Compared as sorted arrays so the failure message
    // names the paths that moved rather than a number that changed.
    expect(reported).toEqual(baseline);
  });

  it("R6-1 — reports nothing in the four surfaces this ticket fixes", () => {
    expect(reported.filter((path) => FIXED_BY_THIS_TICKET.includes(path))).toEqual([]);
  });

  it("R6-5 — every baseline entry's reason says what the operator gets instead", () => {
    // The test can only check a reason exists and is substantive; that it names
    // the RECOVERY is a reviewer's check (AC9).
    for (const [path, reason] of Object.entries(KNOWN_DIVERGENT_POSTURES)) {
      expect({ path, length: reason.length > 20 }).toEqual({ path, length: true });
    }
  });

  it("does not report the compliant surfaces this ticket lands", () => {
    // The five migrated predicates all carry a content term, so a predicate
    // that stopped seeing content terms would flag them — and would also make
    // the baseline assertion above pass for the wrong reason.
    const monitor = readFileSync(join(SRC, "components/dashboard/WorkflowMonitorView.tsx"), "utf8");
    expect(findPostureSites("components/dashboard/WorkflowMonitorView.tsx", monitor)).toEqual([]);
  });
});

/**
 * The predicate's own behaviour, over inline source strings.
 *
 * R6-2 and R6-3 are about what the test DOES when the tree changes, and the
 * only honest way to pin that without editing a real file is to run the
 * predicate on a string. A test that edited the tree it scans would be
 * order-dependent and would leave the repository dirty on a failure.
 */
describe("the posture predicate", () => {
  it("R6-2 — reports a new polling surface with a bare status guard", () => {
    const source = `
      export function NewSurface() {
        const rows = useQuery({
          queryKey: ["rows"],
          queryFn: api.rows,
          refetchInterval: 4000,
        });
        if (rows.isError) return <div />;
        return <ul>{rows.data.map((r) => <li key={r.id}>{r.name}</li>)}</ul>;
      }
    `;

    const sites = findPostureSites("components/NewSurface.tsx", source);

    expect(sites).toHaveLength(1);
    expect(sites[0].text).toBe("if (rows.isError) return <div />;");
  });

  it("R6-2 — reports a status-only guard written as a JSX ternary", () => {
    // The real shape, as `ChildStageBoard.tsx:318` and `TicketOutputs.tsx:174`
    // write it: the condition on its own line, the data render below. Not
    // compressed onto one line, which would put a `.data` from the OTHER arm of
    // the ternary on the condition's line and read as compliant — see the
    // stated limit below.
    const source = `
      const feed = useQuery({ queryKey: ["feed"], queryFn: api.feed, refetchInterval: 5000 });
      return (
        <div>
          {feed.isError ? (
            <p>Could not load.</p>
          ) : (
            <Feed rows={feed.data} />
          )}
        </div>
      );
    `;

    const sites = findPostureSites("components/Feed.tsx", source);

    expect(sites).toHaveLength(1);
    expect(sites[0].text).toBe("{feed.isError ? (");
  });

  it("states its own limit: a status-gated CONTROL beside a count reads as compliant", () => {
    // AC8's stated limit, pinned so nobody reads a green census as covering it.
    // `InitiativeBoard.tsx:186`/`:190` gate the work-item count and the whole
    // selection toolbar on `board.isSuccess` — FALSE on a failed refetch over
    // good data — and the `.length` beside the status makes the line read as
    // content-based. This predicate catches the error-branch posture, which is
    // what AC8 asks for; that half is pinned per-surface by R1-2 instead.
    const source = `
      const board = useQuery({ queryKey: ["b"], queryFn: api.b, refetchInterval: 30000 });
      return <span>{board.isSuccess ? \`\${items.length} work items\` : ""}</span>;
    `;

    expect(findPostureSites("components/StatusGatedControl.tsx", source)).toEqual([]);
  });

  it("R6-3 — stops reporting a surface once it carries a content term", () => {
    // The fixed shape: the branch is justified by what the READ produced, so
    // the baseline entry for it must then be removed or R6-1 fails with the
    // "this is fixed" delta.
    const source = `
      const rows = useQuery({ queryKey: ["rows"], queryFn: api.rows, refetchInterval: 4000 });
      const failure = usePolledReadFailure(rows, "rows", (rows.data?.length ?? 0) > 0);
      if (rows.isError && !rows.data?.length) return <div />;
    `;

    expect(findPostureSites("components/Fixed.tsx", source)).toEqual([]);
  });

  it("R6-3 — a baseline entry that stops being reported fails the set comparison", () => {
    // The shrink-only half, as a property of the comparison rather than of the
    // tree: this is what makes the baseline not an exemption list.
    const baseline = ["a.tsx", "b.tsx"].sort();
    const reported = ["a.tsx"];

    expect(() => expect(reported).toEqual(baseline)).toThrow();
  });

  it("R6-4 — does not report the refetchInterval stop-polling idiom", () => {
    // `GatePrimitive.tsx:151` and `TicketListPrimitive.tsx:54`. Reading a
    // sibling query's status to STOP POLLING is the opposite of a bad posture:
    // it is how a surface avoids hammering an endpoint that cannot answer.
    const source = `
      const ticketQuery = useQuery({ queryKey: ["t", id], queryFn: api.ticket, refetchInterval: 4000 });
      const gate = useQuery({
        queryKey: ["gate", id],
        queryFn: api.gate,
        enabled: Boolean(id) && !ticketQuery.isError,
        refetchInterval: ticketQuery.isError ? false : 4000,
      });
    `;

    expect(findPostureSites("components/GateLike.tsx", source)).toEqual([]);
  });

  it("R6-4 — does not report a mutation result", () => {
    const source = `
      const rows = useQuery({ queryKey: ["rows"], queryFn: api.rows, refetchInterval: 4000 });
      const resolveApproval = useMutation({ mutationFn: api.resolve });
      const send = useMutation({ mutationFn: api.send });
      if (resolveApproval.isError) return <p>Could not resolve.</p>;
      if (send.isError) return <p>Could not send.</p>;
    `;

    expect(findPostureSites("components/Mutations.tsx", source)).toEqual([]);
  });

  it("R6-4 — does not report a status passed to an error-shaped prop", () => {
    const source = `
      const chat = useQuery({ queryKey: ["chat"], queryFn: api.chat, refetchInterval: 2000 });
      return { loadError: chat.isError, messages: chat.data };
    `;

    expect(findPostureSites("hooks/useChatLike.ts", source)).toEqual([]);
  });

  it("R6-4 — does not report a query with no refetchInterval", () => {
    // AC8 is about POLLING surfaces: a one-shot read that fails has no later
    // tick to restore anything, so its error branch is the right answer.
    const source = `
      const servers = useQuery({ queryKey: ["servers"], queryFn: api.servers });
      if (servers.isError) return <div>Could not load the registry.</div>;
    `;

    expect(findPostureSites("pages/OneShot.tsx", source)).toEqual([]);
  });

  it("R6-4 — does not report a hook dependency array", () => {
    const source = `
      const library = useQuery({ queryKey: ["lib"], queryFn: api.lib, refetchInterval: 60000 });
      useEffect(() => {
        void library;
      }, [library.isError, library.error]);
    `;

    expect(findPostureSites("components/Deps.tsx", source)).toEqual([]);
  });

  it("binds the query identifier, so one file's polled query does not implicate another's", () => {
    // The whole reason the predicate parses the `const NAME = useQuery(` line
    // rather than grepping for `isError`: an unbound grep reports mutations and
    // non-polled reads, and differed from this by nine files at 913f5a894.
    const source = `
      const polled = useQuery({ queryKey: ["a"], queryFn: api.a, refetchInterval: 1000 });
      const oneShot = useQuery({ queryKey: ["b"], queryFn: api.b });
      if (oneShot.isError) return <div />;
      return <div>{polled.data?.length}</div>;
    `;

    expect(findPostureSites("components/Bound.tsx", source)).toEqual([]);
  });
});

/**
 * lg-milestone-that-2187 test-break — the predicate's DECLARATION BINDING, and
 * the shapes a polling surface can be written in that it cannot see at all.
 *
 * The suite above tests what the predicate does with a query it has bound. This
 * one tests whether it binds the query in the first place, which is the half
 * AC8's binding clause actually rests on: "so a fourth polling surface cannot be
 * added with a fresh posture". A surface the scan never binds is not reported,
 * is not in the baseline, and therefore passes — silently, which is the one
 * outcome AC8 exists to prevent.
 *
 * MEASURED read-only over `client/src` at 439f744e9, by enumerating every
 * `use(Query|Queries|InfiniteQuery)(` span whose options carry a
 * `refetchInterval` and comparing against the spans this predicate's
 * `POLLED_QUERY` opener binds: **70 polled queries, 63 bindable, 7 invisible.**
 * The seven are not exotic; they are four ordinary idioms already in the tree:
 *
 *   components/chat/primitives/ParentTicketPrimitive.tsx:23   const { … } = useQuery({
 *   components/chat/primitives/TicketPrimitive.tsx:14         const { … } = useQuery({
 *   components/chat/primitives/TicketWorkflowPrimitive.tsx:18 const { … } = useQuery({
 *   components/chat/primitives/KanbanPrimitive.tsx:45         return useQuery({
 *   hooks/useRuntimeOptionsQuery.ts:42                        return useQuery({
 *   hooks/useTicketApprovals.ts:13                            return useQuery({
 *   hooks/useTicketStatusSummary.ts:17                        return useQuery({
 *
 * One of them is a live instance of this ticket's own defect class, filed rather
 * than absorbed: the three ticket primitives destructure `error` from a polled
 * read and compute `const live = Boolean(data) && !loadError`, so a failed
 * background poll over a ticket already on screen removes its Play/Stop and
 * Open controls while leaving the card rendered — UX1(b)'s defect exactly, on a
 * surface no criterion of this ticket names. See lg-milestone-that-2249.
 *
 * Which of these is a BUG and which is a LIMIT is decided by AC8's own text —
 * "binds every `const NAME = useQuery({…})` whose options contain
 * `refetchInterval`":
 *
 *  - The type-argument form IS that shape, differing only by an annotation the
 *    opener's `useQuery\(` happens to exclude. Missing it is a defect in the
 *    predicate, so the first test below is RED and must be made to pass. It
 *    costs no baseline churn: both type-argument queries in the tree
 *    (`StageFanoutPanel.tsx:47`, `AddWorkspaceModal.tsx:88`) are one-shot reads
 *    with no `refetchInterval`, so widening the opener changes the reported set
 *    by nothing and strictly widens what AC8 can catch.
 *  - The other three are NOT that shape. Binding them means either a different
 *    model (a destructured read has no `NAME.isError` to branch on) or a
 *    cross-file one (a hook returns the query; the caller branches on it). Both
 *    are scope no criterion funds, so they are pinned here as PASSING limit
 *    tests — in the same form as the status-gated-control limit above, and for
 *    the same reason: a stated limit nobody can run is a limit that gets
 *    forgotten, and a green census must not be mistaken for coverage it does
 *    not have.
 */
describe("the posture predicate's declaration binding", () => {
  it("reports a polled query declared with a type argument", () => {
    // RED at 439f744e9. `POLLED_QUERY` requires `useQuery\(`, so the annotation
    // alone hides the surface. This is the same declaration AC8 names, and the
    // branch below is the same bare posture R6-2 reports without the annotation.
    const source = `
      const rows = useQuery<Row[]>({
        queryKey: ["rows"],
        queryFn: api.rows,
        refetchInterval: 4000,
      });
      if (rows.isError) return <div />;
    `;

    const sites = findPostureSites("components/TypeArg.tsx", source);

    expect(sites).toHaveLength(1);
    expect(sites[0].text).toBe("if (rows.isError) return <div />;");
  });

  it("LIMIT — a destructured polled read is invisible, so its branch is never reported", () => {
    // `const { data, error } = useQuery({…})` has no `NAME.isError` to bind, and
    // the branch is on the destructured `error`. Three live surfaces are written
    // this way (the ticket primitives above) and one of them loses its controls
    // on a failed poll. Filed as lg-milestone-that-2249; NOT fixed here, because
    // reporting it needs a second model of what a polled read looks like, and
    // because the baseline AC8 freezes is funded for the bound form only.
    const source = `
      const { data, error } = useQuery({
        queryKey: ["ticket", id],
        queryFn: () => api.ticket(id),
        refetchInterval: 5000,
      });
      if (error) return <div />;
      return <Card ticket={data} />;
    `;

    expect(findPostureSites("components/Destructured.tsx", source)).toEqual([]);
  });

  it("LIMIT — a polled query returned from a custom hook is invisible to a per-file scan", () => {
    // Four surfaces are written this way. The declaration carries no name and
    // the branch lives in whatever file calls the hook, so reporting it needs a
    // cross-file model. A whole-tree scan could grow one; this one has not, and
    // says so rather than letting a green run imply otherwise.
    const source = `
      export function useTicketApprovals(ticketId: string) {
        return useQuery({
          queryKey: ["approvals", ticketId],
          queryFn: () => api.approvals(ticketId),
          refetchInterval: 5000,
        });
      }
    `;

    expect(findPostureSites("hooks/useTicketApprovals.ts", source)).toEqual([]);
  });

  it("LIMIT — aliasing the status to an error-shaped binding evades the report", () => {
    // Two exclusions compose into an evasion: the alias line is suppressed by
    // ERROR_SHAPED (its name contains "error"), and the branch line no longer
    // mentions the query at all, so nothing binds it. The exclusion is right on
    // its own — a status passed to an `error` prop discards nothing (R6-4) — but
    // a status assigned to an error-NAMED local and then branched on discards
    // everything. Accepted as a false negative per the spec's stated terms;
    // pinned so it is a known shape rather than a surprise.
    const source = `
      const feed = useQuery({ queryKey: ["f"], queryFn: api.f, refetchInterval: 5000 });
      const feedError = feed.isError;
      if (feedError) return <p>Could not load.</p>;
      return <Feed rows={feed.data} />;
    `;

    expect(findPostureSites("components/Aliased.tsx", source)).toEqual([]);
  });
});

/**
 * The regime cannot be switched off without a test going red.
 *
 * Every stale-read test on the four surfaces drives its second read by calling
 * `client.refetchQueries(...)` directly — deliberately, so no test has to run
 * jest's clock through react-query. The cost is that NO test anywhere asserts
 * the surfaces still poll. Delete `refetchInterval: 30_000` from the board and
 * every one of R1-1…R1-8 stays green while the live surface never refetches, so
 * the notice they pin can never appear in production; and the census would then
 * stop binding the board's query, report nothing for it, and pass too, because
 * a file reported as clean and a file never scanned are the same answer here.
 *
 * So the polling option and the shared predicate are asserted at the source,
 * which is the one place that distinguishes them.
 */
describe("the four migrated surfaces are still polled, and still share the predicate", () => {
  const MIGRATED = [
    "components/initiatives/plan/InitiativeBoard.tsx",
    "components/mcp/McpActivityFeed.tsx",
    "components/mergeQueue/MergeQueueRow.tsx",
    "pages/InitiativePlanPage.tsx",
  ];

  it.each(MIGRATED)("%s still declares a refetchInterval", (path) => {
    // Coarse on purpose: the claim is "this surface did not stop polling", and
    // the option's presence in the file is what that claim needs. Binding it to
    // a particular query would re-implement the scan above to say less.
    expect(readFileSync(join(SRC, path), "utf8")).toMatch(/\brefetchInterval\s*:/);
  });

  it.each(MIGRATED)("%s decides its branch through usePolledReadFailure", (path) => {
    // RED until the surface migrates. AC6's no-divergence clause is a statement
    // about all five call sites at once, and the per-surface behavioural tests
    // cannot see it: each one passes against a hand-rolled predicate local to
    // its own file, which is precisely the divergence AC6 forbids.
    expect(readFileSync(join(SRC, path), "utf8")).toContain("usePolledReadFailure");
  });
});
