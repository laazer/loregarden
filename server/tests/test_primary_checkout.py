"""Refuse to share a primary checkout that holds someone else's uncommitted work.

lg-workflow-integrity-864, a redo of 738 on the current tree snapshot. Decided
with the operator on 2026-10-03: a ticket's own recorded paths are not foreign
work; hook installation watches only the file it writes; the editor's branch
switch is not guarded.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.executors import cli as cli_module
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    Approval,
    Artifact,
    ArtifactKind,
    BaxterChatSession,
    DirtyCheckoutCause,
    PrimaryCheckoutUse,
    RunStatus,
    Ticket,
    Workspace,
    WorktreeState,
)
from loregarden.services import organization_gate_service
from loregarden.services.chat_worktree import resolve_chat_execution_root
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.primary_checkout import (
    DirtyPrimaryCheckoutError,
    require_clean_checkout,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.workspace_integration import InstallerStatus, InstallState
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket
from tests.worktree_helpers import git, head_branch, make_repo, make_ticket

_USE = PrimaryCheckoutUse.TICKET_FALLBACK


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session: Session, repo: Path) -> Workspace:
    ws = Workspace(slug="primary", name="primary", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _worktree_off(ticket: Ticket, session: Session) -> Ticket:
    ticket.git_automation_json = json.dumps({"worktree": False})
    session.add(ticket)
    session.commit()
    return ticket


def _run(session: Session, workspace: Workspace, ticket: Ticket | None, *, paths=()) -> AgentRun:
    run = AgentRun(
        run_code=f"pc-{len(session.exec(select(AgentRun)).all())}",
        workspace_id=workspace.id,
        ticket_id=ticket.id if ticket else None,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        changed_paths_json=json.dumps(list(paths)),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# --- REQ-1: the guard -------------------------------------------------------


def test_a_clean_checkout_passes(repo: Path):
    require_clean_checkout(repo, use=_USE)


def test_foreign_dirt_is_refused_and_named_sorted(repo: Path):
    (repo / "zeta.txt").write_text("wip\n")
    (repo / "seed.txt").write_text("edited\n")

    with pytest.raises(DirtyPrimaryCheckoutError) as caught:
        require_clean_checkout(repo, use=_USE)

    assert caught.value.cause is DirtyCheckoutCause.DIRTY
    assert caught.value.dirty_paths == ("seed.txt", "zeta.txt")


def test_the_callers_own_paths_are_not_foreign(repo: Path):
    (repo / "seed.txt").write_text("the ticket's own edit\n")

    require_clean_checkout(repo, use=_USE, ignoring={"seed.txt"})


def test_watching_narrows_the_check_to_the_named_files(repo: Path):
    (repo / "unrelated.txt").write_text("operator wip\n")
    require_clean_checkout(repo, use=PrimaryCheckoutUse.HOOK_INSTALL, watching=("lefthook.yml",))

    (repo / "lefthook.yml").write_text("edited by hand\n")
    with pytest.raises(DirtyPrimaryCheckoutError) as caught:
        require_clean_checkout(
            repo, use=PrimaryCheckoutUse.HOOK_INSTALL, watching=("lefthook.yml",)
        )
    assert caught.value.dirty_paths == ("lefthook.yml",)


def test_a_repository_git_cannot_read_is_refused_not_read_as_clean(repo: Path):
    (repo / ".git" / "index").write_bytes(b"not an index")

    with pytest.raises(DirtyPrimaryCheckoutError) as caught:
        require_clean_checkout(repo, use=_USE)
    assert caught.value.cause is DirtyCheckoutCause.UNREADABLE


def test_a_directory_with_no_repository_has_nothing_to_guard(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "anything.txt").write_text("x\n")

    require_clean_checkout(plain, use=_USE)


# --- REQ-2: the fallback resolvers -----------------------------------------


def test_a_ticket_fallback_onto_foreign_dirt_is_refused(session, workspace, repo):
    ticket = _worktree_off(make_ticket(session, workspace), session)
    (repo / "operator.txt").write_text("wip\n")

    with pytest.raises(DirtyPrimaryCheckoutError):
        resolve_execution_root(session, _run(session, workspace, ticket), ticket, workspace)


def test_a_ticket_fallback_over_its_own_earlier_work_is_allowed(session, workspace, repo):
    """With worktrees off, a ticket's earlier stage leaves its files in the
    shared checkout; the next stage must not park on them."""
    ticket = _worktree_off(make_ticket(session, workspace), session)
    _run(session, workspace, ticket, paths=["stage_one.py"])
    (repo / "stage_one.py").write_text("written by stage one\n")

    root = resolve_execution_root(session, _run(session, workspace, ticket), ticket, workspace)

    assert root == repo


def test_a_clean_fallback_is_unchanged(session, workspace, repo):
    ticket = _worktree_off(make_ticket(session, workspace), session)

    assert (
        resolve_execution_root(session, _run(session, workspace, ticket), ticket, workspace) == repo
    )


def test_a_worktree_is_used_whatever_the_primary_holds(session, workspace, repo):
    ticket = make_ticket(session, workspace)
    (repo / "operator.txt").write_text("wip\n")

    root = resolve_execution_root(session, _run(session, workspace, ticket), ticket, workspace)

    assert root != repo
    assert head_branch(root) == ticket.branch


def test_a_chat_fallback_onto_any_dirt_is_refused(session, workspace, repo):
    chat = BaxterChatSession(workspace_id=workspace.id, title="t")
    session.add(chat)
    session.commit()
    (repo / "operator.txt").write_text("wip\n")

    with (
        mock.patch(
            "loregarden.services.chat_worktree.resolve_git_automation",
            return_value=GitAutomationConfig(worktree=False),
        ),
        pytest.raises(DirtyPrimaryCheckoutError),
    ):
        resolve_chat_execution_root(session, _run(session, workspace, None), chat, workspace)


# --- REQ-3: changing the primary -------------------------------------------


def test_switching_a_dirty_checkout_onto_a_ticket_branch_is_refused(session, workspace, repo):
    ticket = make_ticket(session, workspace)
    (repo / "operator.txt").write_text("wip\n")

    with pytest.raises(DirtyPrimaryCheckoutError):
        ensure_ticket_branch(repo, ticket)

    assert head_branch(repo) == "main", "nothing may have been switched"


def test_switching_over_the_tickets_own_work_is_allowed(session, workspace, repo):
    ticket = make_ticket(session, workspace)
    (repo / "mine.py").write_text("x\n")

    ensure_ticket_branch(repo, ticket, ignoring={"mine.py"})

    assert head_branch(repo) == ticket.branch


def test_merging_a_worktree_refuses_a_dirty_primary_and_touches_nothing(session, workspace, repo):
    ticket = make_ticket(session, workspace)
    service = WorktreeService(session, repo_path=str(repo))
    worktree = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket).id)
    (Path(worktree.worktree_path) / "feature.txt").write_text("work\n")
    git(repo, "checkout", "-q", "-b", "elsewhere")
    (repo / "operator.txt").write_text("wip\n")

    with pytest.raises(DirtyPrimaryCheckoutError):
        service.merge_worktree(worktree, target_branch="main")

    session.refresh(worktree)
    assert worktree.state is WorktreeState.ACTIVE, "a refused merge is not a failed one"
    assert head_branch(repo) == "elsewhere"


def test_hook_install_refuses_only_when_its_own_file_is_dirty(workspace, repo):
    (repo / "unrelated.txt").write_text("operator wip\n")
    status = InstallerStatus(mock.ANY, InstallState.CURRENT, "current")
    with mock.patch.object(
        organization_gate_service.integration, "install", return_value=status
    ) as install:
        assert organization_gate_service.hooks_result(workspace, install=True).ok
        assert install.called

        (repo / "lefthook.yml").write_text("hand edit\n")
        install.reset_mock()
        result = organization_gate_service.hooks_result(workspace, install=True)

    assert not result.ok
    assert "lefthook.yml" in result.message
    assert not install.called


# --- REQ-4: a refused dispatch parks, it does not run ----------------------


def test_a_refused_dispatch_parks_files_the_paths_and_starts_no_agent(db_session: Session):
    ticket = _worktree_off(make_workspace_ticket(db_session, "dirty-primary"), db_session)
    workspace = db_session.get(Workspace, ticket.workspace_id)
    repo = Path(workspace.repo_path)
    (repo / "operator.txt").write_text("wip\n")
    run = AgentRun(
        run_code="dirty_primary",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()

    with mock.patch.object(cli_module, "run_print_mode") as spawned:
        completed = CliAgentExecutor(db_session).execute(run, ticket)

    assert not spawned.called, "no agent CLI may start on a refused checkout"
    assert completed.status is RunStatus.CANCELLED
    [error] = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.ERROR)
    ).all()
    assert json.loads(error.content_json)["dirty_paths"] == ["operator.txt"]
    assert db_session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).first()
