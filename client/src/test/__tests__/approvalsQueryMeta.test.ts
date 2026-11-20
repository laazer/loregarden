/**
 * AC-8 (lg-inbox-organized-2738): every `useQuery({ queryKey: ["approvals"] })`
 * in the client carries `meta: { errorTitle: "Load approvals" }`.
 *
 * ## Why this is a test and not a render assertion
 *
 * TanStack Query v5 keeps ONE `meta` per query, assigned by whichever observer
 * last set options, and the global `queryClient` is what formats the cold-cache
 * toast from it. So the effect of `meta` on one observer is not observable from
 * that component's render — asserting it there would mean reaching into the
 * library's observer internals, which is interaction testing of someone else's
 * object. Test design said so and left it to be "verified at implement by
 * count: six occurrences".
 *
 * ## Why a count is not enough
 *
 * Because the defect is the SEVENTH observer. Five files hold six of them
 * today, in four directories, and which one names the toast is decided by a
 * race between them; a new approvals surface added anywhere in `src/` that
 * omits the meta silently restores "Load" as the operator's answer to "what
 * failed?". A grep run once at implement cannot see that, and a hand-kept list
 * of five files is an allowlist. The fixture is therefore the repository,
 * following `test/__tests__/polledReadFailurePosture.test.ts` and
 * `components/__tests__/dialogsTrapFocus.test.ts`.
 *
 * ## Stated limits
 *
 * The predicate is textual. It matches the EXACT key `["approvals"]`, because
 * that is the key AC-8 names and the one whose `meta` the observers share — a
 * prefixed key like `["approvals", ticketId]` is a different query with its own
 * meta and is out of scope here. It cannot see a key built from a variable, and
 * it says nothing about whether the toast is any good; it only pins that every
 * observer of the shared query names the same thing as having failed.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..", "..");

/** AC-8's literal string. The toast says this; a near-miss is a different toast. */
const ERROR_TITLE = "Load approvals";

export interface ApprovalsObserver {
  path: string;
  line: number;
  hasMeta: boolean;
}

/** `useQuery(` or `useQuery<T>(`, through its balanced closing paren. */
const USE_QUERY = /\buseQuery\s*(<[^(]*>)?\s*\(/;
/** The exact shared key, tolerant of quote style and spacing only. */
const APPROVALS_KEY = /queryKey:\s*\[\s*["'`]approvals["'`]\s*\]/;

/**
 * Every `useQuery` observing the shared `["approvals"]` query in `source`,
 * with whether it carries AC-8's `errorTitle`.
 *
 * Exported so the self-tests below can run it over inline strings: a test that
 * edited the tree it scans would be order-dependent and would leave the
 * repository dirty on a failure.
 */
export function findApprovalsObservers(path: string, source: string): ApprovalsObserver[] {
  const lines = source.split("\n");
  const found: ApprovalsObserver[] = [];

  for (let i = 0; i < lines.length; i += 1) {
    if (!USE_QUERY.test(lines[i])) continue;

    let depth = 0;
    let end = i;
    for (let j = i; j < lines.length; j += 1) {
      for (const char of lines[j]) {
        if (char === "(") depth += 1;
        else if (char === ")") depth -= 1;
      }
      end = j;
      if (depth <= 0) break;
    }

    const body = lines.slice(i, end + 1).join("\n");
    if (!APPROVALS_KEY.test(body)) continue;

    found.push({
      path,
      line: i + 1,
      hasMeta: new RegExp(`errorTitle:\\s*["'\`]${ERROR_TITLE}["'\`]`).test(body),
    });
  }

  return found;
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

function scanTree(): ApprovalsObserver[] {
  return sourceFiles(SRC).flatMap((path) =>
    findApprovalsObservers(path.slice(SRC.length + 1), readFileSync(path, "utf8")),
  );
}

/**
 * The five files the spec census found, as a floor rather than a list: a sixth
 * file is not a failure, a missing one is. They are named so that a predicate
 * that quietly stopped matching fails here instead of passing vacuously.
 */
const CENSUS_2026_11_18 = [
  "components/AppTopbarActions.tsx",
  "components/inbox/ApprovalsList.tsx",
  "pages/HomePage.tsx",
  "pages/WorkItemsPage.tsx",
  "pages/WorkspaceDashboardPage.tsx",
];

describe("every observer of the shared approvals query names what failed", () => {
  const observers = scanTree();

  it("finds the tree's approvals observers at all", () => {
    // A scan that quietly matched nothing passes every assertion below — the
    // failure mode a crashed gate greps as zero findings.
    expect(observers.length).toBeGreaterThanOrEqual(6);
    expect([...new Set(observers.map((observer) => observer.path))].sort()).toEqual(
      expect.arrayContaining(CENSUS_2026_11_18),
    );
  });

  it("finds the two observers inside ApprovalsList, not one", () => {
    // The list's own query and `usePendingApprovalCount`'s. A predicate that
    // stopped after the first match per file would report this file green
    // while the drawer header's observer overwrote the meta.
    expect(
      observers.filter((observer) => observer.path === "components/inbox/ApprovalsList.tsx"),
    ).toHaveLength(2);
  });

  it(`carries meta.errorTitle "${ERROR_TITLE}" on every one of them`, () => {
    // Reported as the sites that are missing it, so the failure names the file
    // and the line rather than a count that changed.
    expect(observers.filter((observer) => !observer.hasMeta)).toEqual([]);
  });
});

describe("the approvals-observer predicate", () => {
  it("reports a new observer that omits the meta", () => {
    const source = `
      const approvalsQ = useQuery({
        queryKey: ["approvals"],
        queryFn: () => api.approvals(),
        refetchInterval: 15000,
      });
    `;

    expect(findApprovalsObservers("pages/NewPage.tsx", source)).toEqual([
      { path: "pages/NewPage.tsx", line: 2, hasMeta: false },
    ]);
  });

  it("accepts an observer that carries it", () => {
    const source = `
      const approvals = useQuery({
        queryKey: ["approvals"],
        queryFn: () => api.approvals(),
        meta: { errorTitle: "Load approvals" },
      });
    `;

    expect(findApprovalsObservers("pages/NewPage.tsx", source)[0].hasMeta).toBe(true);
  });

  it("rejects a near-miss title, which is a different toast", () => {
    const source = `
      const approvals = useQuery({
        queryKey: ["approvals"],
        meta: { errorTitle: "Load approval" },
      });
    `;

    expect(findApprovalsObservers("pages/NewPage.tsx", source)[0].hasMeta).toBe(false);
  });

  it("finds both observers in one file", () => {
    const source = `
      const list = useQuery({ queryKey: ["approvals"], meta: { errorTitle: "Load approvals" } });
      const count = useQuery({ queryKey: ["approvals"], queryFn: api.approvals });
    `;

    expect(findApprovalsObservers("f.tsx", source).map((observer) => observer.hasMeta)).toEqual([
      true,
      false,
    ]);
  });

  it("ignores a different query, and a key that only starts with approvals", () => {
    // `["approvals", ticketId]` is a separate query with its own meta; the
    // shared-meta argument AC-8 rests on does not apply to it.
    const source = `
      const ticketApprovals = useQuery({ queryKey: ["approvals", ticketId], queryFn: api.approvals });
      const tickets = useQuery({ queryKey: ["tickets"], queryFn: api.tickets });
    `;

    expect(findApprovalsObservers("f.tsx", source)).toEqual([]);
  });

  it("ignores invalidateQueries, which is not an observer", () => {
    const source = `
      const qc = useQueryClient();
      const m = useMutation({
        mutationFn: api.resolveApproval,
        onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
      });
    `;

    expect(findApprovalsObservers("hooks/useThing.ts", source)).toEqual([]);
  });

  it("reads a type-argument form, which differs only by an annotation", () => {
    const source = `
      const approvals = useQuery<Approval[]>({ queryKey: ["approvals"], queryFn: api.approvals });
    `;

    expect(findApprovalsObservers("f.tsx", source)).toHaveLength(1);
  });
});
