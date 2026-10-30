"""The PR tab's "Merge and clean up": merge only what GitHub says can ship, then tidy.

GitHub is faked at `run_gh`; git is real, so the cleanup is checked against a
real worktree, a real local branch, and what the fake was asked to delete.
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, ORCHESTRATED_DENIED_MCP_TOOLS, McpTool
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import pull_request_merge, ticket_pull_request
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.publish_ref import publish_ref_for
from loregarden.services.pull_request_merge import MergeFailed, MergeRefused, merge_and_clean_up
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo

BRANCH = "loregarden/lg-1"
SHA = "a" * 40
#: The tree rooted at the `ticket` fixture, and the head its PR has on GitHub.
INTEGRATION = "integration/lg-1"
TREE_HEAD = publish_ref_for(INTEGRATION)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "branch", BRANCH)
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="merge", name="merge", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(external_id="lg-1", workspace_id=workspace.id, title="t", branch=BRANCH)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="member")
def member_fixture(session, workspace, ticket, repo):
    """A ticket inside `ticket`'s tree. Its work ships in the root's PR, so the
    PR its tab now resolves is the tree's — and merging it would squash the
    whole tree to the base and delete the branch its siblings are still on."""
    git(repo, "branch", INTEGRATION)
    member = Ticket(
        external_id="lg-2", workspace_id=workspace.id, title="c", parent_ticket_id=ticket.id
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return member


def _pr(**overrides):
    payload = {
        "number": 7,
        "url": "https://github.com/o/r/pull/7",
        "title": "t",
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": BRANCH,
        "headRefOid": SHA,
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    return {**payload, **overrides}


class FakeGitHub:
    """Answers the four `gh` calls the merge makes, and records them."""

    def __init__(self, pr: dict, *, merge_exit: int = 0, state_after: str = "MERGED"):
        self.pr = pr
        self.merge_exit = merge_exit
        self.state_after = state_after
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd, timeout=None, gh_token=None):
        self.calls.append(list(args))
        if args[:2] == ["pr", "view"] and "state,mergeCommit" in args:
            out = {"state": self.state_after, "mergeCommit": {"oid": "m" * 40}}
            return subprocess.CompletedProcess(args, 0, json.dumps(out), "")
        if args[:2] == ["pr", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(self.pr), "")
        if args[:2] == ["pr", "merge"]:
            return subprocess.CompletedProcess(args, self.merge_exit, "", "merge refused by test")
        if args[0] == "api" and args[1].endswith("/commits?per_page=100"):
            return subprocess.CompletedProcess(args, 0, "20a44d8c test: unsigned\n", "")
        if args[:3] == ["api", "-X", "DELETE"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected gh call {args}")

    def did(self, *prefix: str) -> bool:
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


@pytest.fixture(name="github")
def github_fixture():
    def install(fake: FakeGitHub):
        stack = [
            mock.patch.object(ticket_pull_request, "run_gh", fake),
            mock.patch.object(pull_request_merge, "run_gh", fake),
        ]
        for patcher in stack:
            patcher.start()
        return fake

    yield install
    mock.patch.stopall()


def test_merges_pinned_to_the_head_it_was_shown_then_cleans_up(
    session, workspace, ticket, repo, github
):
    # Under `worktree_base` (`<repo parent>/.worktrees`), which is where
    # `worktree_service` actually creates checkouts — and the only place the
    # release will remove one from (lg-external-harness-1026 AC9: containment
    # is a strict descendant of the base, not a string prefix of it). The
    # assertion below is unchanged; the fixture just stops describing a layout
    # production never produces.
    tree = repo.parent / ".worktrees" / "lg-1-tree"
    git(repo, "worktree", "add", "-q", str(tree), BRANCH)
    run = AgentRun(
        run_code="r1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.add(
        Worktree(
            agent_run_id=run.id,
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            branch=BRANCH,
            worktree_path=str(tree),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    # The receipt the gate now requires (lg-external-harness-1026 AC14/AC18).
    # A PR merge happens on GitHub; what releases the checkout is the record
    # that the work landed, which `land_ticket` / `publish_tree` write. A tree
    # with no such record is kept — the refusal is the case below this one.
    (tree / "merged.txt").write_text("the work this PR carried\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "the work this PR carried")
    grant_receipt(session, ticket, repo, branch=BRANCH)
    fake = github(FakeGitHub(_pr()))

    result = merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)

    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls
    assert result.merge_commit == "m" * 40
    assert all(step.ok for step in result.steps), result.steps
    assert not tree.exists()
    assert git(repo, "branch", "--list", BRANCH).stdout.strip() == ""
    assert fake.did("api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{BRANCH}")
    row = session.exec(select(Worktree)).one()
    assert row.state is WorktreeState.CLEANUP


@pytest.mark.parametrize(
    ("pr", "number", "head", "match"),
    [
        (_pr(mergeStateStatus="BLOCKED"), 7, SHA, "cannot merge yet"),
        (_pr(), 7, "b" * 40, "new commits"),
        (_pr(), 8, SHA, "no longer"),
    ],
)
def test_refuses_without_asking_github_to_merge(
    session, workspace, ticket, github, pr, number, head, match
):
    fake = github(FakeGitHub(pr))
    with pytest.raises(MergeRefused, match=match):
        merge_and_clean_up(session, ticket, workspace, number=number, head_sha=head)
    assert not fake.did("pr", "merge")


def test_a_merge_gh_reports_but_github_does_not_show_is_a_failure(
    session, workspace, ticket, repo, github
):
    github(FakeGitHub(_pr(), state_after="OPEN"))
    with pytest.raises(MergeFailed, match="'OPEN'"):
        merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)
    assert git(repo, "branch", "--list", BRANCH).stdout.strip(), "nothing cleaned up"


def test_a_refused_merge_cleans_up_nothing(session, workspace, ticket, repo, github):
    fake = github(FakeGitHub(_pr(), merge_exit=1))
    with pytest.raises(MergeFailed, match="merge refused by test"):
        merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)
    assert not fake.did("api")
    assert git(repo, "branch", "--list", BRANCH).stdout.strip()


def test_a_worktree_with_uncommitted_work_is_kept_and_said_so(
    session, workspace, ticket, repo, github
):
    tree = repo.parent / "lg-1-tree"
    git(repo, "worktree", "add", "-q", str(tree), BRANCH)
    # A row and a receipt (lg-external-harness-1026 AC18), so the premise this
    # test is named for is the one that fires. Without a `worktrees` row there
    # is no owner to resolve a receipt for and the gate refuses on that
    # instead; without a receipt it refuses on that. The uncommitted file is
    # what the assertion is about, so it has to be what decides.
    (tree / "committed.txt").write_text("the work this PR carried\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "the work this PR carried")
    grant_receipt(session, ticket, repo, branch=BRANCH)
    run = AgentRun(
        run_code="r-dirty",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.add(
        Worktree(
            agent_run_id=run.id,
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            branch=BRANCH,
            worktree_path=str(tree),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    (tree / "unsaved.txt").write_text("work in progress\n")
    github(FakeGitHub(_pr()))

    result = merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)

    by_step = {step.step: step for step in result.steps}
    assert not by_step[f"worktree {tree}"].ok
    assert "uncommitted" in by_step[f"worktree {tree}"].detail
    assert tree.exists() and (tree / "unsaved.txt").exists()
    assert not by_step["local branch"].ok, "a branch still checked out is not deleted"
    assert by_step["remote branch"].ok


def test_a_tree_roots_pr_is_looked_up_on_its_integration_branch(
    session, workspace, ticket, repo, github
):
    """`publish_tree` publishes a root's integration branch as ``publish/<branch>``,
    so its PR lives there."""
    git(repo, "branch", "integration/lg-1")
    session.add(
        Ticket(external_id="lg-2", workspace_id=workspace.id, title="c", parent_ticket_id=ticket.id)
    )
    session.commit()
    published = publish_ref_for("integration/lg-1")
    fake = github(FakeGitHub(_pr(headRefName=published)))

    found = ticket_pull_request.ticket_pull_request(session, ticket, workspace)

    assert fake.calls[0][:3] == ["pr", "view", published]
    assert found.branch == published
    assert found.pull_request is not None and found.pull_request.mergeable_now


def test_a_published_pr_cleans_up_the_branch_it_publishes_and_its_remote_name(
    session, workspace, ticket, repo, github
):
    """The head is ``publish/<branch>`` on the remote; what is held here is the branch."""
    fake = github(FakeGitHub(_pr(headRefName=publish_ref_for(BRANCH))))

    result = merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)

    assert all(step.ok for step in result.steps), result.steps
    assert git(repo, "branch", "--list", BRANCH).stdout.strip() == ""
    remote = f"repos/{{owner}}/{{repo}}/git/refs/heads/{publish_ref_for(BRANCH)}"
    assert fake.did("api", "-X", "DELETE", remote)


def test_a_blocked_pr_names_its_unsigned_commits_and_a_clean_one_is_not_asked(
    session, workspace, ticket, github
):
    fake = github(FakeGitHub(_pr(mergeStateStatus="BLOCKED")))
    blocked = ticket_pull_request.ticket_pull_request(session, ticket, workspace).pull_request
    assert blocked is not None and blocked.unsigned_commits == ["20a44d8c test: unsigned"]

    fake.pr = _pr()
    fake.calls.clear()
    clean = ticket_pull_request.ticket_pull_request(session, ticket, workspace).pull_request
    assert clean is not None and clean.unsigned_commits == []
    assert not any(call[0] == "api" for call in fake.calls)


# --- agents: the same merge over MCP --------------------------------------------------


def _mcp(session, arguments, **kwargs):
    name = McpTool.MERGE_PULL_REQUEST.value
    return execute_tool(session, name, normalize_tool_arguments(name, arguments), **kwargs)


def test_an_agent_merges_through_mcp_with_the_same_checks(session, workspace, ticket, github):
    fake = github(FakeGitHub(_pr()))

    result = json.loads(_mcp(session, {"ticket_id": "lg-1", "number": 7, "head_sha": SHA}))

    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls
    assert result["merge_commit"] == "m" * 40
    assert {step["step"] for step in result["steps"]} >= {"local branch", "remote branch"}


def test_an_agent_without_a_head_sha_merges_the_head_github_reports(
    session, workspace, ticket, github
):
    fake = github(FakeGitHub(_pr()))
    _mcp(session, {"ticket_id": "lg-1", "number": "7"})
    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls


def test_an_agent_is_refused_what_a_person_would_be(session, workspace, ticket, github):
    fake = github(FakeGitHub(_pr(mergeStateStatus="BLOCKED")))
    with pytest.raises(ValueError, match="cannot merge yet"):
        _mcp(session, {"ticket_id": "lg-1", "number": 7, "head_sha": SHA})
    assert not fake.did("pr", "merge")


def test_merging_is_gated_and_never_a_pipeline_stages_to_do():
    """Not auto-approved: a gated run asks first. And a stage agent may not merge
    the ticket it was dispatched for."""
    assert McpTool.MERGE_PULL_REQUEST not in AUTO_APPROVED_MCP_TOOLS
    assert McpTool.MERGE_PULL_REQUEST in ORCHESTRATED_DENIED_MCP_TOOLS


def test_a_pipeline_agent_calling_it_is_refused(session, workspace, ticket, github):
    fake = github(FakeGitHub(_pr()))
    with pytest.raises(ValueError):
        _mcp(session, {"ticket_id": "lg-1", "number": 7, "head_sha": SHA}, orchestrated=True)
    assert not fake.did("pr", "merge")


# --- a tree member may read its tree's PR, and may not merge it -----------------------


def test_a_tree_member_is_refused_its_trees_merge(session, workspace, member, repo, github):
    """AC3(b)/AC9(a): the refusal is an instruction, pinned to the character — it
    names the PR the operator clicked, the branch (which carries the root's
    external id, so they can navigate without a second lookup), the reason and
    where not to do it. Nothing is merged, no worktree is removed and no branch
    is deleted."""
    tree = repo.parent / "lg-1-tree"
    git(repo, "worktree", "add", "-q", str(tree), BRANCH)
    fake = github(FakeGitHub(_pr(headRefName=TREE_HEAD)))

    with pytest.raises(MergeRefused) as refused:
        merge_and_clean_up(session, member, workspace, number=7, head_sha=SHA)

    assert str(refused.value) == (
        f"#7 is the pull request for {TREE_HEAD}, this ticket's tree; "
        "it merges when that tree's root completes, not from here."
    )
    assert not fake.did("pr", "merge")
    assert not fake.did("api", "-X", "DELETE")
    assert tree.exists(), "a sibling's worktree"
    assert git(repo, "branch", "--list", INTEGRATION).stdout.strip(), "the tree's branch"
    assert git(repo, "branch", "--list", BRANCH).stdout.strip()


@pytest.mark.parametrize(
    ("pr", "number", "head", "match"),
    [
        # After the identity check: a number that is not this PR still reads
        # "refresh the tab", which is the honest answer for a stale tab.
        (_pr(headRefName=TREE_HEAD), 8, SHA, "no longer"),
        # Before the staleness and mergeability checks, which are less fundamental.
        (_pr(headRefName=TREE_HEAD), 7, "b" * 40, "this ticket's tree"),
        (_pr(headRefName=TREE_HEAD, mergeStateStatus="BLOCKED"), 7, SHA, "this ticket's tree"),
    ],
)
def test_where_the_tree_refusal_sits_among_the_other_refusals(
    session, workspace, member, github, pr, number, head, match
):
    """AC3(b) placement: immediately after the identity check, before `head_sha`."""
    fake = github(FakeGitHub(pr))

    with pytest.raises(MergeRefused, match=match):
        merge_and_clean_up(session, member, workspace, number=number, head_sha=head)

    assert not fake.did("pr", "merge")


def test_an_agent_is_refused_its_trees_merge_with_a_sentence_it_can_act_on(
    session, workspace, member, github
):
    """AC9(d): the same sentence reaches `loregarden_merge_pull_request`, so an
    agent reading it can go to the root named in the branch."""
    fake = github(FakeGitHub(_pr(headRefName=TREE_HEAD)))

    with pytest.raises(ValueError) as refused:
        _mcp(session, {"ticket_id": "lg-2", "number": 7, "head_sha": SHA})

    assert TREE_HEAD in str(refused.value)
    assert "it merges when that tree's root completes, not from here." in str(refused.value)
    assert not fake.did("pr", "merge")


def test_a_tree_root_still_merges_its_own_tree_pull_request(
    session, workspace, ticket, member, repo, github
):
    """The false-positive the `bool(ships_with)` gate exists to prevent, on the
    merge path: a root's own tree PR has head `publish/integration/<root>`, which
    is not the root's own branch. Refusing on the head comparison alone would take
    the merge away from the surface's main use today — 215 live roots have members.
    """
    fake = github(FakeGitHub(_pr(headRefName=TREE_HEAD)))

    result = merge_and_clean_up(session, ticket, workspace, number=7, head_sha=SHA)

    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls
    assert result.merge_commit == "m" * 40
    assert git(repo, "branch", "--list", INTEGRATION).stdout.strip() == ""
    assert fake.did("api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{TREE_HEAD}")


def test_a_members_own_published_pr_is_not_refused_as_its_trees(
    session, workspace, member, repo, github
):
    """A member that hand-opened a PR on its own branch, published: the head is
    `publish/<own>`, which *is* its own branch, so it is the member's to merge.
    A refusal keyed on `pr.head != own` instead of `branch_for_pr_head(pr.head)`
    would strand it, with the sentence telling the operator to go to a root that
    has nothing to do with this PR."""
    own = resolve_ticket_branch(member)
    git(repo, "branch", own)
    fake = github(FakeGitHub(_pr(headRefName=publish_ref_for(own))))

    result = merge_and_clean_up(session, member, workspace, number=7, head_sha=SHA)

    assert ["pr", "merge", "7", "--squash", "--match-head-commit", SHA] in fake.calls
    assert result.merge_commit == "m" * 40
    assert git(repo, "branch", "--list", own).stdout.strip() == ""
    assert git(repo, "branch", "--list", INTEGRATION).stdout.strip(), "the tree's branch"


def test_a_member_whose_lookup_failed_reads_that_before_the_tree(session, workspace, member):
    """Order among the refusals: "GitHub could not be asked" comes first, and must
    not be answered with the tree sentence — which would assert that #7 *is* the
    tree's PR on the strength of a lookup that returned nothing."""
    unreachable = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 503: upstream error")
    merge_gh = mock.Mock(side_effect=AssertionError("gh must not be asked to merge"))
    with (
        mock.patch.object(ticket_pull_request, "run_gh", return_value=unreachable),
        mock.patch.object(pull_request_merge, "run_gh", merge_gh),
        pytest.raises(MergeRefused, match="Could not ask GitHub"),
    ):
        merge_and_clean_up(session, member, workspace, number=7, head_sha=SHA)

    merge_gh.assert_not_called()
