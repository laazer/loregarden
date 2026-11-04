"""A ticket that wrote code does not reach done on a landing that merged none of it.

`land_ticket` had two ok exits that merge nothing, no branch and a branch the
target already holds, and both derived done. lg-skills-versioned-328's branch
was a commit of `main` and lg-external-harness-489's was its integration
branch's own refresh merge; both reached done with their stages' edits on no
branch. A ticket whose runs recorded no path (a survey pass) still lands on
those exits, and so does a branch whose own commits an earlier landing, a
squash or a cherry-pick already delivered.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.invariant_registry import LANDINGS_CARRY_THE_WORK
from loregarden.services.invariants import run_invariant
from loregarden.services.land_ticket import LandSkip, land_ticket, landing_would_succeed
from loregarden.services.landing_carried_work import carries_own_work
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session
from tests.worktree_helpers import (
    at_terminal_stage,
    blocking_text,
    commit_on,
    git,
    make_repo,
    make_tree_ticket,
)

WROTE = "server/loregarden/feature.py"


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    """Seeded (via `client`): a re-entered invariant files a bug, which needs a template."""
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="carried", name="carried", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    milestone = make_tree_ticket(session, workspace, "lg-ms-1", kind=WorkItemType.MILESTONE)
    return make_tree_ticket(session, workspace, "lg-impl-2", parent=milestone)


@pytest.fixture(name="target")
def target_fixture(session, workspace, repo, ticket):
    """The ticket's integration branch, as `land_ticket` would create it."""
    return resolve_target_branch(session, ticket, workspace, repo_root=repo)


def _wrote(session, ticket, *paths: str) -> None:
    """A succeeded run of the ticket that recorded writing ``paths``."""
    session.add(
        AgentRun(
            run_code=f"r-{len(paths)}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            status=RunStatus.SUCCEEDED,
            changed_paths_json=json.dumps(list(paths)),
        )
    )
    session.commit()


def _sha(repo, ref):
    return git(repo, "rev-parse", ref).stdout.strip()


def _refresh_from_main(repo, target) -> str:
    """`Refresh <target> from main`: main advanced, merged into the target."""
    commit_on(repo, "main", "elsewhere.txt", "another change on main\n")
    tree = repo.parent / "tmp-refresh"
    git(repo, "worktree", "add", "-q", str(tree), target)
    git(tree, "merge", "-q", "--no-ff", "-m", f"Refresh {target} from main", "main")
    sha = _sha(tree, "HEAD")
    git(repo, "worktree", "remove", "--force", str(tree))
    return sha


# Every shape of "the branch carries nothing", each as it was met on live data.
def _no_branch(repo, ticket, target):
    """lg-run-durability-469's exit, for a ticket that did write something."""


def _cut_from_the_target(repo, ticket, target):
    """Cut, never committed to."""
    git(repo, "branch", ticket.branch, target)


def _a_commit_of_main(repo, ticket, target):
    """lg-skills-versioned-328: the branch was `main` at a squash of another PR."""
    git(repo, "branch", ticket.branch, "main")


def _the_targets_refresh_merge(repo, ticket, target):
    """lg-external-harness-489: the branch was its integration branch's refresh."""
    git(repo, "branch", ticket.branch, _refresh_from_main(repo, target))


def _a_commit_of_main_the_target_holds_by_cherry_pick(repo, ticket, target):
    """The 328 shape reached through the squash exit: the target shares no
    commit with the branch but holds its content."""
    tip = commit_on(repo, "main", "picked.txt", "someone else's change\n")
    commit_on(repo, target, "picked.txt", "someone else's change\n")
    git(repo, "branch", ticket.branch, tip)


CARRIES_NOTHING = [
    _no_branch,
    _cut_from_the_target,
    _a_commit_of_main,
    _the_targets_refresh_merge,
    _a_commit_of_main_the_target_holds_by_cherry_pick,
]


@pytest.mark.parametrize("shape", CARRIES_NOTHING, ids=lambda shape: shape.__name__)
def test_no_ok_exit_of_land_ticket_merges_nothing_a_writing_ticket_recorded(
    session, workspace, repo, ticket, target, shape
):
    shape(repo, ticket, target)
    _wrote(session, ticket, WROTE)
    refs_before = git(repo, "for-each-ref", "--format=%(objectname) %(refname)").stdout

    result = land_ticket(session, ticket, workspace)

    assert not result.ok, result
    assert ticket.branch in result.detail and WROTE in result.detail
    assert git(repo, "for-each-ref", "--format=%(objectname) %(refname)").stdout == refs_before
    session.refresh(ticket)
    assert ticket.landed_sha == "", "a refused landing records nothing"


@pytest.mark.parametrize("shape", CARRIES_NOTHING, ids=lambda shape: shape.__name__)
def test_a_ticket_that_recorded_no_writes_lands_on_the_same_exits(
    session, workspace, repo, ticket, target, shape
):
    """A survey pass writes documents, not files: nothing is owed to the branch."""
    shape(repo, ticket, target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail
    assert result.skipped in (LandSkip.NO_BRANCH, LandSkip.ALREADY_LANDED)


def test_a_branch_landed_earlier_is_still_already_landed(session, workspace, repo, ticket, target):
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "feature.py", "work\n")
    _wrote(session, ticket, WROTE)
    first = land_ticket(session, ticket, workspace)
    assert first.ok and first.skipped is None, first.detail

    again = land_ticket(session, ticket, workspace)

    assert again.ok and again.skipped is LandSkip.ALREADY_LANDED, again.detail


def test_a_branch_whose_work_main_holds_by_squash_is_already_landed(
    session, workspace, repo, ticket, target
):
    """Published as one squash (#621), the integration branch moved onto main:
    the ticket's own commits are on neither, and the target holds their content."""
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "feature.py", "work\n")
    _wrote(session, ticket, WROTE)
    squash = commit_on(repo, "main", "feature.py", "work\n")
    git(repo, "update-ref", f"refs/heads/{target}", squash)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.ALREADY_LANDED, result.detail
    session.refresh(ticket)
    assert ticket.landed_sha == squash


def test_a_relanding_after_main_merged_the_work_is_still_already_landed(
    session, workspace, repo, ticket, target
):
    """blob-core-absorption-134: blobert publishes with merge commits, so the
    ticket's own commits end up on main and the walk finds none of its own."""
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "feature.py", "work\n")
    _wrote(session, ticket, WROTE)
    assert land_ticket(session, ticket, workspace).ok
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge the tree", target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.ALREADY_LANDED, result.detail


def test_a_deleted_branch_whose_landing_is_on_record_is_not_refused(
    session, workspace, repo, ticket, target
):
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "feature.py", "work\n")
    _wrote(session, ticket, WROTE)
    assert land_ticket(session, ticket, workspace).ok
    git(repo, "branch", "-D", ticket.branch)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.NO_BRANCH, result.detail


def test_the_landing_cleared_sweep_does_not_retry_an_empty_landing(
    session, workspace, repo, ticket, target
):
    _cut_from_the_target(repo, ticket, target)
    _wrote(session, ticket, WROTE)

    assert not landing_would_succeed(session, ticket, workspace)


def test_finishing_a_ticket_that_landed_nothing_blocks_its_terminal_stage(
    session, workspace, repo, ticket, target
):
    _cut_from_the_target(repo, ticket, target)
    _wrote(session, ticket, WROTE)
    at_terminal_stage(session, ticket)

    OrchestrationService(session).advance_stage(ticket)
    session.refresh(ticket)

    assert ticket.state == TicketState.BLOCKED, "not done: none of its work landed"
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    said = blocking_text(session, ticket)
    assert ticket.branch in said and WROTE in said


def test_a_local_main_behind_origin_does_not_lend_the_branch_origins_commits(repo):
    """Excluding only `refs/heads/main` would count a commit `origin/main` has
    and the local base lacks as the branch's own."""
    published = commit_on(repo, "main", "published.txt", "on origin\n")
    git(repo, "update-ref", "refs/remotes/origin/main", published)
    git(repo, "update-ref", "refs/heads/main", f"{published}~1")

    assert not carries_own_work(repo, published, "main")


# ---- the invariant over tickets already done -------------------------------------


def _done(session, ticket, *, days_from_since: int) -> None:
    since = LANDINGS_CARRY_THE_WORK.since
    ticket.state = TicketState.DONE
    ticket.resolved_at = utcnow().replace(
        year=since.year, month=since.month, day=since.day
    ) + timedelta(days=days_from_since)
    session.add(ticket)
    session.commit()


def test_a_ticket_done_on_an_empty_landing_after_the_guard_is_a_reentry(
    session, repo, ticket, target
):
    _cut_from_the_target(repo, ticket, target)
    _wrote(session, ticket, WROTE)
    _done(session, ticket, days_from_since=1)

    check = run_invariant(session, LANDINGS_CARRY_THE_WORK)

    assert (check.found, check.reentered, check.repaired) == (1, 1, 0)


def test_the_backlog_from_before_the_guard_is_not_swept(session, repo, ticket, target):
    """lg-external-harness-489 and lg-skills-versioned-328 were checked by hand;
    a report-only sweep would name them on every pass."""
    _cut_from_the_target(repo, ticket, target)
    _wrote(session, ticket, WROTE)
    _done(session, ticket, days_from_since=-1)

    assert run_invariant(session, LANDINGS_CARRY_THE_WORK).found == 0


def test_a_done_ticket_whose_branch_carries_its_work_is_not_a_violation(
    session, workspace, repo, ticket, target
):
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "feature.py", "work\n")
    _wrote(session, ticket, WROTE)
    assert land_ticket(session, ticket, workspace).ok
    _done(session, ticket, days_from_since=1)

    assert run_invariant(session, LANDINGS_CARRY_THE_WORK).found == 0
