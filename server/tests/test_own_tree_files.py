"""The handoff loregarden exports for the gates is not the ticket's work.

lmkr-execution-leaves-12 passed every stage and could not land: its repository
does not ignore `.loregarden/`, a repair run had recorded the export, and the
export read as the ticket's one uncommitted file. `test_toolchain_links` covers
the other file loregarden puts in a tree, and that every reader asks for both.
"""

from __future__ import annotations

import json
import logging
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    GitBoundary,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import own_tree_files
from loregarden.services.gate_producer import snapshot_worktree
from loregarden.services.git_automation import PublishSubject, _commit
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.handoff_store import (
    CHECKPOINTS_SUBDIR,
    HANDOFF_SCRATCH_SUBDIR,
    build_handoff_doc,
    export_for_gate,
    store_handoff,
)
from loregarden.services.land_ticket import land_ticket, landing_would_succeed
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.own_tree_files import excluding_own_files, own_files
from loregarden.services.run_worktree_commit import commit_succeeded_run_worktree
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import KeepReason, checkout_loss
from loregarden.services.worktree_snapshot import read_tree
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo

TICKET = "lg-impl-2"
EXPORT = f"{HANDOFF_SCRATCH_SUBDIR}/{TICKET}/handoff-latest.yaml"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    """A repository that does not ignore `.loregarden/`, as loremaker does not."""
    repo = make_repo(tmp_path)
    (repo / "service.py").write_text("VALUE = 1\n")
    (repo / ".loregarden").mkdir()
    (repo / ".loregarden" / "review.yaml").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a service")
    ws = Workspace(slug="exported", name="exported", repo_path=str(repo))
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
        external_id=TICKET,
        workspace_id=workspace.id,
        title="Implement the thing",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
        branch=f"loregarden/{TICKET}",
        git_automation_json=serialize_override({"commit": True}),
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="run")
def run_fixture(session, workspace, ticket):
    """A run that recorded the export, as lmkr-execution-leaves-12's repair run did."""
    run = AgentRun(
        run_code="r-impl",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
        changed_paths_json=json.dumps([EXPORT]),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture(name="tree")
def tree_fixture(session, workspace, ticket, run):
    root = resolve_execution_root(session, run, ticket, workspace)
    (root / "tests_for_it.py").write_text("def test_value():\n    assert True\n")
    git(root, "add", "tests_for_it.py")
    git(root, "commit", "-q", "-m", "the tests")
    return root


def _gate_exports(session, ticket, tree, to_agent: str) -> None:
    store_handoff(
        session,
        ticket=ticket,
        doc=build_handoff_doc(
            external_id=ticket.external_id,
            from_agent="backend_implementer",
            to_agent=to_agent,
            checklist=[],
            required_items_met=0,
            total_required_items=0,
            boundary=GitBoundary(),
        ),
    )
    session.commit()
    export_for_gate(session, ticket, repo_root=tree)


@pytest.fixture(name="tracked")
def tracked_fixture(session, ticket, tree):
    """The export committed by an agent, then rewritten by the next gate. Premise checked."""
    _gate_exports(session, ticket, tree, "reviewer")
    git(tree, "add", "-f", EXPORT)
    git(tree, "commit", "-q", "-m", "an agent committed everything it saw")
    _gate_exports(session, ticket, tree, "gatekeeper")
    assert git(tree, "status", "--porcelain").stdout == f" M {EXPORT}\n"
    return tree


def _committed(tree, ref: str = "HEAD") -> str:
    return git(tree, "ls-tree", "-r", "--name-only", ref).stdout


# ---- never tracked: it ignores itself ----------------------------------------


def test_a_new_export_is_invisible_to_git(session, ticket, tree):
    """Including to an agent's own `git add -A`, which no helper here can reach."""
    _gate_exports(session, ticket, tree, "reviewer")
    assert (tree / EXPORT).is_file()

    assert git(tree, "status", "--porcelain").stdout == ""
    git(tree, "add", "-A")
    assert git(tree, "diff", "--cached", "--name-only").stdout == ""


def test_the_rest_of_dot_loregarden_is_still_the_tickets(session, workspace, ticket, tree):
    """A workspace commits `review.yaml` and `instances.yaml` beside the exports."""
    _gate_exports(session, ticket, tree, "reviewer")
    (tree / ".loregarden" / "review.yaml").write_text("{changed: true}\n")
    (tree / ".loregarden" / "instances.yaml").write_text("{}\n")

    assert read_tree(tree).dirty_paths == {
        ".loregarden/review.yaml",
        ".loregarden/instances.yaml",
    }


def test_an_ignore_file_a_repository_keeps_there_is_left_alone(session, ticket, tree):
    keep = tree / HANDOFF_SCRATCH_SUBDIR / ".gitignore"
    keep.parent.mkdir(parents=True)
    keep.write_text("*.yaml\n")

    _gate_exports(session, ticket, tree, "reviewer")

    assert keep.read_text() == "*.yaml\n"


def test_an_untracked_export_from_before_it_ignored_itself_does_not_block_the_landing(
    session, workspace, ticket, tree
):
    """lmkr-execution-leaves-12 as it stands: exported by the build before this one."""
    _gate_exports(session, ticket, tree, "reviewer")
    (tree / HANDOFF_SCRATCH_SUBDIR / ".gitignore").unlink()
    assert git(tree, "status", "--porcelain", "-uall").stdout == f"?? {EXPORT}\n"

    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert own_files(tree) == {EXPORT}
    assert landing_would_succeed(session, ticket, workspace), (
        "the landing-cleared sweep re-lands a ticket already blocked on the export"
    )
    result = land_ticket(session, ticket, workspace)
    assert result.ok, result.detail
    assert EXPORT not in _committed(tree)


# ---- already tracked ----------------------------------------------------------


def test_a_tracked_export_a_gate_rewrote_does_not_block_the_landing(
    session, workspace, ticket, tracked
):
    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert read_tree(tracked).dirty_paths == frozenset()
    assert landing_would_succeed(session, ticket, workspace), (
        "the landing-cleared sweep re-lands a ticket already blocked on the export"
    )

    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail


def test_it_lands_when_the_target_moved_and_the_tree_must_take_it(
    session, workspace, ticket, tracked
):
    """The refresh merges the target into the ticket's own tree, over the rewritten export."""
    assert land_ticket(session, ticket, workspace).ok
    target = "integration/lg-ms-1"
    (tracked / "more.py").write_text("MORE = 1\n")
    git(tracked, "add", "more.py")
    git(tracked, "commit", "-q", "-m", "more")
    tip = git(tracked, "rev-parse", target).stdout.strip()
    theirs = git(
        tracked, "commit-tree", f"{tip}^{{tree}}", "-p", tip, "-m", "another ticket landed"
    ).stdout.strip()
    git(tracked, "update-ref", f"refs/heads/{target}", theirs)
    assert git(tracked, "status", "--porcelain").stdout == f" M {EXPORT}\n"

    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail
    assert "more.py" in _committed(tracked, target)


def test_the_tickets_own_edit_beside_the_export_still_blocks(session, workspace, ticket, tracked):
    (tracked / "service.py").write_text("VALUE = 2\n")

    assert uncommitted_at_landing(session, ticket, workspace) == ("service.py",)


def test_an_export_a_gate_removed_does_not_block(session, workspace, ticket, tracked):
    """No handoff stored means none exported, so the gate deletes the tracked file."""
    (tracked / EXPORT).unlink()
    assert git(tracked, "status", "--porcelain").stdout == f" D {EXPORT}\n"

    assert uncommitted_at_landing(session, ticket, workspace) == ()


def test_a_staged_rewrite_does_not_block(session, workspace, ticket, tracked):
    """An agent's `git add -A` after a gate: the next rewrite leaves it staged and edited."""
    git(tracked, "add", "-A")
    _gate_exports(session, ticket, tracked, "learner")
    assert git(tracked, "status", "--porcelain").stdout == f"MM {EXPORT}\n"

    assert uncommitted_at_landing(session, ticket, workspace) == ()


def test_a_mirrored_checkpoint_beside_the_handoff_is_the_exports_too(
    session, workspace, ticket, tree
):
    source = tree / CHECKPOINTS_SUBDIR / TICKET
    source.mkdir(parents=True)
    (source / "todo [1]*.json").write_text("{}\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "a checkpoint")
    _gate_exports(session, ticket, tree, "reviewer")
    mirrored = f"{HANDOFF_SCRATCH_SUBDIR}/{TICKET}/todo [1]*.json"
    git(tree, "add", "-f", "--", f":(literal){mirrored}")
    git(tree, "commit", "-q", "-m", "and its copy")
    (tree / mirrored).write_text("{changed: true}\n")

    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert git(tree, "status", "--porcelain", *excluding_own_files(tree)).stdout == ""


# ---- what a ticket did there on purpose -----------------------------------------


def test_removing_a_tickets_exports_is_reported_and_committed(
    session, workspace, ticket, run, tracked
):
    """A ticket tidying old exports away must not lose the removal at landing."""
    (tracked / EXPORT).unlink()
    (tracked / EXPORT).parent.rmdir()
    assert uncommitted_at_landing(session, ticket, workspace) == (EXPORT,)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed, result
    assert EXPORT not in _committed(tracked)


def test_a_removal_someone_staged_is_reported_and_committed(
    session, workspace, ticket, run, tracked
):
    git(tracked, "rm", "-q", "--cached", EXPORT)
    assert (tracked / EXPORT).is_file()
    assert uncommitted_at_landing(session, ticket, workspace) == (EXPORT,)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed, result
    assert EXPORT not in _committed(tracked)


def test_a_file_that_is_not_in_a_tickets_directory_is_reported(session, workspace, ticket, tracked):
    stray = tracked / HANDOFF_SCRATCH_SUBDIR / "README.txt"
    stray.write_text("kept here on purpose\n")
    git(tracked, "add", "-f", str(stray))

    assert uncommitted_at_landing(session, ticket, workspace) == (
        f"{HANDOFF_SCRATCH_SUBDIR}/README.txt",
    )


def test_a_link_where_the_exports_go_is_reported(tree, tmp_path):
    """git names the link itself, which is no ticket's export."""
    (tmp_path / "elsewhere").mkdir()
    (tree / HANDOFF_SCRATCH_SUBDIR).symlink_to(tmp_path / "elsewhere")

    assert own_files(tree) == frozenset()
    assert read_tree(tree).dirty_paths == {HANDOFF_SCRATCH_SUBDIR}


# ---- the commits that could carry it ------------------------------------------


def _committed_export(tree) -> str:
    return git(tree, "show", f"HEAD:{EXPORT}").stdout


def test_the_stage_end_commit_leaves_the_rewrite_out(session, ticket, run, tracked):
    before = _committed_export(tracked)
    (tracked / "service.py").write_text("VALUE = 2\n")

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed and result.paths == ("service.py",), result
    assert _committed_export(tracked) == before


def test_the_whole_tree_commit_leaves_the_rewrite_out(tracked):
    before = _committed_export(tracked)
    (tracked / "new_module.py").write_text("VALUE = 2\n")
    subject = PublishSubject(branch="", commit_message="work", pr_title="", pr_body="")

    assert _commit(tracked, subject).ok

    assert "new_module.py" in _committed(tracked)
    assert _committed_export(tracked) == before


def test_the_gate_snapshot_leaves_the_rewrite_out(tracked):
    (tracked / "new_module.py").write_text("VALUE = 2\n")

    snapshot = snapshot_worktree(tracked)

    assert "new_module.py" in _committed(tracked, snapshot.tree_sha)
    held = git(tracked, "show", f"{snapshot.tree_sha}:{EXPORT}").stdout
    assert held == _committed_export(tracked)


def test_the_rewrite_alone_does_not_keep_a_finished_tree(tracked):
    assert checkout_loss(tracked) is not KeepReason.UNCOMMITTED

    (tracked / "service.py").write_text("VALUE = 2\n")
    assert checkout_loss(tracked) is KeepReason.UNCOMMITTED


# ---- what it costs, and when git cannot say -----------------------------------


def test_a_tree_with_no_export_costs_no_git_call(tree):
    with mock.patch.object(own_tree_files, "run_git") as run_git:
        assert excluding_own_files(tree) == []
    run_git.assert_not_called()


def test_an_ignored_export_changes_no_command(session, ticket, tree):
    _gate_exports(session, ticket, tree, "reviewer")

    assert excluding_own_files(tree) == []


def test_a_git_that_cannot_answer_discounts_nothing(tracked, caplog):
    refused = subprocess.CompletedProcess([], 128, "", "fatal: refused")
    with (
        mock.patch.object(own_tree_files, "run_git", return_value=refused),
        caplog.at_level(logging.WARNING, logger=own_tree_files.__name__),
    ):
        assert own_files(tracked) == frozenset()
        assert read_tree(tracked).dirty_paths == {EXPORT}
    assert "fatal: refused" in caplog.text


@pytest.mark.parametrize("name", ["todo [1]*.json", "a?b.md", "back\\slash.md", "plain.md"])
def test_an_awkward_name_is_left_out_and_its_lookalike_is_not(session, ticket, tree, name):
    _gate_exports(session, ticket, tree, "reviewer")
    awkward = f"{HANDOFF_SCRATCH_SUBDIR}/{TICKET}/{name}"
    (tree / awkward).write_text("1\n")
    git(tree, "add", "-f", "--", f":(literal){awkward}")
    git(tree, "commit", "-q", "-m", "committed")
    (tree / awkward).write_text("2\n")
    (tree / "xloregarden" / "handoffs" / TICKET).mkdir(parents=True)
    lookalike = f"xloregarden/handoffs/{TICKET}/{name}"
    (tree / lookalike).write_text("the ticket's own\n")

    git(tree, "add", "-A", *excluding_own_files(tree))

    assert git(tree, "diff", "--cached", "--name-only", "-z").stdout == f"{lookalike}\0"
    assert own_files(tree) == {awkward}


@pytest.mark.parametrize("name", ["a*b.md", "a?b.md"])
def test_leaving_out_a_name_does_not_hide_a_removal_it_would_match(session, ticket, tree, name):
    _gate_exports(session, ticket, tree, "reviewer")
    directory = f"{HANDOFF_SCRATCH_SUBDIR}/{TICKET}"
    for each in (name, "axb.md"):
        (tree / directory / each).write_text("1\n")
        git(tree, "add", "-f", "--", f":(literal){directory}/{each}")
    git(tree, "commit", "-q", "-m", "committed")
    (tree / directory / name).write_text("2\n")
    git(tree, "rm", "-q", "--cached", f"{directory}/axb.md")

    seen = git(tree, "status", "--porcelain", "-z", *excluding_own_files(tree)).stdout

    assert seen == f"D  {directory}/axb.md\0"
