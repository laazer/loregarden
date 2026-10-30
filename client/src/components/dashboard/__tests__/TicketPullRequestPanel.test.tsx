import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { PullRequestStatus } from "../../../api/ticketPullRequestApi";
import { uiActionRegistry } from "../../../lib/agentActions/registry";
import {
  deferred,
  lookup,
  mockApi,
  OPEN_TREE_PR,
  PR,
  PROVENANCE,
  provenanceLine,
  READY,
  renderPanel,
  TREE_MEMBER,
  TREE_ROOT,
} from "./ticketPullRequestPanelFixtures";

jest.mock("../../../api/client");

describe("TicketPullRequestPanel", () => {
  beforeEach(() => jest.clearAllMocks());

  it("shows a PR GitHub has even when Loregarden never recorded one, failing checks first", async () => {
    renderPanel(lookup({}));

    expect(await screen.findByRole("link", { name: PR.title })).toHaveAttribute("href", PR.url);
    expect(screen.getByRole("status")).toHaveTextContent("Server (Python) is failing.");
    const checks = screen.getAllByRole("listitem");
    expect(checks[0]).toHaveTextContent("Server (Python)");
    expect(screen.getByRole("link", { name: "Server (Python)" })).toHaveAttribute("href", "https://ci/1");
    expect(screen.getByRole("link", { name: "Open on GitHub" })).toHaveAttribute("href", PR.url);
  });

  it("hands failing checks to the ticket's triage chat", async () => {
    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    renderPanel(lookup({}));

    fireEvent.click(await screen.findByRole("button", { name: "Ask triage to fix" }));

    await waitFor(() =>
      expect(mockApi.sendTriageMessage).toHaveBeenCalledWith("t1", expect.stringContaining("Server (Python): https://ci/1")),
    );
  });

  it("offers no fix for a merged PR and hides its checks", async () => {
    renderPanel(lookup({ pull_request: { ...PR, state: "merged" } }));

    expect(await screen.findByText("Merged into main.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask triage to fix" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Checks" })).not.toBeInTheDocument();
  });

  it("names the branch and offers to open a PR when GitHub has none", async () => {
    const onOpenPr = jest.fn();
    renderPanel(lookup({ lookup: "none", pull_request: null }), { onOpenPr, onCommitPush: jest.fn() });

    expect(await screen.findByText(PR.head)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open PR" }));
    expect(onOpenPr).toHaveBeenCalled();
  });

  it("offers no PR of its own to a ticket that ships with its tree", async () => {
    renderPanel(lookup({ lookup: "none", pull_request: null, ships_with: "integration/lg-ms-1" }), {
      onOpenPr: jest.fn(),
      onCommitPush: jest.fn(),
    });

    expect(await screen.findByText("integration/lg-ms-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open PR" })).not.toBeInTheDocument();
  });

  it("asks before merging, merges at the head on screen, and lists the cleanup", async () => {
    mockApi.mergeTicketPullRequest.mockResolvedValue({
      number: 555,
      merge_commit: "b".repeat(40),
      steps: [
        { step: "local branch", ok: true, detail: "deleted" },
        { step: "worktree /w/336", ok: false, detail: "kept: it has uncommitted changes" },
      ],
    });
    renderPanel(lookup({ pull_request: READY }));

    fireEvent.click(await screen.findByRole("button", { name: "Merge and clean up" }));
    expect(mockApi.mergeTicketPullRequest).not.toHaveBeenCalled();
    const confirm = screen.getByRole("button", { name: "Merge #555" });
    expect(confirm).toHaveFocus();
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(mockApi.mergeTicketPullRequest).toHaveBeenCalledWith("t1", { number: 555, head_sha: "a".repeat(40) }),
    );
    expect(await screen.findByText(/1 cleanup step need you/)).toBeInTheDocument();
    expect(screen.getByText("kept: it has uncommitted changes")).toBeInTheDocument();
  });

  it("offers agents the same merge, only while the button is offered", async () => {
    mockApi.mergeTicketPullRequest.mockResolvedValue({ number: 555, merge_commit: "", steps: [] });
    renderPanel(lookup({ pull_request: READY }));
    await screen.findByRole("button", { name: "Merge and clean up" });
    expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request");

    await expect(
      uiActionRegistry.run("ticket.merge_pull_request", { ticket_id: "other", number: 555, head_sha: "c".repeat(40) }),
    ).rejects.toThrow("open the ticket first");
    expect(mockApi.mergeTicketPullRequest).not.toHaveBeenCalled();

    await uiActionRegistry.run("ticket.merge_pull_request", { ticket_id: "t1", number: 555, head_sha: "c".repeat(40) });
    expect(mockApi.mergeTicketPullRequest).toHaveBeenCalledWith("t1", { number: 555, head_sha: "c".repeat(40) });
    await waitFor(() => expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request"));
  });

  it("does not offer the merge to agents while GitHub blocks it", async () => {
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false } }));
    await screen.findByText(/still block the merge/);
    expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request");
  });

  it("cancels the merge with Escape", async () => {
    renderPanel(lookup({ pull_request: READY }));
    fireEvent.click(await screen.findByRole("button", { name: "Merge and clean up" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Merge #555" }), { key: "Escape" });
    expect(screen.getByRole("button", { name: "Merge and clean up" })).toBeInTheDocument();
    expect(mockApi.mergeTicketPullRequest).not.toHaveBeenCalled();
  });

  it("offers no merge, and says so, while GitHub's rules still block it", async () => {
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false } }));
    expect(await screen.findByText(/rules for main still block the merge/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument();
  });

  it("names the unsigned commits when they are what blocks the merge", async () => {
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false, unsigned_commits: ["20a44d8c test: x"] } }));
    expect(await screen.findByText(/1 commit is unsigned, and main requires signed commits/)).toBeInTheDocument();
    expect(screen.getByText("20a44d8c test: x")).toBeInTheDocument();
  });

  it("does not call a failed lookup 'no pull request', and links the recorded one", async () => {
    renderPanel(
      lookup({
        lookup: "failed",
        pull_request: null,
        error: "HTTP 401: Bad credentials",
        recorded: { url: "https://github.com/o/r/pull/9", number: "9", title: "older" },
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Bad credentials");
    expect(screen.queryByText(/No pull request/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#9 older" })).toHaveAttribute("href", "https://github.com/o/r/pull/9");
  });

  it("keeps the recorded auto-fix attempts visible when GitHub cannot be asked", async () => {
    renderPanel(lookup({ lookup: "failed", pull_request: null, error: "HTTP 401: Bad credentials" }));
    mockApi.ciStatus.mockResolvedValue({
      ci_status: null,
      auto_fix_history: [
        {
          id: "a1",
          ci_run_result_id: "r1",
          attempt_number: 1,
          run_id: null,
          status: "refused",
          result_summary: "the pull request's head moved",
          created_at: "2026-10-12T10:00:00",
          completed_at: "2026-10-12T10:00:00",
        },
      ],
    });

    expect(await screen.findByText("the pull request's head moved")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry auto-fix" })).not.toBeInTheDocument();
  });

  it("offers the merge again when a refetch says GitHub's rules no longer block it", async () => {
    // The third test in the refetch family, and the mirror of the two in
    // TicketPullRequestPanel.treePr.test.tsx (:699 and :733), which drive the
    // sibling guard (MergeAndCleanUpButton.tsx:62) in both directions. This one
    // drives `mergeable_now` (:64), which every other assertion in the suite
    // only ever reads on a fresh mount — so a guard that latches false once a
    // blocked PR has been seen (a `useRef` written in render, say) passes all of
    // them. It is the panel's most ordinary live transition: the tab refetches
    // on Refresh and every 30s precisely while checks are pending, and
    // `mergeable_now` is what flips when they go green. Latching it is fail-safe
    // but it strands the operator, refusing both the merge and the
    // `ticket.merge_pull_request` action until the page is reloaded.
    //
    // Neither payload overrides `is_tree_pull_request`, which `lookup` defaults
    // to false, so the opening assertions hold via `!pr.mergeable_now` and not
    // via the tree guard — the mirror of the vacuity trap the tree tests close.
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false } }));
    // A marker proving that payload has rendered, so the two assertions below
    // cannot pass merely because the lookup is still on the wire.
    // `provenanceLine` is null for a non-tree payload, so it is not one here.
    await screen.findByRole("link", { name: PR.title });
    expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument();
    expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request");

    // After the render: `renderPanel` installs `mockResolvedValue`, which wins
    // over a `mockReturnValueOnce` installed before it.
    const pending = deferred<ReturnType<typeof lookup>>();
    mockApi.ticketPullRequest.mockReturnValueOnce(pending.promise as never);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    pending.settle(lookup({ pull_request: READY }));

    // Both halves: the render guard (MergeAndCleanUpButton.tsx:64) and the
    // `useAgentAction` availability predicate (:59) are two independent reads of
    // the same prop, and a capture of either one alone is a mutant the other
    // assertion cannot see.
    await waitFor(() => expect(screen.getByRole("button", { name: "Merge and clean up" })).toBeInTheDocument());
    await waitFor(() => expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request"));
  });

  it("retracts the merge when a refetch says GitHub's rules now block it", async () => {
    // The fourth and last member of the refetch family, and the exact mirror of
    // the test above it: that one drives `mergeable_now` (MergeAndCleanUpButton
    // .tsx:64 and :59) false -> true, this one true -> false. Every other
    // `mergeable_now: false` assertion in both panel suites is a fresh mount, so
    // a guard that latches true once a mergeable PR has been seen (a `useRef`
    // written in render, say) passes all 72 of them.
    //
    // This direction matters more than its mirror's. Latching false was
    // fail-safe: it withheld the merge. Latching true is fail-dangerous — the
    // operator is offered "Merge and clean up", and `ticket.merge_pull_request`
    // stays available, on a PR GitHub has since decided it will not merge (a
    // conflict appears, a required check regresses, a signature rule starts
    // applying). The server re-checks and refuses, so nothing is destroyed, but
    // `verdict()` (TicketPullRequestPanel.tsx:90) re-reads `pr` fresh on every
    // render, so the panel would print "Checks pass, but GitHub's rules for
    // <base> still block the merge." directly above an offered merge button.
    //
    // Neither payload overrides `is_tree_pull_request`, which `lookup` defaults
    // to false, so the closing assertions hold via `pr.mergeable_now` and not
    // via the sibling tree guard (MergeAndCleanUpButton.tsx:62).
    renderPanel(lookup({ pull_request: READY }));
    // A marker proving that payload has rendered, so the two assertions below
    // cannot pass merely because the lookup is still on the wire.
    // `provenanceLine` is null for a non-tree payload, so it is not one here.
    await screen.findByRole("link", { name: PR.title });
    expect(screen.getByRole("button", { name: "Merge and clean up" })).toBeInTheDocument();
    expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request");

    // After the render: `renderPanel` installs `mockResolvedValue`, which wins
    // over a `mockReturnValueOnce` installed before it.
    const pending = deferred<ReturnType<typeof lookup>>();
    mockApi.ticketPullRequest.mockReturnValueOnce(pending.promise as never);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    pending.settle(lookup({ pull_request: { ...READY, mergeable_now: false } }));

    // Both halves: the render guard (MergeAndCleanUpButton.tsx:64) and the
    // `useAgentAction` availability predicate (:59) are two independent reads of
    // the same prop, and a capture of either one alone is a mutant the other
    // assertion cannot see.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument());
    await waitFor(() => expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request"));
  });
});

/**
 * The PR tab for a ticket inside a tree.
 *
 * The question these pin is the operator's: "is the pull request carrying this
 * ticket's work ready, and if not what blocks it?" So a member whose tree PR
 * resolves gets the full panel, with one line saying whose PR it is.
 *
 * Every test here pins its state by structure, never by the shared
 * "Ships in its tree's pull request" wording (AC13): that string is deliberately
 * used by both the provenance line and the tree-has-no-PR-yet pane, so an
 * assertion on it alone passes in both and proves nothing.
 */
describe("TicketPullRequestPanel, a tree member's pull request", () => {
  beforeEach(() => jest.clearAllMocks());

  it("renders the full panel for a member, with the provenance line above the header", async () => {
    // AC7 + AC13: the line AND the PR title, verdict and failing check together.
    const container = renderPanel(TREE_MEMBER);

    expect(await screen.findByRole("link", { name: PR.title })).toHaveAttribute("href", PR.url);
    expect(screen.getByRole("status")).toHaveTextContent("Server (Python) is failing.");
    expect(screen.getByRole("link", { name: "Server (Python)" })).toHaveAttribute("href", "https://ci/1");
    expect(screen.getByRole("heading", { name: "Checks" })).toBeInTheDocument();
    expect(screen.getByText("Description")).toBeInTheDocument();

    const line = provenanceLine(container);
    expect(line).not.toBeNull();
    expect(line).toHaveTextContent(PROVENANCE);
  });

  it("puts the provenance line first in the panel, as the header's sibling", async () => {
    // AC12: first child of `.prp`, immediately before `header.prp-head` — not
    // inside the header, where it would join the badge/#number/branches cluster.
    const container = renderPanel(TREE_MEMBER);
    await screen.findByRole("link", { name: PR.title });

    const panel = container.querySelector(".prp");
    const line = provenanceLine(container);
    expect(panel?.firstElementChild).toBe(line);
    expect(line?.nextElementSibling?.tagName).toBe("HEADER");
    expect(line?.nextElementSibling).toHaveClass("prp-head");
  });

  it("names the tree's branch, after the text, in the existing muted and branch classes", async () => {
    // AC12 + AC11: the markup is exactly one <p> and one <code>, reusing classes
    // that already carry the colour and the font size, so no literal of either
    // can be introduced here. Static text: no role, no aria-live, no inline style.
    const container = renderPanel(TREE_MEMBER);
    await screen.findByRole("link", { name: PR.title });

    const line = provenanceLine(container);
    expect(line?.tagName).toBe("P");
    expect(line?.className).toBe("prp-muted");
    expect(line?.getAttribute("role")).toBeNull();
    expect(line?.getAttribute("aria-live")).toBeNull();
    expect(line?.getAttribute("style")).toBeNull();
    expect(line?.textContent).toMatch(
      /^Ships in its tree’s pull request\s+integration\/lg-ms-1\s+Open the tree’s pull request$/,
    );

    const branch = line?.querySelector("code");
    expect(branch?.className).toBe("prp-branch");
    expect(branch).toHaveTextContent("integration/lg-ms-1");
    expect(branch?.getAttribute("style")).toBeNull();
    expect(line?.querySelectorAll("code")).toHaveLength(1);
  });

  it("adds nothing for a ticket that ships its own branch", async () => {
    // AC7: is_tree_pull_request false, ships_with empty — no line, no badge.
    const container = renderPanel(lookup({}));
    await screen.findByRole("link", { name: PR.title });

    expect(provenanceLine(container)).toBeNull();
    expect(screen.queryByText(PROVENANCE)).not.toBeInTheDocument();
  });

  it("keeps the merge button and the agent action on a tree root's own mergeable PR", async () => {
    // AC9, the other side: is_tree_pull_request is false for the root, so its
    // own mergeable PR still offers both the button and the registered action.
    renderPanel({ ...TREE_ROOT, pull_request: { ...TREE_ROOT.pull_request!, ...READY } });
    await screen.findByRole("link", { name: PR.title });

    expect(screen.getByRole("button", { name: "Merge and clean up" })).toBeInTheDocument();
    expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request");
  });

  it("still says the merge is ready here on a tree root's own mergeable PR", async () => {
    // AC2: the root takes the unchanged branch, byte for byte. Asserted over the
    // same payload as the AC9 merge-button test directly above, so the wording
    // and the button cannot answer "can I merge here?" differently.
    renderPanel({ ...TREE_ROOT, pull_request: { ...TREE_ROOT.pull_request!, ...READY } });
    await screen.findByRole("link", { name: PR.title });

    expect(screen.getByText("Checks pass; ready to merge.")).toBeInTheDocument();
    expect(screen.getByRole("status").className).toBe("prp-verdict prp-verdict--good");
  });

  it("adds nothing for a tree root, whose own branch is the PR's head", async () => {
    // AC14(a): the gate is `is_tree_pull_request`, not `ships_with` being
    // non-empty. This payload has a non-empty ships_with and must stay bare.
    const container = renderPanel(TREE_ROOT);
    await screen.findByRole("link", { name: PR.title });

    expect(provenanceLine(container)).toBeNull();
    expect(screen.queryByText(PROVENANCE)).not.toBeInTheDocument();
  });

  it("offers a member the same actions as any other failing PR", async () => {
    // AC8: title link, Open on GitHub, every failing check's link, Ask triage, Refresh.
    renderPanel(TREE_MEMBER);

    expect(await screen.findByRole("link", { name: PR.title })).toHaveAttribute("href", PR.url);
    expect(screen.getByRole("link", { name: "Open on GitHub" })).toHaveAttribute("href", PR.url);
    expect(screen.getByRole("link", { name: "Server (Python)" })).toHaveAttribute("href", "https://ci/1");
    expect(screen.getByRole("button", { name: "Ask triage to fix" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("offers no merge on a member's tab, and registers no agent action, even when mergeable", async () => {
    // AC9: both the button and the registered `ticket.merge_pull_request` agent
    // action are absent for a member's mergeable tree PR, whatever mergeable_now says.
    renderPanel({ ...TREE_MEMBER, pull_request: { ...TREE_MEMBER.pull_request!, ...READY } });
    await screen.findByRole("link", { name: PR.title });

    expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument();
    expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request");
  });

  it("tells a member where the merge is, instead of claiming it is ready here", async () => {
    // AC1 + AC5: on a member's mergeable tree PR the green line names the tree's
    // pull request as the place the merge happens, rather than asserting a merge
    // this tab does not offer. Written as a literal with an ASCII apostrophe
    // (U+0027), matching verdict's sibling strings: OPEN_TREE_PR is the link's
    // text and renders U+2019, so building the sentence from it would fail as
    // "unable to find an element" rather than as a wrong codepoint.
    renderPanel({ ...TREE_MEMBER, pull_request: { ...TREE_MEMBER.pull_request!, ...READY } });
    await screen.findByRole("link", { name: PR.title });

    expect(screen.getByText("Checks pass; merge it from the tree's pull request.")).toBeInTheDocument();
    // AC2's negative: a positive-only assertion would pass if BOTH sentences rendered.
    expect(screen.queryByText(/ready to merge/)).toBeNull();
    // AC1: tone stays "good" — the state IS good, the merge is simply elsewhere.
    // A re-toned member branch rewrites no sentence, so only the class sees it.
    expect(screen.getByRole("status").className).toBe("prp-verdict prp-verdict--good");
  });

  it("says the same thing to a member whose payload names no root to link to", async () => {
    // AC4: verdict gains exactly one new string. With ships_with_root empty the
    // .prp-tree-root link does not render, and the sentence is unchanged — the
    // merge is still the tree PR's. There is no second wording keyed on the link.
    renderPanel({
      ...TREE_MEMBER,
      ships_with_root: "",
      pull_request: { ...TREE_MEMBER.pull_request!, ...READY },
    });
    await screen.findByRole("link", { name: PR.title });

    expect(screen.queryByRole("link", { name: OPEN_TREE_PR })).toBeNull();
    expect(screen.getByText("Checks pass; merge it from the tree's pull request.")).toBeInTheDocument();
    expect(screen.queryByText(/ready to merge/)).toBeNull();
  });

  /**
   * AC7: only the LAST of `verdict`'s twelve returns splits, so the new branch
   * has to sit below the others. Every test above renders READY and so cannot
   * see a branch placed higher up: inserted above the `!mergeable_now` return it
   * would tell a member "checks pass" over a PR GitHub is still holding, and
   * above the pending one, over checks that are still running. The returns above
   * `failing` are already pinned by the AC7/AC13 test at the top of this
   * describe; these are the seven below it that a member can reach.
   */
  const OTHER_MEMBER_VERDICTS: ReadonlyArray<[string, Partial<PullRequestStatus>]> = [
    ["Conflicts with main; the branch needs a rebase.", { has_conflicts: true }],
    ["A reviewer requested changes.", { review: "changes_requested" }],
    ["1 check still running.", { checks: [{ ...READY.checks[0], outcome: "pending" }, READY.checks[1]] }],
    ["Draft: mark it ready for review on GitHub.", { is_draft: true }],
    ["Checks pass; waiting on a review.", { review: "review_required" }],
    ["1 commit is unsigned, and main requires signed commits.", { unsigned_commits: ["0123456 feat: detach runs"] }],
    ["Checks pass, but GitHub's rules for main still block the merge.", { mergeable_now: false }],
  ];

  it.each(OTHER_MEMBER_VERDICTS)('still reads "%s" on a member whose PR is in that state', async (reads, pr) => {
    const c = renderPanel({ ...TREE_MEMBER, pull_request: { ...TREE_MEMBER.pull_request!, ...READY, ...pr } });
    await within(c).findByText(reads);

    expect(within(c).getByRole("status")).toHaveTextContent(reads);
    expect(within(c).queryByText(/merge it from the tree/)).toBeNull();
  });

  it("tells triage to fix it on the tree's integration branch, not the publish ref", async () => {
    // AC8 + AC14(b). The negatives are the point: `branch` here is the publish
    // ref no worktree is ever on, and the old wording named no branch at all.
    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    renderPanel(TREE_MEMBER);

    fireEvent.click(await screen.findByRole("button", { name: "Ask triage to fix" }));

    await waitFor(() => expect(mockApi.sendTriageMessage).toHaveBeenCalled());
    const message = mockApi.sendTriageMessage.mock.calls[0][1] as string;
    expect(message).toContain("Read the failing logs, find the first real failure, fix it on integration/lg-ms-1 and push.");
    expect(message).not.toContain("this ticket's branch");
    expect(message).not.toContain("publish/integration/lg-ms-1");
    expect(message).toContain("Server (Python): https://ci/1");
  });

  it("still tells triage 'this ticket's branch' when the PR is the ticket's own", async () => {
    // AC14(b): the empty-treeBranch line is unchanged, byte for byte.
    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    renderPanel(lookup({}));

    fireEvent.click(await screen.findByRole("button", { name: "Ask triage to fix" }));

    await waitFor(() => expect(mockApi.sendTriageMessage).toHaveBeenCalled());
    const message = mockApi.sendTriageMessage.mock.calls[0][1] as string;
    expect(message).toContain(
      "Read the failing logs, find the first real failure, fix it on this ticket's branch, and push.",
    );
    expect(message).not.toContain("integration/lg-ms-1");
  });
});
