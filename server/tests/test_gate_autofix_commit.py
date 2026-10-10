"""Committing a transition gate's mechanical fixes, and reporting what it could not.

lg-workflow-integrity-850. `GateRecovery._commit_autofix` read a commit git
refused as "nothing to commit": no log, no artifact, and the gate passed with the
fixer's edits uncommitted. And it only ever staged paths a run had recorded, so a
fixer edit anywhere else was neither committed nor mentioned.

Each test takes the footprint, makes the fixer's edits by hand, then commits —
the same order `GateRecovery` follows around `run_gate_autofix`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.autofix_commit import (
    commit_fixer_changes,
    take_fixer_footprint,
)
from loregarden.services.gate_recovery import GateRecovery
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo

_STAGE = "implement"


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """`seed.txt` committed, `work.py` committed and then recorded as the ticket's."""
    repo = make_repo(tmp_path)
    (repo / "work.py").write_text("x = 1\n")
    git(repo, "add", "work.py")
    git(repo, "commit", "-qm", "work")
    return repo


@pytest.fixture(name="ticket_session")
def ticket_session_fixture(isolated_db, repo: Path):
    session = Session(isolated_db)
    workspace = Workspace(slug="autofix", name="autofix", repo_path=str(repo))
    session.add(workspace)
    session.commit()
    ticket = Ticket(
        external_id="autofix-1",
        workspace_id=workspace.id,
        title="autofix",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    session.add(
        AgentRun(
            run_code="autofix_run",
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            agent_id="backend_implementer",
            stage_key=_STAGE,
            changed_paths_json='["work.py"]',
        )
    )
    session.commit()
    yield session, ticket
    session.close()


@pytest.fixture(name="refusing_hook")
def refusing_hook_fixture(repo: Path) -> Path:
    """A pre-commit hook that refuses every commit, as a workspace gate refuses fixer output."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refused by the workspace gate' >&2\nexit 1\n")
    hook.chmod(0o755)
    return hook


def _recovery(session: Session) -> GateRecovery:
    return GateRecovery(
        session, OrchestrationCallbackService(session), OrchestrationService(session)
    )


def _artifacts(session: Session, ticket: Ticket, kind: ArtifactKind) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == kind)
        ).all()
    )


def _committed_paths(repo: Path) -> set[str]:
    out = git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    return {line for line in out.splitlines() if line}


def _head_subject(repo: Path) -> str:
    return git(repo, "log", "-1", "--format=%s").stdout.strip()


# --- AC1: a refused commit is reported -------------------------------------


def test_a_failed_autofix_commit_is_reported(
    ticket_session, repo: Path, refusing_hook: Path, caplog: pytest.LogCaptureFixture
):
    """Moved from test_open_defect_repros.py, where it was the 850 repro."""
    session, ticket = ticket_session
    before = take_fixer_footprint(repo)
    (repo / "work.py").write_text("x = 2\n")  # the fixer's edit

    with caplog.at_level(logging.WARNING, logger="loregarden.services.gate_recovery"):
        _recovery(session)._commit_autofix(
            ticket, _STAGE, "ruff --fix: 1 fixed", repo_root=repo, before=before
        )

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a refused commit must be logged at warning or above"
    assert "refused by the workspace gate" in warnings[-1].getMessage()
    errors = _artifacts(session, ticket, ArtifactKind.ERROR)
    assert len(errors) == 1
    assert "refused by the workspace gate" in json.loads(errors[0].content_json)["message"]
    assert _head_subject(repo) == "work", "nothing may have been committed"


def test_a_refused_commit_is_not_reported_as_an_autofix(
    ticket_session, repo: Path, refusing_hook: Path
):
    session, ticket = ticket_session
    before = take_fixer_footprint(repo)
    (repo / "work.py").write_text("x = 2\n")

    _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    titles = [a.title for a in _artifacts(session, ticket, ArtifactKind.CONTEXT)]
    assert not any(t.startswith("Auto-fixed") for t in titles)


# --- AC2: an unrecorded path is committed or reported ----------------------


def test_a_fixer_edit_to_an_unrecorded_clean_file_is_committed(ticket_session, repo: Path):
    session, ticket = ticket_session
    before = take_fixer_footprint(repo)
    (repo / "seed.txt").write_text("formatted by a fixer\n")  # tracked, clean, never recorded

    _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    assert _committed_paths(repo) == {"seed.txt"}
    assert git(repo, "status", "--porcelain").stdout == ""


def test_a_file_a_fixer_creates_is_committed(ticket_session, repo: Path):
    session, ticket = ticket_session
    before = take_fixer_footprint(repo)
    (repo / "generated_stub.pyi").write_text("x: int\n")

    _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    assert _committed_paths(repo) == {"generated_stub.pyi"}


def test_a_fixer_edit_to_someone_elses_dirty_file_is_left_and_reported(
    ticket_session, repo: Path, caplog: pytest.LogCaptureFixture
):
    """The operator's work-in-progress with a fix mixed in cannot be committed as
    the ticket's — it is left alone, and the report names it."""
    session, ticket = ticket_session
    (repo / "seed.txt").write_text("operator wip\n")  # dirty before, recorded by no run
    before = take_fixer_footprint(repo)
    (repo / "seed.txt").write_text("operator wip, then formatted\n")
    (repo / "work.py").write_text("x = 2\n")

    with caplog.at_level(logging.WARNING, logger="loregarden.services.gate_recovery"):
        _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    assert _committed_paths(repo) == {"work.py"}
    assert (repo / "seed.txt").read_text() == "operator wip, then formatted\n"
    [artifact] = [
        a
        for a in _artifacts(session, ticket, ArtifactKind.CONTEXT)
        if a.title.startswith("Auto-fixed")
    ]
    rows = {row["k"]: row["v"] for row in json.loads(artifact.content_json)["rows"]}
    assert "seed.txt" in rows["Left uncommitted"]
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_a_dirty_file_the_fixer_did_not_touch_is_neither_committed_nor_reported(
    ticket_session, repo: Path
):
    session, ticket = ticket_session
    (repo / "seed.txt").write_text("operator wip\n")
    before = take_fixer_footprint(repo)
    (repo / "work.py").write_text("x = 2\n")

    _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    assert _committed_paths(repo) == {"work.py"}
    [artifact] = _artifacts(session, ticket, ArtifactKind.CONTEXT)
    assert "Left uncommitted" not in {row["k"] for row in json.loads(artifact.content_json)["rows"]}


# --- AC3: nothing to commit stays quiet -------------------------------------


def test_nothing_to_commit_is_quiet(ticket_session, repo: Path, caplog: pytest.LogCaptureFixture):
    session, ticket = ticket_session
    before = take_fixer_footprint(repo)

    with caplog.at_level(logging.WARNING):
        _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert _artifacts(session, ticket, ArtifactKind.CONTEXT) == []
    assert _artifacts(session, ticket, ArtifactKind.ERROR) == []
    assert _head_subject(repo) == "work"


def test_a_workspace_with_no_repository_is_quiet(tmp_path: Path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    before = take_fixer_footprint(plain)
    (plain / "fixed.txt").write_text("x\n")

    outcome = commit_fixer_changes(plain, before, recorded=[], message="m")

    assert (outcome.committed, outcome.error, outcome.left_uncommitted) == (False, "", ())


def test_a_tree_git_cannot_read_after_the_fixers_is_an_error(repo: Path):
    before = take_fixer_footprint(repo)
    (repo / ".git" / "index").write_bytes(b"not an index")

    outcome = commit_fixer_changes(repo, before, recorded=["work.py"], message="m")

    assert not outcome.committed
    assert outcome.error


def test_the_recorded_path_is_still_committed_with_the_usual_subject(ticket_session, repo: Path):
    """The behaviour that worked before 850 is unchanged."""
    session, ticket = ticket_session
    before = take_fixer_footprint(repo)
    (repo / "work.py").write_text("x = 2\n")

    _recovery(session)._commit_autofix(ticket, _STAGE, "ruff fixed", repo_root=repo, before=before)

    assert _committed_paths(repo) == {"work.py"}
    assert _head_subject(repo) == f"chore({_STAGE}): auto-fix static-analysis gate [autofix-1]"
    titles = [a.title for a in _artifacts(session, ticket, ArtifactKind.CONTEXT)]
    assert titles == [f"Auto-fixed static-analysis gate — {_STAGE}"]


def test_a_fixer_that_only_rewrites_a_file_back_is_not_committed(ticket_session, repo: Path):
    session, ticket = ticket_session
    (repo / "seed.txt").write_text("wip\n")
    before = take_fixer_footprint(repo)
    git(repo, "checkout", "--", "seed.txt")  # the fixer reverts it to the committed text

    _recovery(session)._commit_autofix(ticket, _STAGE, "", repo_root=repo, before=before)

    assert _head_subject(repo) == "work"
    assert git(repo, "status", "--porcelain").stdout == ""
