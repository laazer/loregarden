"""A ticket's work left uncommitted in its tree neither lands silently nor stays unseen.

lg-milestone-that-1237's implement edit never reached a commit, its tests
landed alone, and the ticket derived done. On 2026-10-21 eight landed
loregarden tickets held tracked edits in their kept trees, every one made
before the landing. Landing now refuses such a ticket (the guard), and the
`landed_work_is_committed` invariant reports the trees already in that state.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    MonitorArtifactKind,
    MonitorCondition,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.invariant_registry import LANDED_WORK_IS_COMMITTED
from loregarden.services.invariants import run_invariant
from loregarden.services.land_ticket import land_ticket, landing_would_succeed
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session, col, select
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    """Seeded (via `client`): escalating a finding files a bug, which needs a template."""
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "service.py").write_text("VALUE = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a tracked source file")
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="uncommitted", name="uncommitted", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    milestone = Ticket(
        external_id="lg-ms-1",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(milestone)
    session.commit()
    ticket = Ticket(
        external_id="lg-impl-2",
        workspace_id=workspace.id,
        title="Implement the thing",
        branch="loregarden/lg-impl-2",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="tree")
def tree_fixture(session, workspace, ticket):
    """The ticket's own worktree, with one committed change on its branch."""
    run = AgentRun(
        run_code="r-impl",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    root = resolve_execution_root(session, run, ticket, workspace)
    (root / "tests_for_it.py").write_text("def test_value():\n    assert True\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the tests")
    return root


def _edit_without_committing(root) -> None:
    (root / "service.py").write_text("VALUE = 2  # the implementation\n")


def test_a_ticket_with_uncommitted_work_does_not_land(session, workspace, ticket, tree, repo):
    _edit_without_committing(tree)
    target_before = git(repo, "for-each-ref", "--format=%(objectname)", "refs/heads").stdout

    result = land_ticket(session, ticket, workspace)

    assert not result.ok
    assert "service.py" in result.detail and "uncommitted" in result.detail
    assert git(repo, "for-each-ref", "--format=%(objectname)", "refs/heads").stdout == (
        target_before
    ), "a refused landing moves no ref"
    assert not landing_would_succeed(session, ticket, workspace), (
        "the landing-cleared sweep must not retry a landing that would be refused"
    )


def test_committed_work_lands_as_before(session, workspace, ticket, tree):
    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail


def test_an_untracked_file_nobody_recorded_does_not_block(session, workspace, ticket, tree):
    (tree / "baked_texture.png").write_bytes(b"\x89PNG")

    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert land_ticket(session, ticket, workspace).ok


def test_a_ticket_without_its_own_tree_is_not_judged(session, workspace, ticket, repo):
    (repo / "service.py").write_text("VALUE = 3\n")

    assert uncommitted_at_landing(session, ticket, workspace) == ()


# ---- the invariant over trees already in the state ------------------------------


def _done(session, ticket, *, resolved_days_from_since: int) -> None:
    since = LANDED_WORK_IS_COMMITTED.since
    ticket.state = TicketState.DONE
    ticket.resolved_at = utcnow().replace(
        year=since.year, month=since.month, day=since.day
    ) + timedelta(days=resolved_days_from_since)
    session.add(ticket)
    session.commit()


def _findings(session, condition: MonitorCondition) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                col(Artifact.kind) == MonitorArtifactKind.FINDING.value,
                col(Artifact.title) == f"{condition.value}:{LANDED_WORK_IS_COMMITTED.name}",
            )
        ).all()
    )


def _bugs(session) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(
                col(Ticket.work_item_type) == WorkItemType.BUG,
                col(Ticket.title).contains(LANDED_WORK_IS_COMMITTED.name),
            )
        ).all()
    )


def test_a_tree_left_dirty_before_the_guard_is_reported_for_a_person(session, ticket, tree):
    _edit_without_committing(tree)
    _done(session, ticket, resolved_days_from_since=-4)

    check = run_invariant(session, LANDED_WORK_IS_COMMITTED)

    assert (check.found, check.reentered, check.unrepaired, check.repaired) == (1, 0, 1, 0)
    [finding] = _findings(session, MonitorCondition.INVARIANT_UNREPAIRED)
    payload = json.loads(finding.content_json or "{}")
    assert "service.py" in payload["evidence"]["detail"]
    assert "git checkout --" in payload["summary"], "the finding says what a person does"
    assert len(_bugs(session)) == 1
    assert (tree / "service.py").read_text().startswith("VALUE = 2"), "nothing is discarded"


def test_a_tree_left_dirty_after_the_guard_is_a_reentry(session, ticket, tree):
    _edit_without_committing(tree)
    _done(session, ticket, resolved_days_from_since=1)

    check = run_invariant(session, LANDED_WORK_IS_COMMITTED)

    assert (check.reentered, check.unrepaired) == (1, 0)
    assert _findings(session, MonitorCondition.INVARIANT_REENTERED)


def test_a_clean_done_tree_is_not_reported(session, ticket, tree):
    _done(session, ticket, resolved_days_from_since=-4)

    assert run_invariant(session, LANDED_WORK_IS_COMMITTED).found == 0
