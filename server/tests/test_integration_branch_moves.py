"""Every move of an integration branch goes through one owner, and is recorded.

`integration/lg-milestone-that-491` lost lg-milestone-that-1316's landing and
nobody could say which writer moved it: the branch was recreated and its
reflog went with it. `move_integration_branch` guards, keeps the tip it
leaves, moves by compare-and-swap with the writer in the reflog message, and
appends a ledger row that outlives the branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loregarden.db.versions.integration_branch_moves import m_integration_branch_moves
from loregarden.mcp.branch_moves_tool import branch_moves_tool
from loregarden.models.domain import IntegrationBranchMove
from loregarden.services.branch_triage_service import delete_branch
from loregarden.services.git_merge_noco import merge_without_checkout
from loregarden.services.integration_branch_moves import (
    BranchMoveRefused,
    branch_moves,
    enable_ref_logs,
    enable_ref_logs_in_workspaces,
    move_integration_branch,
    mover,
)
from loregarden.services.landing_survival import LandingsWouldBeLost
from loregarden.services.settled_refs import settled_tips
from loregarden.services.target_branch import resolve_target_branch
from sqlalchemy import inspect
from sqlmodel import Session, select
from tests.tree_publish_helpers import (
    clear_process_state,
    landed_tree,
    make_workspace,
    repo_with_remote,
    squash_onto_remote_main,
)
from tests.worktree_helpers import commit_on, git

BRANCH = "integration/lg-ms-1"


@pytest.fixture(autouse=True)
def _fresh_process_state():
    clear_process_state()
    yield
    clear_process_state()


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return repo_with_remote(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    return make_workspace(session, repo, "branch-moves")


def _sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def _ledger(session: Session) -> list[IntegrationBranchMove]:
    return list(session.exec(select(IntegrationBranchMove)).all())


# ---- the owner ----


def test_a_move_is_recorded_with_its_writer_tips_and_run(session, workspace, repo):
    enable_ref_logs(repo)
    git(repo, "branch", BRANCH, "main")
    old = _sha(repo, BRANCH)
    new = commit_on(repo, "main", "a.txt", "a\n")

    row = move_integration_branch(
        session, workspace, BRANCH, new, "take main", writer="test.writer", run_id="run-1"
    )

    assert _sha(repo, BRANCH) == new
    assert row is not None
    assert (row.branch, row.old_tip, row.new_tip, row.writer, row.why, row.run_id) == (
        BRANCH,
        old,
        new,
        "test.writer",
        "take main",
        "run-1",
    )
    assert row.moved_at is not None
    reflog = git(repo, "reflog", "show", "--format=%gs", f"refs/heads/{BRANCH}").stdout
    assert "test.writer: take main" in reflog


def test_a_fast_forward_keeps_nothing_and_a_rewind_keeps_the_tip_it_leaves(
    session, workspace, repo
):
    git(repo, "branch", BRANCH, "main")
    start = _sha(repo, BRANCH)
    ahead = commit_on(repo, BRANCH, "b.txt", "b\n")
    assert settled_tips(repo, BRANCH) == []

    rewind = move_integration_branch(
        session, workspace, BRANCH, start, "rewind", writer="test.rewind"
    )

    assert settled_tips(repo, BRANCH) == [ahead]
    assert rewind is not None and "not a fast forward" in rewind.why
    forward = move_integration_branch(
        session, workspace, BRANCH, ahead, "forward", writer="test.forward"
    )
    assert settled_tips(repo, BRANCH) == [ahead]
    assert forward is not None and forward.why == "forward"


def test_a_move_from_a_tip_that_moved_since_is_refused_and_not_recorded(session, workspace, repo):
    git(repo, "branch", BRANCH, "main")
    stale = _sha(repo, BRANCH)
    moved_meanwhile = commit_on(repo, BRANCH, "c.txt", "c\n")

    with pytest.raises(BranchMoveRefused):
        move_integration_branch(
            session, workspace, BRANCH, "", "delete", writer="test.delete", expected=stale
        )

    assert _sha(repo, BRANCH) == moved_meanwhile
    assert _ledger(session) == []


def test_a_creation_over_an_existing_branch_is_refused(session, workspace, repo):
    git(repo, "branch", BRANCH, "main")
    other = commit_on(repo, "main", "d.txt", "d\n")

    with pytest.raises(BranchMoveRefused):
        move_integration_branch(
            session, workspace, BRANCH, other, "create", writer="test.create", expected=""
        )
    assert _ledger(session) == []


def test_a_move_that_drops_a_landing_is_refused_before_anything_moves(session, workspace, repo):
    _ms, child, branch = landed_tree(session, workspace, repo)
    tip = _sha(repo, branch)
    before = len(_ledger(session))

    with pytest.raises(LandingsWouldBeLost, match=child.external_id):
        move_integration_branch(session, workspace, branch, "", "delete", writer="test.delete")
    with pytest.raises(LandingsWouldBeLost):
        move_integration_branch(
            session, workspace, branch, _sha(repo, "main"), "reset", writer="test.reset"
        )

    assert _sha(repo, branch) == tip
    assert settled_tips(repo, branch) == []
    assert len(_ledger(session)) == before


def test_the_mover_reports_a_refusal_instead_of_raising(session, workspace, repo):
    git(repo, "branch", BRANCH, "main")
    move = mover(session, workspace, writer="test.mover", why="move")

    assert "was not moved" in move(BRANCH, _sha(repo, "main"), "0" * 40)


def test_a_merge_helper_refuses_an_integration_target_without_the_owner(repo):
    git(repo, "branch", BRANCH, "main")
    git(repo, "branch", "feature", "main")
    commit_on(repo, "feature", "e.txt", "e\n")
    tip = _sha(repo, BRANCH)

    with pytest.raises(ValueError, match="integration_branch_moves"):
        merge_without_checkout(repo, target=BRANCH, source="feature", subject="bare")

    assert _sha(repo, BRANCH) == tip


# ---- every writer of an integration branch goes through it ----


def test_every_integration_branch_writer_records_its_moves(session, workspace, repo):
    """Creation and the refresh (`target_branch`), landing (`land_ticket`) and deletion
    (`branch_triage_service`), each by name. The reset to the base moves through the
    refresh's mover; `test_landing_survival` drives it."""
    ms, child, branch = landed_tree(session, workspace, repo)
    commit_on(repo, "main", "base.txt", "base moved\n")
    git(repo, "push", "-q", "origin", "main")
    resolve_target_branch(session, child, workspace, repo_root=repo)  # the refresh
    squash_onto_remote_main(repo)
    assert delete_branch(session, workspace, branch, force=True).deleted

    writers = [move.writer for move in reversed(branch_moves(session, workspace, branch=branch))]

    assert writers[:2] == ["target_branch.create", "land_ticket.land"]
    assert "target_branch.refresh" in writers
    assert writers[-1] == "branch_triage_service.delete_branch"
    assert git(repo, "branch", "--list", branch).stdout.strip() == ""
    assert branch_moves(session, workspace, branch=branch)[0].new_tip == ""


# ---- reading it ----


def test_the_tool_answers_who_moved_a_branch(session, workspace, repo):
    git(repo, "branch", BRANCH, "main")
    git(repo, "branch", "integration/other", "main")
    new = commit_on(repo, "main", "f.txt", "f\n")
    move_integration_branch(session, workspace, BRANCH, new, "take main", writer="test.a")
    move_integration_branch(session, workspace, "integration/other", new, "x", writer="test.b")

    answer = json.loads(
        branch_moves_tool(session, {"workspace_slug": workspace.slug, "branch": BRANCH})
    )

    assert answer["count"] == 1
    [move] = answer["moves"]
    assert (move["branch"], move["writer"], move["new_tip"]) == (BRANCH, "test.a", new)


def test_boot_turns_on_reflogs_for_every_ref(session, workspace, repo):
    git(repo, "config", "--local", "core.logAllRefUpdates", "false")

    assert enable_ref_logs_in_workspaces(session) >= 1

    assert git(repo, "config", "--get", "core.logAllRefUpdates").stdout.strip() == "always"


def test_the_migration_creates_the_ledger_once(isolated_db):
    with isolated_db.begin() as conn:
        conn.exec_driver_sql("DROP TABLE integration_branch_moves")
        m_integration_branch_moves(conn)
        m_integration_branch_moves(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("integration_branch_moves")}
    assert columns == {
        "id",
        "workspace_id",
        "branch",
        "old_tip",
        "new_tip",
        "writer",
        "why",
        "run_id",
        "moved_at",
    }
