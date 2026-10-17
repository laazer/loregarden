import type { TicketTreeNode } from "../../api/types";
import type { WorkspacePullRequest } from "../../api/workspaceDashboardApi";
import { workspaceDashboardPath, workspaceSlugFromPath } from "../appNavigation";
import { beginVisit, resolveCatchUpWindow, SAME_VISIT_MS } from "../catchUpWindow";
import {
  collectMilestones,
  highlightText,
  milestoneProgress,
  pullRequestHeadline,
  standingSentence,
} from "../workspaceDashboard";

const NOW = new Date("2026-10-17T12:00:00Z");
const DAY = 24 * 60 * 60 * 1000;

function pr(number: number, fields: Partial<WorkspacePullRequest> & { checks?: number } = {}): WorkspacePullRequest {
  const { checks = 1, ...rest } = fields;
  return {
    pull_request: {
      number,
      url: `https://github.com/o/r/pull/${number}`,
      title: `PR ${number}`,
      state: "open",
      is_draft: false,
      base: "main",
      head: `b${number}`,
      additions: 1,
      deletions: 0,
      changed_files: 1,
      review: "review_required",
      has_conflicts: false,
      checks: Array.from({ length: checks }, () => ({ name: "ci", outcome: "passing" as const, url: "" })),
      body: "",
      head_sha: "",
      mergeable_now: false,
      unsigned_commits: [],
    },
    ci: "passing",
    author: "a",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: new Date(NOW.getTime() - DAY).toISOString(),
    tier: "to_review",
    review_rank: number,
    files_truncated: false,
    tickets: [],
    overlaps: [],
    ...rest,
  };
}

describe("pullRequestHeadline", () => {
  it("says what a person catching up needs first", () => {
    const stale = new Date(NOW.getTime() - 15 * DAY).toISOString();
    const rows = [
      pr(1, { updated_at: stale, tier: "conflicting" }),
      pr(2, { updated_at: stale, checks: 0 }),
      pr(3),
    ];
    expect(pullRequestHeadline(rows, NOW)).toBe("2 have been idle over 7 days; 1 needs a rebase and 1 has no checks.");
    expect(pullRequestHeadline(rows.slice(0, 2), NOW)).toBe(
      "All 2 have been idle over 7 days; 1 needs a rebase and 1 has no checks.",
    );
    expect(pullRequestHeadline([pr(1)], NOW)).toBe("Every open PR is current and checked; review them in this order.");
  });

  it("says where the workspace stands in one sentence", () => {
    const rows = [pr(1), pr(2, { tier: "ready_to_merge" })];
    expect(standingSentence({ approvals: 14, blocked: 11, pullRequests: rows })).toBe(
      "14 approvals and 11 blocked tickets are waiting on you. 2 pull requests are open and 1 approved.",
    );
    expect(standingSentence({ approvals: 0, blocked: 0, pullRequests: [pr(1)] })).toBe(
      "Nothing is waiting on you. 1 pull request is open and it is not approved.",
    );
    expect(standingSentence({ approvals: 1, blocked: undefined, pullRequests: undefined })).toBe("");
  });
});

describe("highlightText", () => {
  it("names the target, the stage or the state, and folds repeats", () => {
    expect(highlightText({ kind: "landed", detail: "main", count: 10 })).toBe("Landed on main ×10");
    expect(highlightText({ kind: "moved", detail: "in_progress", count: 1 })).toBe("Moved to in progress");
    expect(highlightText({ kind: "stage_rejected", detail: "review", count: 2 })).toBe("Stage rejected at review ×2");
    expect(highlightText({ kind: "run_failed", detail: "", count: 1 })).toBe("Run failed");
  });
});

describe("milestoneProgress", () => {
  const node = (id: string, state: TicketTreeNode["state"], children: TicketTreeNode[] = [], member_link = false) =>
    ({
      id,
      external_id: id,
      title: id,
      state,
      priority: 0,
      work_item_type: "feature",
      workflow_stage_name: "",
      workflow_stage_status: "pending",
      child_count: children.length,
      children,
      member_link,
    }) as TicketTreeNode;

  it("counts every descendant once, and skips member-link copies", () => {
    const milestone = node("m", "in_progress", [
      node("a", "done", [node("a1", "blocked"), node("a2", "wont_do")]),
      node("b", "in_progress"),
      node("c", "parked"),
      node("copy", "done", [], true),
    ]);
    expect(milestoneProgress(milestone)).toEqual({ total: 5, finished: 2, inProgress: 1, blocked: 2 });
  });

  it("finds milestones at any depth of the unfiltered tree, with their children", () => {
    const nested = { ...node("m2", "backlog", [node("x", "done")]), work_item_type: "milestone" } as TicketTreeNode;
    const top = { ...node("m1", "in_progress", [node("y", "in_progress")]), work_item_type: "milestone" } as TicketTreeNode;
    const initiative = node("i", "in_progress", [nested]);
    expect(collectMilestones([top, initiative]).map((m) => [m.id, milestoneProgress(m).total])).toEqual([
      ["m1", 1],
      ["m2", 1],
    ]);
  });
});

describe("workspace dashboard paths", () => {
  it("round-trips a slug and leaves the Instances tab alone", () => {
    expect(workspaceSlugFromPath(workspaceDashboardPath("lore-eden"))).toBe("lore-eden");
    expect(workspaceSlugFromPath("/workspaces/blobert/")).toBe("blobert");
    expect(workspaceSlugFromPath("/workspaces/instances")).toBeNull();
    expect(workspaceSlugFromPath("/workspaces")).toBeNull();
    expect(workspaceSlugFromPath("/workspaces/a/b")).toBeNull();
  });
});

describe("catch-up window", () => {
  beforeEach(() => window.localStorage.clear());

  it("anchors on the previous visit, and a reload within the hour keeps that anchor", () => {
    expect(beginVisit("ws", NOW)).toEqual({ lastVisit: null, remembered: true });

    const later = new Date(NOW.getTime() + 2 * DAY);
    expect(beginVisit("ws", later).lastVisit).toEqual(NOW);
    const reload = new Date(later.getTime() + SAME_VISIT_MS / 2);
    expect(beginVisit("ws", reload).lastVisit).toEqual(NOW);
  });

  it("falls back to seven days without a usable visit, and says which span it used", () => {
    expect(resolveCatchUpWindow("last_visit", null, NOW)).toEqual({
      since: new Date(NOW.getTime() - 7 * DAY),
      span: "7d",
    });
    const ancient = new Date(NOW.getTime() - 400 * DAY);
    expect(resolveCatchUpWindow("last_visit", ancient, NOW).span).toBe("7d");
    const recent = new Date(NOW.getTime() - 3 * DAY);
    expect(resolveCatchUpWindow("last_visit", recent, NOW)).toEqual({ since: recent, span: "last_visit" });
    expect(resolveCatchUpWindow("30d", recent, NOW).since).toEqual(new Date(NOW.getTime() - 30 * DAY));
  });

  it("reports a blocked store instead of pretending to remember", () => {
    const getItem = jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    try {
      expect(beginVisit("ws", NOW)).toEqual({ lastVisit: null, remembered: false });
    } finally {
      getItem.mockRestore();
    }
  });
});
