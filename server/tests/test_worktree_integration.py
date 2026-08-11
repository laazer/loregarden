"""Everything downstream of a stage looks at the tree the stage wrote in.

Moving execution into per-ticket worktrees quietly re-points three things that
still read the shared checkout, and each fails silently rather than loudly: a
gate lints a copy of the repo with none of the ticket's edits and passes, a
scoped commit stages paths that match nothing and commits nothing, and the Diff
tab shows whatever else happened to be dirty in the shared tree.
"""

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Workspace
from loregarden.services.artifact_service import capture_git_diff
from loregarden.services.gate_runner import run_transition_gates
from loregarden.services.git_commit_push_service import commit_paths
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.ticket_worktree import resolve_execution_root, resolve_ticket_root
from sqlmodel import Session
from tests.worktree_helpers import make_repo, make_ticket


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
    return make_ticket(session, workspace)


@pytest.fixture(name="worktree")
def worktree_fixture(session, workspace, ticket):
    """The ticket's tree, with one stage's edit sitting in it uncommitted."""
    run = AgentRun(
        run_code="r1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    root = resolve_execution_root(session, run, ticket, workspace)
    (root / "stage-work.txt").write_text("what the agent wrote\n")
    return root


def test_the_ticket_tree_is_the_worktree_not_the_checkout(
    session, workspace, ticket, worktree, repo
):
    assert resolve_ticket_root(session, ticket, workspace) == worktree
    assert worktree != repo


def test_a_scoped_commit_captures_work_that_lives_in_the_worktree(
    session, workspace, ticket, worktree
):
    committed = commit_paths(session, ticket, "LG-1: stage work", ["stage-work.txt"])

    assert committed is True


def test_gates_run_in_the_worktree_so_they_see_the_stage_s_edits(
    session, workspace, ticket, worktree
):
    profile = OrchestrationProfile(
        slug="gates-test",
        gates=GatesConfig(
            enabled=True,
            # Fails unless it runs somewhere that has the stage's file.
            commands=["test -f {workspace_root}/stage-work.txt"],
        ),
    )

    result = run_transition_gates(
        session,
        profile,
        workspace,
        ticket,
        from_stage="implement",
        to_stage="review",
    )

    assert result.ok, result.message


def test_the_diff_artifact_is_taken_from_the_worktree(session, workspace, ticket, worktree, repo):
    # `git diff` is blind to untracked files, so commit the stage's work the way
    # the pipeline does before asking what this ticket changed.
    commit_paths(session, ticket, "LG-1: stage work", ["stage-work.txt"])
    # Something unrelated in the shared checkout must not turn up as this
    # ticket's diff.
    (repo / "someone-elses.txt").write_text("not this ticket\n")

    diff = capture_git_diff(workspace, resolve_ticket_root(session, ticket, workspace))

    rendered = str(diff)
    assert "stage-work.txt" in rendered
    assert "someone-elses.txt" not in rendered
