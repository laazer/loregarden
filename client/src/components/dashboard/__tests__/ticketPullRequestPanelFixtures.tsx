/**
 * Shared harness for the TicketPullRequestPanel suites.
 *
 * The panel's tests outgrew one file (the 1200-line cap), so they are split by
 * subject — the base panel and a member's PR in TicketPullRequestPanel.test.tsx,
 * the route to the tree's PR and the states around it in
 * TicketPullRequestPanel.treePr.test.tsx — over this one set of fixtures.
 *
 * Importers call `jest.mock("../../../api/client")` themselves: the mock has to
 * be registered by the test module, and `mockApi` below is the cast of the
 * already-mocked client.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { PullRequestStatus, TicketPullRequest } from "../../../api/ticketPullRequestApi";
import { TicketPullRequestPanel } from "../TicketPullRequestPanel";

export const mockApi = api as jest.Mocked<typeof api>;

export const PR: PullRequestStatus = {
  number: 555,
  url: "https://github.com/o/r/pull/555",
  title: "feat(runs): detach agent processes",
  state: "open",
  is_draft: false,
  base: "main",
  head: "loregarden/lg-durable-remote-336",
  additions: 7522,
  deletions: 297,
  changed_files: 49,
  review: "not_required",
  has_conflicts: false,
  checks: [
    { name: "PR UX section", outcome: "passing", url: "https://ci/2" },
    { name: "Server (Python)", outcome: "failing", url: "https://ci/1" },
  ],
  body: "Detaches print-mode agent runs.",
  head_sha: "a".repeat(40),
  mergeable_now: false,
  unsigned_commits: [],
};

export const READY: PullRequestStatus = {
  ...PR,
  checks: PR.checks.map((check) => ({ ...check, outcome: "passing" as const })),
  mergeable_now: true,
};

export function lookup(overrides: Partial<TicketPullRequest>): TicketPullRequest {
  return {
    lookup: "found",
    branch: PR.head,
    pull_request: PR,
    error: "",
    recorded: null,
    ships_with: "",
    ships_with_root: "",
    is_tree_pull_request: false,
    ...overrides,
  };
}

export function renderPanel(data: TicketPullRequest, handlers: { onOpenPr?: () => void; onCommitPush?: () => void } = {}) {
  mockApi.ticketPullRequest.mockResolvedValue(data);
  mockApi.ciStatus.mockResolvedValue({ ci_status: null, auto_fix_history: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TicketPullRequestPanel ticketId="t1" isOpeningPr={false} isCommittingPush={false} {...handlers} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return container;
}

/**
 * The panel while the lookup is still on the wire: the query never settles, so
 * the loading state is what is on screen for the whole test (AC11).
 */
export function renderPending() {
  mockApi.ticketPullRequest.mockReturnValue(new Promise<TicketPullRequest>(() => {}));
  mockApi.ciStatus.mockResolvedValue({ ci_status: null, auto_fix_history: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TicketPullRequestPanel ticketId="t1" isOpeningPr={false} isCommittingPush={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return container;
}

/** A promise this test resolves by hand, to hold a mutation or a refetch in flight. */
export function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

/**
 * AC15(a) — the tree-member payload, with its four fields deliberately disagreeing.
 *
 * `branch` is the publish ref the lookup actually matched on
 * (`publish_ref_for(tree)` is asked first), `ships_with` is the integration
 * branch a repair turn can commit to, and the PR's head is neither of those two
 * things by accident: a fixture where they coincide passes whether the source
 * reads the right field or the wrong one. `PR` keeps its failing
 * "Server (Python)" check so the AC8 assertions have a failing check to find.
 */
export const TREE_MEMBER: TicketPullRequest = lookup({
  branch: "publish/integration/lg-ms-1",
  ships_with: "integration/lg-ms-1",
  // AC4: a fifth value, distinct from the three above. "lg-ms-1" is the root's
  // shareable id, not "integration/lg-ms-1" with the prefix taken off it, so a
  // source reading `ships_with` (or slicing it) cannot pass the link's target.
  ships_with_root: "lg-ms-1",
  is_tree_pull_request: true,
  pull_request: { ...PR, head: "publish/integration/lg-ms-1" },
});

/**
 * A tree ROOT: it ships with a tree, but the resolved PR's head *is* its own
 * branch, so `is_tree_pull_request` is false. This is the payload that tells
 * apart a gate on `is_tree_pull_request` from a gate on `ships_with` being
 * non-empty — the latter would wrongly add the provenance line here.
 */
export const TREE_ROOT: TicketPullRequest = lookup({
  branch: "integration/lg-ms-1",
  ships_with: "integration/lg-ms-1",
  is_tree_pull_request: false,
  pull_request: { ...PR, head: "integration/lg-ms-1" },
});

export const PROVENANCE = "Ships in its tree\u2019s pull request";
/** The link’s exact text (UI-2), which is also its whole accessible name. */
export const OPEN_TREE_PR = "Open the tree\u2019s pull request";

/**
 * The provenance line, found by structure rather than by its text (AC13): a
 * direct `p.prp-muted` child of `.prp`. The same class is used deeper inside the
 * checks section, and the same wording is used by a whole `ap-state` pane, so
 * neither the class nor the string identifies this element on its own.
 */
export function provenanceLine(container: HTMLElement): HTMLElement | null {
  return container.querySelector<HTMLElement>(".prp > p.prp-muted");
}
