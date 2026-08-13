"""Verdicts on the tree a stage is about to run in.

The distinctions that matter are the ones a bool would flatten: a HEAD that moved
forward is the normal state between stages and must not read as a mismatch, and a
commit the receiver has never heard of is a different repository rather than a
diverged one. Getting the second wrong sends someone hunting a force-push that
never happened.
"""

from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    ApprovalStatus,
    BoundaryVerdict,
    GitBoundary,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.git_boundary import read_boundary, stamp_run_boundary
from loregarden.services.handoff_boundary import (
    compare,
    describe,
    park_for_boundary,
    verdict_proceeds,
    verify_run_boundary,
)
from loregarden.services.handoff_store import build_handoff_doc, store_handoff
from loregarden.services.orchestration import OrchestrationService
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo, make_ticket


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
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
    ticket = make_ticket(session, workspace)
    # Parking moves the stage to AWAITING, which only happens for a ticket that
    # is actually on a workflow — as every orchestrator-dispatched ticket is.
    OrchestrationService(session).ensure_workflow_instance(ticket, commit=True)
    session.refresh(ticket)
    return ticket


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _run(session, workspace, ticket, *, stage_key: str = "") -> AgentRun:
    run = AgentRun(
        run_code="r1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        # The ticket's real current stage: parking asks the workflow to move that
        # stage to AWAITING, and a key the template does not contain moves nothing.
        stage_key=stage_key or ticket.workflow_stage_key,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _handoff(session, ticket, boundary: GitBoundary) -> None:
    store_handoff(
        session,
        ticket=ticket,
        doc=build_handoff_doc(
            external_id=ticket.external_id,
            from_agent="test_designer",
            to_agent="backend_implementer",
            checklist=[],
            required_items_met=0,
            total_required_items=0,
            boundary=boundary,
        ),
    )
    session.commit()


# -- verdicts -----------------------------------------------------------------


def test_the_same_tree_at_the_same_commit_matches(repo):
    boundary = read_boundary(repo)

    assert compare(boundary, boundary) == BoundaryVerdict.MATCH


def test_a_commit_added_since_the_handoff_is_advanced_not_diverged(repo):
    """The orchestrator commits between stages, so this is the ordinary case. A
    check that called it a mismatch would fire on every ticket and be switched
    off within a day."""
    sender = read_boundary(repo)
    _commit(repo, "later.txt")
    receiver = read_boundary(repo)

    assert compare(receiver, sender) == BoundaryVerdict.ADVANCED
    assert verdict_proceeds(BoundaryVerdict.ADVANCED)


def test_an_unrelated_history_on_the_same_branch_is_diverged(repo):
    seed = read_boundary(repo).head_sha
    _commit(repo, "theirs.txt")
    sender = read_boundary(repo)
    git(repo, "reset", "-q", "--hard", seed)
    _commit(repo, "ours.txt")
    receiver = read_boundary(repo)

    assert compare(receiver, sender) == BoundaryVerdict.DIVERGED
    assert not verdict_proceeds(BoundaryVerdict.DIVERGED)


def test_a_different_branch_is_branch_changed(repo):
    sender = read_boundary(repo)
    git(repo, "checkout", "-q", "-b", "someone-elses-work")
    receiver = read_boundary(repo)

    assert compare(receiver, sender) == BoundaryVerdict.BRANCH_CHANGED


def test_a_different_checkout_is_repo_changed(repo, tmp_path):
    """The worktree case, in both directions: a stage expecting the ticket's
    worktree and getting the shared root, or the reverse."""
    sender = read_boundary(repo)
    other = make_repo(tmp_path, name="elsewhere")
    receiver = read_boundary(other)

    assert compare(receiver, sender) == BoundaryVerdict.REPO_CHANGED


def test_a_commit_the_receiver_does_not_have_is_repo_changed_not_diverged(repo):
    """`merge-base --is-ancestor` cannot tell "not an ancestor" from "never heard
    of it" — both exit non-zero. Reporting the second as divergence would send
    someone looking for a force-push that never happened."""
    sender = GitBoundary(
        repo_path=str(repo),
        branch="main",
        head_sha="0" * 40,
    )
    receiver = read_boundary(repo)

    assert compare(receiver, sender) == BoundaryVerdict.REPO_CHANGED


def test_an_unrecorded_sender_is_unknown_and_proceeds(repo):
    """A handoff written before boundaries existed. Refusing to run on one would
    strand every ticket already in flight."""
    assert compare(read_boundary(repo), GitBoundary()) == BoundaryVerdict.UNKNOWN
    assert verdict_proceeds(BoundaryVerdict.UNKNOWN)


def test_an_unrecorded_receiver_is_unknown(repo, tmp_path):
    assert compare(GitBoundary(), read_boundary(repo)) == BoundaryVerdict.UNKNOWN


def test_the_description_names_both_trees_and_the_verdict(repo):
    sender = read_boundary(repo)
    git(repo, "checkout", "-q", "-b", "elsewhere")
    receiver = read_boundary(repo)

    text = describe(BoundaryVerdict.BRANCH_CHANGED, receiver=receiver, sender=sender)

    assert "branch_changed" in text
    assert "main" in text and "elsewhere" in text
    # READY is not authorization: the approval must not read as permission to act.
    assert "does not commit, push, merge" in text.lower()


# -- recording and parking ----------------------------------------------------


def test_the_verdict_is_recorded_on_every_dispatch_including_matches(
    session, workspace, ticket, repo
):
    """The column exists for the rate. A table holding only mismatches cannot say
    whether one dispatch in ten hits this or one in ten thousand."""
    run = _run(session, workspace, ticket)
    stamp_run_boundary(session, run, read_boundary(repo))
    _handoff(session, ticket, read_boundary(repo))

    verdict = verify_run_boundary(session, run, ticket)

    assert verdict == BoundaryVerdict.MATCH
    session.refresh(run)
    assert run.start_boundary_verdict == BoundaryVerdict.MATCH


def test_a_ticket_with_no_handoff_yet_is_unknown(session, workspace, ticket, repo):
    """The first stage of a ticket has nothing to compare against."""
    run = _run(session, workspace, ticket)
    stamp_run_boundary(session, run, read_boundary(repo))

    assert verify_run_boundary(session, run, ticket) == BoundaryVerdict.UNKNOWN


def test_a_branch_switched_under_the_ticket_is_caught(session, workspace, ticket, repo):
    """The concurrent-session failure, end to end: a handoff attested against
    `main`, someone moved the checkout, and the next stage is about to run there."""
    _handoff(session, ticket, read_boundary(repo))
    git(repo, "checkout", "-q", "-b", "someone-elses-branch")

    run = _run(session, workspace, ticket)
    stamp_run_boundary(session, run, read_boundary(repo))

    assert verify_run_boundary(session, run, ticket) == BoundaryVerdict.BRANCH_CHANGED


def test_parking_raises_an_approval_and_leaves_the_ticket_unblocked(
    session, workspace, ticket, repo
):
    run = _run(session, workspace, ticket)
    stamp_run_boundary(session, run, read_boundary(repo))
    _handoff(session, ticket, read_boundary(repo))

    approval = park_for_boundary(
        session, run=run, ticket=ticket, verdict=BoundaryVerdict.BRANCH_CHANGED
    )

    assert approval.status == ApprovalStatus.PENDING
    assert "branch_changed" in approval.title
    assert approval.impact
    session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED


def test_parking_moves_the_stage_to_awaiting_so_the_orchestrator_pauses(db_session, git_repo):
    """AWAITING is the signal the orchestrator reads to pause instead of block.

    Needs a ticket on a real workflow template, which is what the seeded session
    provides — `set_stage_status` moves nothing for a stage key no template has.
    """
    ticket = db_session.exec(select(Ticket)).first()
    orch = OrchestrationService(db_session)
    orch.ensure_workflow_instance(ticket)
    instance, stages = orch._resolve_stages(ticket)
    assert instance and stages
    ticket.workflow_stage_key = stages[0].key
    db_session.add(ticket)
    db_session.commit()

    run = AgentRun(
        run_code="r-park",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=stages[0].key,
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    park_for_boundary(db_session, run=run, ticket=ticket, verdict=BoundaryVerdict.DIVERGED)

    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.AWAITING
    assert ticket.state != TicketState.BLOCKED


def test_parking_refunds_the_stage_dispatch_budget(session, workspace, ticket, repo):
    """Parking to ask a question is not an attempt at the work. Charging for it
    would walk a ticket toward its breaker every time a human touched the tree."""
    from loregarden.services.stage_retry_budget import (
        count_stage_dispatches,
        record_stage_dispatch,
    )

    run = _run(session, workspace, ticket)
    stamp_run_boundary(session, run, read_boundary(repo))
    record_stage_dispatch(session, ticket.id, run.stage_key)
    assert count_stage_dispatches(session, ticket.id, run.stage_key) == 1

    park_for_boundary(session, run=run, ticket=ticket, verdict=BoundaryVerdict.DIVERGED)

    assert count_stage_dispatches(session, ticket.id, run.stage_key) == 0


def test_enforcement_is_off_by_default(session, workspace):
    """Shipped record-only. Turning a brand-new precondition into a hard stop on
    day one converts a diagnostic into an outage."""
    from loregarden.services.handoff_boundary import boundary_enforced

    assert boundary_enforced(workspace) is False
