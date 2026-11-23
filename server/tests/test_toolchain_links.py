"""loregarden's own dependency link in a ticket tree is not the ticket's work.

lmkr-execution-leaves-11 finished every stage and could not land: its repository
ignores `client/node_modules/`, git does not match a symlink to that, and the
link loregarden itself made read as the ticket's one uncommitted file.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import toolchain_links
from loregarden.services.gate_producer import snapshot_worktree
from loregarden.services.git_automation import PublishSubject, _commit
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.land_ticket import land_ticket, landing_would_succeed
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.run_worktree_commit import commit_succeeded_run_worktree
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.toolchain_links import excluding_own_links, own_toolchain_links
from loregarden.services.worktree_lifecycle import KeepReason, checkout_loss
from loregarden.services.worktree_snapshot import read_tree
from sqlmodel import Session
from tests.repo_paths import REPO_ROOT
from tests.worktree_helpers import git, make_repo

LINK = "client/node_modules"
IGNORE_RULE = "client/node_modules/\n"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path, request):
    """Dependencies installed under `client/`, ignored the way loremaker ignores them."""
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(getattr(request, "param", IGNORE_RULE))
    (repo / "client").mkdir()
    (repo / "client" / "package.json").write_text("{}\n")
    (repo / "service.py").write_text("VALUE = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a client")
    (repo / LINK).mkdir()
    (repo / LINK / "marker.txt").write_text("installed\n")
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="linked", name="linked", repo_path=str(repo))
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
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
        branch="loregarden/lg-impl-2",
        git_automation_json=serialize_override({"commit": True}),
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="run")
def run_fixture(session, workspace, ticket):
    """A run that recorded the link, so landing judges whatever is at that path."""
    run = AgentRun(
        run_code="r-impl",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
        changed_paths_json=json.dumps([LINK, f"{LINK}/marker.txt"]),
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


@pytest.fixture(name="linked")
def linked_fixture(tree, repo):
    """The tree, once its premise holds: git itself calls loregarden's link untracked."""
    assert (tree / LINK).is_symlink() and (tree / LINK / "marker.txt").is_file()
    assert git(tree, "status", "--porcelain").stdout == f"?? {LINK}\n"
    return tree


def _replace_link(tree: Path, target: Path) -> None:
    (tree / LINK).unlink()
    (tree / LINK).symlink_to(target)


# ---- the landing ------------------------------------------------------------


def test_the_link_loregarden_made_does_not_block_the_landing(session, workspace, ticket, linked):
    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert landing_would_succeed(session, ticket, workspace), (
        "the landing-cleared sweep re-lands a ticket already blocked on the link"
    )

    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail
    assert LINK not in git(linked, "ls-tree", "-r", "--name-only", "HEAD").stdout


def test_a_committed_handoff_naming_the_link_does_not_block_the_landing(
    session, workspace, ticket, run, linked
):
    """How lmkr-execution-leaves-11 was refused: no run had recorded the link, but its committed
    handoff listed it among the tree's dirty paths, which landing reads as a reference to it."""
    run.changed_paths_json = "[]"
    session.add(run)
    (linked / "handoff-latest.yaml").write_text(f"dirty_paths:\n  - {LINK}\n")
    git(linked, "add", "handoff-latest.yaml")
    git(linked, "commit", "-q", "-m", "the handoff")
    session.commit()

    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert land_ticket(session, ticket, workspace).ok
    assert read_tree(linked).dirty_paths == frozenset(), "so the next handoff does not list it"


def test_the_tickets_own_edit_beside_the_link_still_blocks(session, workspace, ticket, linked):
    (linked / "service.py").write_text("VALUE = 2\n")

    assert uncommitted_at_landing(session, ticket, workspace) == ("service.py",)


def test_a_relative_spelling_of_the_same_link_is_still_ours(session, workspace, ticket, linked):
    source = (linked / LINK).resolve()
    _replace_link(linked, Path(os.path.relpath(source, (linked / LINK).parent.resolve())))
    assert (linked / LINK / "marker.txt").is_file()

    assert own_toolchain_links(linked) == {LINK}


def test_a_foreign_symlink_is_reported(session, workspace, ticket, linked, tmp_path):
    elsewhere = tmp_path / "elsewhere" / LINK
    elsewhere.mkdir(parents=True)
    _replace_link(linked, elsewhere)

    assert uncommitted_at_landing(session, ticket, workspace) == (LINK,)
    assert LINK in land_ticket(session, ticket, workspace).detail


def test_a_link_beside_the_repos_own_directory_is_reported(
    session, workspace, ticket, linked, repo
):
    (repo / "client" / "other_modules").mkdir()
    _replace_link(linked, repo / "client" / "other_modules")

    assert uncommitted_at_landing(session, ticket, workspace) == (LINK,)


def test_a_link_that_only_reaches_the_repos_directory_through_another_is_reported(
    session, workspace, ticket, linked, repo, tmp_path
):
    """What it names is someone else's file, which can be pointed anywhere after the landing."""
    hop = tmp_path / "hop"
    hop.symlink_to(repo / LINK)
    _replace_link(linked, hop)

    assert uncommitted_at_landing(session, ticket, workspace) == (LINK,)


def test_a_real_file_at_the_path_is_reported(session, workspace, ticket, linked):
    (linked / LINK).unlink()
    (linked / LINK).write_text("not a directory\n")

    assert uncommitted_at_landing(session, ticket, workspace) == (LINK,)


@pytest.mark.parametrize("repo", ["# nothing ignored\n"], indirect=True)
def test_a_real_directory_at_the_path_is_reported(session, workspace, ticket, tree):
    """In a repository that does not ignore it: one that does hides its directory itself."""
    (tree / LINK).unlink()
    (tree / LINK).mkdir()
    (tree / LINK / "marker.txt").write_text("vendored on purpose\n")

    assert uncommitted_at_landing(session, ticket, workspace) == (f"{LINK}/marker.txt",)


def test_a_link_someone_staged_is_reported(session, workspace, ticket, linked):
    """The next commit would carry it, so it is no longer only ours."""
    git(linked, "add", LINK)

    assert uncommitted_at_landing(session, ticket, workspace) == (LINK,)


@pytest.mark.parametrize("failing", ["ls-files", "worktree"])
def test_a_git_that_cannot_answer_discounts_nothing(linked, failing, caplog):
    real = toolchain_links.run_git

    def refuse(args, **kwargs):
        if args[0] == failing:
            return subprocess.CompletedProcess(args, 128, "", "fatal: refused")
        return real(args, **kwargs)

    with (
        mock.patch.object(toolchain_links, "run_git", refuse),
        caplog.at_level(logging.WARNING, logger=toolchain_links.__name__),
    ):
        assert own_toolchain_links(linked) == frozenset()
        assert read_tree(linked).dirty_paths == {LINK}
    assert "fatal: refused" in caplog.text


def test_a_tree_with_no_link_costs_no_git_call(repo):
    with mock.patch.object(toolchain_links, "run_git") as run_git:
        assert excluding_own_links(repo) == []
    run_git.assert_not_called()


@pytest.mark.parametrize("repo", [f"{LINK}\n"], indirect=True)
def test_a_link_git_already_ignores_changes_no_command(tree):
    assert (tree / LINK).is_symlink()
    with mock.patch.object(toolchain_links, "run_git", wraps=toolchain_links.run_git) as run_git:
        assert excluding_own_links(tree) == []
    assert run_git.call_count == 1, "the common case: every loregarden tree, on every read"


# ---- the commits that could carry it ------------------------------------------


def _committed(tree: Path) -> str:
    return git(tree, "ls-tree", "-r", "--name-only", "HEAD").stdout


def test_the_stage_end_commit_leaves_the_link_out(session, workspace, ticket, run, linked):
    (linked / "service.py").write_text("VALUE = 2\n")

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed and result.paths == ("service.py",), result
    assert LINK not in _committed(linked)


def test_the_whole_tree_commit_leaves_the_link_out(linked):
    (linked / "new_module.py").write_text("VALUE = 2\n")
    subject = PublishSubject(branch="", commit_message="work", pr_title="", pr_body="")

    assert _commit(linked, subject).ok

    assert "new_module.py" in _committed(linked)
    assert LINK not in _committed(linked)


def test_the_gate_snapshot_leaves_the_link_out(linked):
    (linked / "new_module.py").write_text("VALUE = 2\n")

    snapshot = snapshot_worktree(linked)

    held = git(linked, "ls-tree", "-r", "--name-only", snapshot.tree_sha).stdout
    assert "new_module.py" in held
    assert LINK not in held


# ---- the tree afterwards ------------------------------------------------------


def test_the_link_alone_does_not_keep_a_finished_tree(linked, tmp_path):
    assert checkout_loss(linked) is not KeepReason.UNCOMMITTED

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _replace_link(linked, elsewhere)
    assert checkout_loss(linked) is KeepReason.UNCOMMITTED


# ---- every other reader -------------------------------------------------------

#: A git read or sweep of a whole tree that does not need to pass over the link.
EXEMPT = {
    "services/toolchain_links.py": "the helper itself",
    "services/worktree_snapshot.py": "read_tree takes the links out of what it parsed",
    "services/landing_uncommitted.py": "intersected with read_tree's dirty paths",
    "services/symbol_map/sources.py": "keeps source files only, by suffix",
    "services/artifact_service.py": "skips paths that are not files; the link is to a directory",
    "services/checkout_guard.py": "lists what git ignores, which an untracked link is not",
    "services/disk_usage/safety.py": "asks about one path a person chose to delete",
    "services/integration_merge_agent.py": "a tree it cut itself, which is never linked",
    "services/change_pr.py": "a tree it cut itself, which is never linked",
    "services/workspace_repository.py": "a repository's first commit, before any worktree",
}


def _sweeps_a_tree(words: list[str]) -> bool:
    if "status" in words:
        return any(word.startswith("--porcelain") for word in words)
    if "add" in words:
        return bool({"-A", "--all"} & set(words))
    return "ls-files" in words and "--others" in words


def _unguarded_sweeps(source: str) -> list[int]:
    """Lines of argument lists that read or stage a whole tree without the helper."""
    found = []
    for node in ast.walk(ast.parse(source)):
        items = getattr(node, "elts", None) or getattr(node, "args", None)
        if type(items) is not list:  # a `def`'s `args` is not a list of expressions
            continue
        words = [str(item.value) for item in items if type(item) is ast.Constant]
        if _sweeps_a_tree(words) and "excluding_own_links(" not in ast.unparse(node):
            found.append(node.lineno)
    return found


def test_every_reader_of_a_ticket_tree_passes_over_the_link():
    package = REPO_ROOT / "server" / "loregarden"
    unguarded = {}
    for path in sorted(package.rglob("*.py")):
        lines = _unguarded_sweeps(path.read_text(encoding="utf-8"))
        if lines:
            unguarded[path.relative_to(package).as_posix()] = lines

    assert {name: at for name, at in unguarded.items() if name not in EXEMPT} == {}, (
        "add `*excluding_own_links(tree)` to the command, or exempt the file with its reason"
    )
    assert set(EXEMPT) == set(unguarded), "an exemption nothing needs any more"
