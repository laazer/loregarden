"""Everything downstream of a stage looks at the tree the stage wrote in.

Moving execution into per-ticket worktrees quietly re-points three things that
still read the shared checkout, and each fails silently rather than loudly: a
gate lints a copy of the repo with none of the ticket's edits and passes, a
scoped commit stages paths that match nothing and commits nothing, and the Diff
tab shows whatever else happened to be dirty in the shared tree.
"""

import textwrap

import pytest
from loregarden.models.domain import AgentRun, GitBoundary, RunStatus, Workspace
from loregarden.services.artifact_service import capture_git_diff
from loregarden.services.gate_runner import DEFAULT_TRANSITION_SCRIPT, run_transition_gates
from loregarden.services.git_commit_push_service import commit_paths
from loregarden.services.handoff_store import (
    HANDOFF_FILENAME,
    HANDOFF_SCRATCH_SUBDIR,
    build_handoff_doc,
    store_handoff,
)
from loregarden.services.handoff_writer import write_handoff
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


def test_the_handoff_export_lands_where_the_transition_script_runs(
    session, workspace, ticket, worktree, repo
):
    """The transition script runs with the worktree as cwd and reads
    ``--checkpoints-dir`` relative to it. The export that feeds it has to be
    written under that same tree — written under the shared checkout instead,
    the script finds nothing and the transition passes on nothing (737).
    """
    script = worktree / DEFAULT_TRANSITION_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text(
        textwrap.dedent(
            """\
            import argparse, pathlib, sys
            p = argparse.ArgumentParser()
            p.add_argument("--ticket-id"); p.add_argument("--transition")
            p.add_argument("--checkpoints-dir")
            a = p.parse_args()
            wanted = pathlib.Path(a.checkpoints_dir) / a.ticket_id / "%s"
            sys.exit(0 if wanted.is_file() else 1)
            """
        )
        % HANDOFF_FILENAME
    )
    store_handoff(
        session,
        ticket=ticket,
        doc=build_handoff_doc(
            external_id=ticket.external_id,
            from_agent="backend_implementer",
            to_agent="reviewer",
            checklist=[],
            required_items_met=0,
            total_required_items=0,
            boundary=GitBoundary(),
        ),
    )
    session.commit()
    profile = OrchestrationProfile(slug="gates-test", gates=GatesConfig(enabled=True))

    result = run_transition_gates(
        session, profile, workspace, ticket, from_stage="implement", to_stage="review"
    )

    exported = HANDOFF_SCRATCH_SUBDIR + "/" + ticket.external_id + "/" + HANDOFF_FILENAME
    assert (worktree / exported).is_file()
    assert not (repo / exported).exists()
    assert result.ok, result.message


def test_write_handoff_exports_into_the_worktree(session, workspace, ticket, worktree, repo):
    """The agent's handoff is exported where the gate that later reads it runs —
    the ticket's worktree — not the shared checkout (737)."""
    commit_paths(session, ticket, "LG-1: stage work", ["stage-work.txt"])

    result = write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug=workspace.slug,
        from_agent="backend_implementer",
        to_agent="reviewer",
        checklist=[
            {"item_key": "work_done", "item": "Work done", "status": "complete", "evidence": "x"}
        ],
    )

    exported = HANDOFF_SCRATCH_SUBDIR + "/" + ticket.external_id + "/" + HANDOFF_FILENAME
    assert result["status"] == "stored_unvalidated", result
    assert (worktree / exported).is_file()
    assert not (repo / exported).exists()


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
