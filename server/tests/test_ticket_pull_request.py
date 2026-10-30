"""The PR tab asks GitHub for the branch's PR, and keeps "none" apart from "failed".

It used to read only the `pr` artifact that its own "Open PR" button writes, so
a PR opened any other way showed as "No pull request opened".
"""

import json
import logging
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    PullRequestCheckOutcome,
    PullRequestLookup,
    PullRequestReview,
    PullRequestState,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.publish_ref import publish_ref_for
from loregarden.services.ticket_pull_request import ticket_pull_request
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo

BRANCH = "loregarden/lg-1-add-the-thing"
#: The integration branch of the tree rooted at the `ticket` fixture.
INTEGRATION = "integration/LG-1"

GH_PR = {
    "number": 555,
    "url": "https://github.com/o/r/pull/555",
    "title": "feat: the thing",
    "state": "OPEN",
    "isDraft": False,
    "baseRefName": "main",
    "headRefName": BRANCH,
    "additions": 10,
    "deletions": 2,
    "changedFiles": 3,
    "reviewDecision": "",
    "mergeable": "MERGEABLE",
    "body": "Body",
    "statusCheckRollup": [
        {
            "__typename": "CheckRun",
            "name": "Server",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "https://ci/1",
        },
        {
            "__typename": "CheckRun",
            "name": "Client",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "detailsUrl": "https://ci/2",
        },
        {
            "__typename": "CheckRun",
            "name": "Lint",
            "status": "IN_PROGRESS",
            "conclusion": "",
            "detailsUrl": "",
        },
        {
            "__typename": "CheckRun",
            "name": "Docs",
            "status": "COMPLETED",
            "conclusion": "SKIPPED",
            "detailsUrl": "",
        },
        {
            "__typename": "StatusContext",
            "context": "legacy/ci",
            "state": "PENDING",
            "targetUrl": "https://ci/3",
        },
    ],
}


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A real repository, because the candidate list is gated on whether the
    tree's branch exists (`rev`). A `tmp_path` with a bare `.git` directory
    answers "" to every ref, which would make the one gate under test invisible.
    """
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(
        external_id="LG-1", workspace_id=workspace.id, title="Add the thing", branch=BRANCH
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="child")
def child_fixture(session, workspace, ticket):
    """A ticket inside `ticket`'s tree: it never ships its own branch."""
    child = Ticket(
        external_id="LG-2", workspace_id=workspace.id, title="Child", parent_ticket_id=ticket.id
    )
    session.add(child)
    session.commit()
    session.refresh(child)
    return child


@pytest.fixture(name="tree_branch")
def tree_branch_fixture(repo, ticket):
    """The tree's integration branch, as `ensure_integration_branch` leaves it."""
    git(repo, "branch", INTEGRATION)
    return INTEGRATION


def _gh(returncode: int, stdout: str = "", stderr: str = ""):
    proc = subprocess.CompletedProcess(["gh"], returncode, stdout=stdout, stderr=stderr)
    return mock.patch("loregarden.services.ticket_pull_request.run_gh", return_value=proc)


def _gh_by_head(prs: dict[str, dict]):
    """``gh pr view <head>`` answering the PR at ``head``, and "none" anywhere else."""

    def view(args, **_kwargs):
        head = args[2]
        if head in prs:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(prs[head]), stderr="")
        none = f'no pull requests found for branch "{head}"'
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=none)

    return mock.patch("loregarden.services.ticket_pull_request.run_gh", side_effect=view)


def _candidates_asked(session, ticket, workspace):
    """Every branch `gh pr view` is asked about, in order, when none has a PR."""
    with _gh_by_head({}) as run_gh:
        result = ticket_pull_request(session, ticket, workspace)
    return [call.args[0][2] for call in run_gh.call_args_list], result


def test_a_pr_opened_outside_loregarden_is_found(session, workspace, ticket):
    with _gh_by_head({BRANCH: GH_PR}) as run_gh:
        result = ticket_pull_request(session, ticket, workspace)

    assert [call.args[0][2] for call in run_gh.call_args_list] == [publish_ref_for(BRANCH), BRANCH]
    assert result.lookup is PullRequestLookup.FOUND
    pr = result.pull_request
    assert pr is not None
    assert (pr.number, pr.state, pr.review, pr.has_conflicts) == (
        555,
        PullRequestState.OPEN,
        PullRequestReview.NOT_REQUIRED,
        False,
    )
    assert [(c.name, c.outcome) for c in pr.checks] == [
        ("Server", PullRequestCheckOutcome.FAILING),
        ("Client", PullRequestCheckOutcome.PASSING),
        ("Lint", PullRequestCheckOutcome.PENDING),
        ("Docs", PullRequestCheckOutcome.SKIPPED),
        ("legacy/ci", PullRequestCheckOutcome.PENDING),
    ]
    assert pr.checks[4].url == "https://ci/3"


def test_the_publish_pr_is_found_before_one_on_the_branch_itself(session, workspace, ticket):
    """`publish_tree` opens a root's PR on ``publish/<branch>``; a PR on the bare
    name is older (opened by hand, or before publishing moved) and is not shown."""
    published = {**GH_PR, "number": 777, "headRefName": publish_ref_for(BRANCH)}
    with _gh_by_head({publish_ref_for(BRANCH): published, BRANCH: GH_PR}):
        pr = ticket_pull_request(session, ticket, workspace).pull_request

    assert pr is not None and pr.number == 777


def test_merged_and_conflicting_are_read_from_github(session, workspace, ticket):
    payload = {**GH_PR, "state": "MERGED", "mergeable": "CONFLICTING", "reviewDecision": "APPROVED"}
    with _gh(0, json.dumps(payload)):
        pr = ticket_pull_request(session, ticket, workspace).pull_request

    assert pr is not None
    assert (pr.state, pr.has_conflicts, pr.review) == (
        PullRequestState.MERGED,
        True,
        PullRequestReview.APPROVED,
    )


def test_no_pr_is_none_not_a_failure(session, workspace, ticket):
    with _gh(1, stderr=f'no pull requests found for branch "{BRANCH}"'):
        result = ticket_pull_request(session, ticket, workspace)

    assert (result.lookup, result.pull_request, result.error) == (PullRequestLookup.NONE, None, "")


def test_a_failed_lookup_says_why_and_offers_the_recorded_pr(session, workspace, ticket):
    recorded = {
        "url": "https://github.com/o/r/pull/9",
        "number": "9",
        "title": "t",
        "branch": BRANCH,
        "body": "",
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.PR,
            title="PR #9",
            content_json=json.dumps(recorded),
        )
    )
    session.commit()

    with _gh(1, stderr="HTTP 401: Bad credentials"):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert "Bad credentials" in result.error
    assert result.recorded is not None
    assert result.recorded.url == recorded["url"]


@pytest.mark.parametrize(
    "side_effect",
    [subprocess.TimeoutExpired(["gh"], 15), FileNotFoundError("gh")],
)
def test_gh_that_cannot_run_is_a_failure(session, workspace, ticket, side_effect):
    with mock.patch("loregarden.services.ticket_pull_request.run_gh", side_effect=side_effect):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert result.error


def test_unparseable_gh_output_is_a_failure(session, workspace, ticket):
    with _gh(0, "not json"):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FAILED


# --- the root that keys the branch, carried beside it (lg-verdict-outcome-1655) -------
#
# `ships_with` named the tree's integration branch and nothing named the ticket
# that branch belongs to, so the PR tab's provenance line ended in a branch name
# and the operator had to invert `integration_branch_for` by hand. The root is
# named here, from the same walk, because that string is not invertible: a root
# with no `external_id` is keyed on `id[:8]`, which no route resolves.


@pytest.fixture(name="anonymous_root")
def anonymous_root_fixture(session, workspace):
    """A tree whose root has no shareable id, and one member of it."""
    root = Ticket(external_id="", workspace_id=workspace.id, title="Unnamed root")
    session.add(root)
    session.commit()
    session.refresh(root)
    member = Ticket(
        external_id="LG-5", workspace_id=workspace.id, title="Member", parent_ticket_id=root.id
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return root, member


def test_a_tree_member_carries_the_root_that_keys_its_branch(session, workspace, ticket, child):
    """AC2/AC4: the root's shareable id for a member, "" for a ticket that ships
    its own branch — whether it roots a tree or has no tree at all.

    "LG-1" is deliberately not "integration/LG-1": a source that put the branch
    in this field, or stripped the prefix off it, passes nothing here.
    """
    solo = Ticket(external_id="LG-9", workspace_id=workspace.id, title="Solo")
    session.add(solo)
    session.commit()
    session.refresh(solo)

    with _gh_by_head({}):
        member = ticket_pull_request(session, child, workspace)
        root = ticket_pull_request(session, ticket, workspace)
        own = ticket_pull_request(session, solo, workspace)

    assert (member.ships_with, member.ships_with_root) == (INTEGRATION, "LG-1")
    assert (root.ships_with, root.ships_with_root) == ("", "")
    assert (own.ships_with, own.ships_with_root) == ("", "")


def test_a_grandchild_carries_the_roots_id_not_its_parents(session, workspace, grandchild, child):
    """A tree is rooted once: depth must not re-key the field on the nearest
    ancestor, which is the mistake `ships_with` itself was filed for."""
    with _gh_by_head({}):
        result = ticket_pull_request(session, grandchild, workspace)

    assert result.ships_with_root == "LG-1"
    assert result.ships_with_root != child.external_id


def test_a_root_with_no_shareable_id_is_named_by_its_full_uuid(session, workspace, anonymous_root):
    """AC2: `root.id` in FULL, never `root.id[:8]`.

    The 8-character prefix is `integration_branch_for`'s branch slug — it is
    right there in `ships_with` — and no ticket route resolves it, so emitting
    it would be the dead link the field exists to avoid.
    """
    root, member = anonymous_root

    with _gh_by_head({}):
        result = ticket_pull_request(session, member, workspace)

    assert result.ships_with == f"integration/{root.id[:8]}"
    assert result.ships_with_root == root.id
    assert result.ships_with_root != root.id[:8]
    assert len(result.ships_with_root) == len(root.id) > 8


@pytest.mark.parametrize(
    ("external_id", "expected_branch_slug", "expects_the_uuid"),
    [
        (" LG-7 ", "LG-7", False),
        ("   ", None, True),
        ("\t\n", None, True),
    ],
)
def test_the_root_ref_is_stripped_the_way_the_branch_slug_is(
    session, workspace, external_id, expected_branch_slug, expects_the_uuid
):
    """AC2: `root.external_id.strip()`, and `root.id` when that leaves nothing.

    `integration_branch_for` strips before it slugs (`target_branch.py:135`), so
    a root whose id carries whitespace already has a clean branch name. A root
    ref that skipped the strip would disagree with it: " LG-7 " goes into the
    link's path, where the leading space is encoded and the route resolves no
    ticket, and a whitespace-only id reads as a shareable id the field would
    emit instead of falling through to the uuid. Neither case can be told from a
    good one by looking at the payload, which is why they are pinned here rather
    than left to the one measured zero (0 of 2086 live tickets carry a blank id).
    """
    root = Ticket(external_id=external_id, workspace_id=workspace.id, title="Whitespace root")
    session.add(root)
    session.commit()
    session.refresh(root)
    member = Ticket(
        external_id="LG-8", workspace_id=workspace.id, title="Member", parent_ticket_id=root.id
    )
    session.add(member)
    session.commit()
    session.refresh(member)

    with _gh_by_head({}):
        result = ticket_pull_request(session, member, workspace)

    slug = root.id[:8] if expects_the_uuid else expected_branch_slug
    assert result.ships_with == f"integration/{slug}"
    assert result.ships_with_root == (root.id if expects_the_uuid else expected_branch_slug)
    assert result.ships_with_root == result.ships_with_root.strip()
    assert result.ships_with_root != external_id


def test_a_milestone_under_an_initiative_names_no_root_on_the_payload(session, workspace):
    """AC2: "" for a ticket that ships its own branch covers the shape where
    that ticket still has a parent.

    `subtree_root` stops at an initiative, so this milestone is its own landing
    root while carrying a `parent_ticket_id`. A seam deciding that on the parent
    id would hand the panel `ships_with="main"` with a root ref pointing at the
    ticket being looked at: a provenance line claiming this work ships in a tree
    pull request on the base branch, linking to itself.
    """
    initiative = Ticket(
        external_id="LG-INIT-2", title="An initiative", work_item_type=WorkItemType.INITIATIVE
    )
    session.add(initiative)
    session.commit()
    session.refresh(initiative)
    milestone = Ticket(
        external_id="LG-6",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
        parent_ticket_id=initiative.id,
    )
    session.add(milestone)
    session.commit()
    session.refresh(milestone)

    with _gh_by_head({}):
        result = ticket_pull_request(session, milestone, workspace)

    assert milestone.parent_ticket_id == initiative.id
    assert (result.ships_with, result.ships_with_root) == ("", "")
    assert result.is_tree_pull_request is False


def test_a_landing_target_failure_degrades_both_fields_together(
    session, workspace, under_initiative, caplog
):
    """AC2: the `TargetBranchError` path empties BOTH fields, and says so.

    Never one without the other — a root ref beside an empty branch would put a
    link on a line that names nothing — and never silently: today's `warning`
    with `exc_info=True` is what keeps the swallow observable.
    """
    with caplog.at_level(logging.WARNING, logger="loregarden.services.ticket_pull_request"):
        with _gh_by_head({}):
            result = ticket_pull_request(session, under_initiative, workspace)

    assert (result.ships_with, result.ships_with_root) == ("", "")
    assert result.is_tree_pull_request is False

    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warned, "a swallowed TargetBranchError must still be reported"
    assert any(under_initiative.external_id in r.getMessage() for r in warned)
    assert any(r.exc_info for r in warned), "logged without exc_info=True"


def test_the_root_ref_does_not_move_the_tree_pull_request_gate(
    session, workspace, child, tree_branch
):
    """AC3: `is_tree_pull_request` still reads `ships_with`, the PR's head and
    the ticket's own branch — never the new field. Both payloads here carry the
    same non-empty root ref; only the head differs, and only the head decides.
    """
    trees = {**GH_PR, "number": 733, "headRefName": publish_ref_for(INTEGRATION)}
    with _gh_by_head({publish_ref_for(INTEGRATION): trees}):
        as_trees = ticket_pull_request(session, child, workspace)

    own = resolve_ticket_branch(child)
    with _gh_by_head({publish_ref_for(own): {**GH_PR, "headRefName": publish_ref_for(own)}}):
        as_own = ticket_pull_request(session, child, workspace)

    assert as_trees.ships_with_root == "LG-1"
    assert as_trees.is_tree_pull_request is True
    assert as_own.ships_with_root == "LG-1"
    assert as_own.is_tree_pull_request is False


def test_a_tree_member_says_which_integration_branch_it_ships_with(session, workspace, ticket):
    child = Ticket(
        external_id="LG-2", workspace_id=workspace.id, title="Child", parent_ticket_id=ticket.id
    )
    session.add(child)
    session.commit()

    with _gh(1, stderr="no pull requests found for branch"):
        assert ticket_pull_request(session, child, workspace).ships_with == "integration/LG-1"
        assert ticket_pull_request(session, ticket, workspace).ships_with == ""


# --- a ticket inside a tree reads its tree's branch -----------------------------------
#
# A tree member never ships its own branch: `publish_tree` publishes the root's
# integration branch and the PR lives there, so asking only about the member's
# own branch reported "no pull request" over an open, failing one. The answer is
# already on the payload as `ships_with`; the lookup reads it.


def test_a_tree_member_resolves_its_trees_pull_request(
    session, workspace, ticket, child, tree_branch
):
    """AC1(c)(i) and AC2(b): the tree's PR is the answer, labelled as the tree's.

    Today this same fixture answers `PullRequestLookup.NONE`.
    """
    published = {**GH_PR, "number": 733, "headRefName": publish_ref_for(INTEGRATION)}
    with _gh_by_head({publish_ref_for(INTEGRATION): published}) as run_gh:
        result = ticket_pull_request(session, child, workspace)

    # The publish ref is asked first, and answers, so nothing else is asked.
    assert [call.args[0][2] for call in run_gh.call_args_list] == [publish_ref_for(INTEGRATION)]
    assert result.lookup is PullRequestLookup.FOUND
    assert result.pull_request is not None and result.pull_request.number == 733
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is True


def test_a_tree_member_with_no_pull_request_anywhere_names_its_trees_branch(
    session, workspace, ticket, child, tree_branch
):
    """AC1(b)/(c)(ii): NONE, and `branch` is the tree's branch rather than the
    last candidate asked — that is what keeps the panel's "ships in its tree's
    pull request" copy naming a real branch."""
    asked, result = _candidates_asked(session, child, workspace)
    own = resolve_ticket_branch(child)

    assert asked == [publish_ref_for(INTEGRATION), INTEGRATION, publish_ref_for(own), own]
    assert result.lookup is PullRequestLookup.NONE
    assert result.branch == INTEGRATION
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is False


def test_a_member_that_hand_opened_a_pr_on_its_own_branch_owns_it(
    session, workspace, ticket, child, tree_branch
):
    """The `publish_ref`-first order does not relabel a PR whose head really is
    the member's own branch (one opened before `_refuse_tree_member` landed)."""
    own = resolve_ticket_branch(child)
    with _gh_by_head({own: {**GH_PR, "number": 41, "headRefName": own}}):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FOUND
    assert result.pull_request is not None and result.pull_request.number == 41
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is False


def test_a_subtree_roots_own_tree_pull_request_is_not_labelled_the_trees(
    session, workspace, ticket, child, tree_branch
):
    """AC2(b): the `bool(ships_with)` gate is load-bearing. A root's own tree PR
    has head `publish/integration/<root>`, which is not the root's own branch, so
    head-comparison alone would strip the merge button from every root's tab."""
    published = {**GH_PR, "number": 733, "headRefName": publish_ref_for(INTEGRATION)}
    with _gh_by_head({publish_ref_for(INTEGRATION): published}):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FOUND
    assert result.ships_with == ""
    assert result.is_tree_pull_request is False


# --- the candidate list for every ticket that is not a tree member --------------------


def test_a_subtree_root_asks_its_integration_branch_then_its_own(
    session, workspace, ticket, child, tree_branch
):
    """AC1(c)(iii), unchanged: a root whose integration branch exists."""
    asked, _ = _candidates_asked(session, ticket, workspace)

    assert asked == [publish_ref_for(INTEGRATION), INTEGRATION, publish_ref_for(BRANCH), BRANCH]


@pytest.mark.parametrize("with_a_tree", [False, True])
def test_a_ticket_with_no_integration_branch_asks_only_its_own(
    session, workspace, ticket, with_a_tree, request
):
    """AC1(c)(iii), unchanged: a top-level ticket (no tree) and a subtree root
    whose integration branch does not exist take the same path and ask the same
    two names. The `rev` gate is what keeps the missing branch off the list."""
    if with_a_tree:
        request.getfixturevalue("child")

    asked, result = _candidates_asked(session, ticket, workspace)

    assert asked == [publish_ref_for(BRANCH), BRANCH]
    assert result.ships_with == ""


def test_an_initiative_now_asks_its_publish_ref_as_well(session, workspace):
    """AC1(c)(iii), an ACCEPTED divergence: today an initiative gets `[own]`,
    because `subtree_root` raises before the publish form is added. It now costs
    one extra `gh pr view`. That is cheaper than a third derivation of
    "is this a root", which is the defect this ticket removes."""
    # An initiative is cross-workspace: `ck_tickets_workspace_binding` refuses
    # one bound to a workspace.
    initiative = Ticket(
        external_id="LG-INIT",
        title="An initiative",
        work_item_type=WorkItemType.INITIATIVE,
    )
    session.add(initiative)
    session.commit()
    session.refresh(initiative)

    asked, result = _candidates_asked(session, initiative, workspace)
    own = resolve_ticket_branch(initiative)

    assert asked == [publish_ref_for(own), own]
    assert result.ships_with == ""
    assert result.branch == own


@pytest.fixture(name="under_initiative")
def under_initiative_fixture(session, workspace):
    """A task sitting directly under an initiative, which the hierarchy does not
    allow: `subtree_root` raises for it, so it has no tree."""
    # An initiative is cross-workspace: `ck_tickets_workspace_binding` refuses
    # one bound to a workspace.
    initiative = Ticket(
        external_id="LG-INIT",
        title="An initiative",
        work_item_type=WorkItemType.INITIATIVE,
    )
    session.add(initiative)
    session.commit()
    session.refresh(initiative)
    stray = Ticket(
        external_id="LG-3",
        workspace_id=workspace.id,
        title="Stray",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=initiative.id,
    )
    session.add(stray)
    session.commit()
    session.refresh(stray)
    return stray


def test_a_ticket_directly_under_an_initiative_asks_its_publish_ref_as_well(
    session, workspace, under_initiative
):
    """AC1(c)(iii), the second ACCEPTED divergence. `_ships_with` swallows the
    `TargetBranchError` to "", but the fallback `integration_branch_for(ticket)`
    then *succeeds*, naming `integration/LG-3`. No such ref exists, so the `rev`
    gate bounds the list at the ticket's own two names — today's `[own]` plus
    its publish form."""
    asked, result = _candidates_asked(session, under_initiative, workspace)
    own = resolve_ticket_branch(under_initiative)

    assert asked == [publish_ref_for(own), own]
    assert result.ships_with == ""


def test_the_ref_gate_is_what_bounds_a_ticket_under_an_initiative(
    session, workspace, repo, under_initiative
):
    """The same ticket, with `integration/LG-3` actually present: the fallback's
    branch is then asked. This is the `rev` gate being the only thing that
    decides, which is why these tests use a real repository."""
    git(repo, "branch", "integration/LG-3")

    asked, _ = _candidates_asked(session, under_initiative, workspace)
    own = resolve_ticket_branch(under_initiative)

    assert asked == [
        publish_ref_for("integration/LG-3"),
        "integration/LG-3",
        publish_ref_for(own),
        own,
    ]


# --- "there is no PR" and "GitHub could not be asked" stay two answers ----------------


def test_a_gh_failure_on_the_first_candidate_is_not_collapsed_into_none(
    session, workspace, ticket, child, tree_branch
):
    """AC5: more candidates must not turn a FAILED into a NONE. `_lookup` returns
    on the first non-NONE answer, so the failure short-circuits with its stderr
    and the later candidates are never asked."""
    proc = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="HTTP 503: upstream error")
    with mock.patch("loregarden.services.ticket_pull_request.run_gh", return_value=proc) as run_gh:
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert "upstream error" in result.error
    assert result.branch == publish_ref_for(INTEGRATION)
    assert run_gh.call_count == 1
    assert run_gh.call_args_list[0].args[0][2] == publish_ref_for(INTEGRATION)


# --- breaking the member path: depth, which ref exists, and which head it is ----------


@pytest.fixture(name="grandchild")
def grandchild_fixture(session, workspace, child):
    """Depth 3. 543 of the live members sit this deep or deeper and `subtree_root`
    walks every one of them to the top, so a grandchild's tree is the *root's*,
    never its parent's."""
    grandchild = Ticket(
        external_id="LG-4",
        workspace_id=workspace.id,
        title="Grandchild",
        parent_ticket_id=child.id,
    )
    session.add(grandchild)
    session.commit()
    session.refresh(grandchild)
    return grandchild


def test_a_grandchild_resolves_the_roots_tree_pull_request_not_its_parents(
    session, workspace, repo, grandchild, tree_branch
):
    """A tree is rooted once. `integration/LG-2` is made to exist here, and given
    a PR of its own, so that a lookup deriving the tree from the *parent* rather
    than from `ships_with` has a wrong answer available to find."""
    git(repo, "branch", "integration/LG-2")
    parents = publish_ref_for("integration/LG-2")
    with _gh_by_head(
        {
            publish_ref_for(INTEGRATION): {**GH_PR, "number": 733},
            parents: {**GH_PR, "number": 999},
        }
    ) as run_gh:
        result = ticket_pull_request(session, grandchild, workspace)

    asked = [call.args[0][2] for call in run_gh.call_args_list]
    assert "integration/LG-2" not in str(asked)
    assert asked == [publish_ref_for(INTEGRATION)]
    assert result.pull_request is not None and result.pull_request.number == 733
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is True


def test_a_member_whose_tree_branch_is_not_here_still_names_it(session, workspace, child):
    """The `rev` gate drops the tree's branch from the candidates (it exists only
    on the remote, or not yet at all), but the NONE result's `branch` is still
    `ships_with` — AC1(b) — which is what keeps the panel's "ships in its tree's
    pull request" copy naming a real branch rather than a branch nobody has."""
    asked, result = _candidates_asked(session, child, workspace)
    own = resolve_ticket_branch(child)

    assert asked == [publish_ref_for(own), own]
    assert (result.lookup, result.branch) == (PullRequestLookup.NONE, INTEGRATION)
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is False


def test_a_members_pr_on_the_bare_integration_branch_is_still_its_trees(
    session, workspace, child, tree_branch
):
    """A tree PR opened before publishing moved to its own name: the bare branch
    is asked after the publish form, and what it answers is still the tree's."""
    stale = {**GH_PR, "number": 712, "headRefName": INTEGRATION}
    with _gh_by_head({INTEGRATION: stale}) as run_gh:
        result = ticket_pull_request(session, child, workspace)

    assert [call.args[0][2] for call in run_gh.call_args_list] == [
        publish_ref_for(INTEGRATION),
        INTEGRATION,
    ]
    assert result.pull_request is not None and result.pull_request.number == 712
    assert result.is_tree_pull_request is True


def test_a_member_whose_own_branch_was_published_owns_that_pr(
    session, workspace, child, tree_branch
):
    """The label is `branch_for_pr_head(head) != own`, not `head != own`: a PR
    published from the member's *own* branch has head `publish/<own>`, which is
    that branch, and relabelling it the tree's would refuse a merge that is the
    member's to make."""
    own = resolve_ticket_branch(child)
    published_own = {**GH_PR, "number": 88, "headRefName": publish_ref_for(own)}
    with _gh_by_head({publish_ref_for(own): published_own}):
        result = ticket_pull_request(session, child, workspace)

    assert result.pull_request is not None and result.pull_request.number == 88
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is False


def test_a_failed_lookup_for_a_member_is_not_labelled_its_trees_pull_request(
    session, workspace, child, tree_branch
):
    """ "GitHub could not be asked" is not "the tree holds it": with no PR in hand
    there is nothing whose head could be compared, and the label must stay False
    rather than read a head off `None`."""
    with _gh(1, stderr="HTTP 401: Bad credentials"):
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert result.pull_request is None
    assert "Bad credentials" in result.error
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is False


def test_a_member_in_a_directory_that_is_not_a_repository_fails_before_asking_github(
    session, workspace, child, tmp_path
):
    """Every member now asks `rev` where only a root used to, so the 95% case runs
    through the ref gate on whatever the workspace's `repo_path` points at. A path
    with no `.git` answers "no such ref" and the lookup reports that honestly,
    without raising and without spending a `gh` call."""
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    broken = Workspace(slug="broken", name="broken", repo_path=str(elsewhere))
    session.add(broken)
    session.commit()

    with mock.patch(
        "loregarden.services.ticket_pull_request.run_gh",
        side_effect=AssertionError("gh must not be asked about a non-repository"),
    ):
        result = ticket_pull_request(session, child, broken)

    assert result.lookup is PullRequestLookup.FAILED
    assert "is not a git repository" in result.error
    assert result.ships_with == INTEGRATION
    assert result.is_tree_pull_request is False


def test_a_gh_timeout_on_the_first_candidate_does_not_cost_four(
    session, workspace, child, tree_branch
):
    """AC10(a): four candidates do not make the worst case 4 x GH_TIMEOUT_SECONDS.
    A timeout is a FAILED answer, and `_lookup` returns on the first non-NONE one,
    so a hang short-circuits instead of accumulating."""
    with mock.patch(
        "loregarden.services.ticket_pull_request.run_gh",
        side_effect=subprocess.TimeoutExpired(["gh"], 15),
    ) as run_gh:
        result = ticket_pull_request(session, child, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert run_gh.call_count == 1
    assert run_gh.call_args_list[0].args[0][2] == publish_ref_for(INTEGRATION)
