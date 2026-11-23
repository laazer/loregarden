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
from pathlib import Path

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
def repo_fixture(tmp_path, request):
    # `indirect` param: extra directory depth, to put the tree's path past the
    # finding's detail limit wherever pytest's tmp dir happens to be.
    parent = tmp_path / getattr(request, "param", "")
    parent.mkdir(parents=True, exist_ok=True)
    repo = make_repo(parent)
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


def test_a_tracked_edit_no_run_recorded_still_does_not_land(session, workspace, ticket, tree):
    """lg-code-knowledge-1055: earlier rounds recorded their paths; the rework round's edits
    were to files none of them named, and dirty ∩ recorded read as nothing uncommitted."""
    run = session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).one()
    run.changed_paths_json = json.dumps(["tests_for_it.py"])
    session.add(run)
    session.commit()
    _edit_without_committing(tree)

    assert uncommitted_at_landing(session, ticket, workspace) == ("service.py",)
    result = land_ticket(session, ticket, workspace)
    assert not result.ok
    assert "service.py" in result.detail


def test_committed_work_lands_as_before(session, workspace, ticket, tree):
    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail


def test_an_untracked_file_nobody_recorded_does_not_block(session, workspace, ticket, tree):
    (tree / "baked_texture.png").write_bytes(b"\x89PNG")

    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert land_ticket(session, ticket, workspace).ok


@pytest.mark.parametrize("importer_committed", [True, False], ids=["committed", "uncommitted"])
def test_a_new_module_the_ticket_imports_does_not_land_without_it(
    session, workspace, ticket, tree, importer_committed
):
    """lg-code-knowledge-1055 added `asserted_claims.py`, recorded by no run, and imported it
    from three edits; landing the edits alone lands an import of a module that is not there."""
    (tree / "pkg").mkdir()
    (tree / "pkg" / "claims.py").write_text("VALUE = 2\n")
    (tree / "service.py").write_text("from pkg.claims import VALUE\n")
    if importer_committed:  # as the stage-end commit leaves it
        git(tree, "commit", "-q", "-am", "the edit, without the module it imports")

    assert "pkg/claims.py" in uncommitted_at_landing(session, ticket, workspace)
    result = land_ticket(session, ticket, workspace)
    assert not result.ok
    assert "pkg/claims.py" in result.detail


@pytest.mark.parametrize(
    ("project", "reference", "counted"),
    [
        ("", 'const Claims = preload("res://scripts/claims.gd")', True),
        ("game", 'const Claims = preload("res://scripts/claims.gd")', True),
        ("", '[ext_resource type="Script" path="res://scripts/claims.gd" id="1"]', True),
        ("game", 'const Claims = preload("res://game/scripts/claims.gd")', False),
        ("", 'const Other = preload("res://other/claims.gd")', False),
        ("", 'var imported := "res://scripts/claims.gd.import"', False),
    ],
    ids=["root", "nested", "scene", "path-from-repo", "elsewhere", "import-sidecar"],
)
def test_a_godot_resource_the_ticket_loads_does_not_land_without_it(
    session, workspace, ticket, tree, project, reference, counted
):
    """A `res://` path spells its extension and is still an import: an untracked script a
    changed script preloads, or a changed scene names, is work the landing would leave out."""
    home = tree / project
    home.mkdir(exist_ok=True)
    (home / "project.godot").write_text("[application]\n")
    (home / "player.gd").write_text(reference + "\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "a script that loads another")
    (home / "scripts").mkdir()
    (home / "scripts" / "claims.gd").write_text("extends Node\n")
    untracked = (Path(project) / "scripts" / "claims.gd").as_posix()

    assert (untracked in uncommitted_at_landing(session, ticket, workspace)) is counted


def test_an_untracked_file_the_ticket_names_with_its_extension_does_not_block(
    session, workspace, ticket, tree
):
    """A path spelled out is code writing or listing a file (a build script's output, a handoff's
    dirty paths), not an import of it: the file stays exempt, as unrecorded output was before."""
    (tree / "build.py").write_text('OUT = "out/report.json"\n')
    git(tree, "add", "build.py")
    git(tree, "commit", "-q", "-m", "a build script the ticket wrote")
    (tree / "out").mkdir()
    (tree / "out" / "report.json").write_text("{}\n")

    assert uncommitted_at_landing(session, ticket, workspace) == ()


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


@pytest.mark.parametrize("repo", ["", "d" * 120 + "/" + "e" * 120], indirect=True)
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
