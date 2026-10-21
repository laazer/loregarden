import {
  pullRequestStatusNote,
  withPullRequests,
  type BranchPullRequests,
  type BranchTriageLocalEntry,
} from "../branchTriageApi";

function local(name: string): BranchTriageLocalEntry {
  return {
    name,
    is_current: false,
    is_base: false,
    squash_merged: false,
    ahead: 1,
    behind: 0,
    dirty: false,
    upstream: null,
    diff_options: [],
    worktrees: [],
    linked_tickets: [],
    last_commit: { date: "", message: "" },
    issues: [],
  };
}

function listing(overrides: Partial<BranchPullRequests> = {}): BranchPullRequests {
  return {
    workspace_slug: "demo",
    state: "complete",
    listed_count: 2,
    total_count: 2,
    reason: "",
    pull_requests: {
      "feature/a": { state: "merged", is_draft: false, url: "u", number: 4, title: "A" },
    },
    ...overrides,
  };
}

describe("withPullRequests", () => {
  it("fills each branch's PR from the listing and leaves the rest null", () => {
    const merged = withPullRequests([local("feature/a"), local("feature/b")], listing());
    expect(merged.map((entry) => entry.pr?.number ?? null)).toEqual([4, null]);
  });

  it("paints every branch before the listing arrives", () => {
    const merged = withPullRequests([local("feature/a")], undefined);
    expect(merged).toHaveLength(1);
    expect(merged[0].pr).toBeNull();
  });
});

describe("pullRequestStatusNote", () => {
  const idle = { loading: false, error: null };

  it("says nothing once the listing is complete", () => {
    expect(pullRequestStatusNote(listing(), idle)).toBeNull();
  });

  it("is never silent while a branch's missing PR could still exist", () => {
    expect(pullRequestStatusNote(undefined, { loading: true, error: null })).not.toBeNull();
    expect(pullRequestStatusNote(undefined, { loading: false, error: "502" })).toContain("502");
    expect(
      pullRequestStatusNote(listing({ state: "unavailable", total_count: null, reason: "gh down" }), idle),
    ).toContain("gh down");
  });

  it("names how much of a partial listing was read", () => {
    const note = pullRequestStatusNote(
      listing({ state: "incomplete", listed_count: 500, total_count: 674, reason: "more pages" }),
      idle,
    );
    expect(note).toContain("500 of 674");
  });
});
