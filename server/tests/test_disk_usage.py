"""Disk usage: the scan's arithmetic, and every refusal cleanup's safety check owes.

The safety tests run against real git repositories and real worktrees in
`tmp_path`: the checks shell out to git, and a mocked git would only prove the
mock. Nothing here moves a file to the real Trash; `send2trash` is replaced with
a move into a directory under `tmp_path`.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from unittest import mock

import pytest
from loregarden.mcp.disk_usage_tool import disk_usage_tool
from loregarden.mcp.tool_ids import READ_ONLY_MCP_TOOLS, McpTool
from loregarden.models.domain import (
    DiskCleanupEvent,
    DiskCleanupMethod,
    DiskCleanupOutcome,
    DiskKind,
    DiskRootOrigin,
    TicketState,
    Worktree,
    WorktreeState,
)
from loregarden.services.disk_usage import cleanup, store
from loregarden.services.disk_usage.roots import ScanPlan, ScanRoot, WorktreeRef, scan_plan
from loregarden.services.disk_usage.safety import SafetyContext, assess
from loregarden.services.disk_usage.scanner import (
    MIN_NODE_BYTES,
    KindHints,
    ScanProgress,
    scan_roots,
)
from loregarden.services.git_subprocess import run_git
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_ticket, make_workspace

MB = 1024 * 1024


def _git(cwd: Path, *args: str) -> str:
    proc = run_git(list(args), cwd=str(cwd), check=True, capture_output=True, text=True)
    return proc.stdout


def _write(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    return path


@pytest.fixture(name="scan_file", autouse=True)
def scan_file_fixture(tmp_path):
    """Keep the stored scan beside this test, never in the real data directory."""
    target = tmp_path / "disk-scan.json"
    with mock.patch.object(store, "_result_path", return_value=target):
        store._JOB.cached = None
        store._JOB.cached_mtime = None
        yield target


@pytest.fixture(name="repo")
def repo_fixture(tmp_path) -> Path:
    """A repository with one commit, ignoring dependencies and a data folder."""
    repo = tmp_path / "ws" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("node_modules/\ndata/\n.env\n")
    _write(repo / "src" / "app.py")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo.resolve()


@pytest.fixture(name="linked")
def linked_fixture(repo) -> Path:
    """A clean linked worktree beside the checkout, on its own branch."""
    path = repo.parent / "wt-feature"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(path))
    return path.resolve()


def _plan(repo: Path, linked: Path | None = None, extra: list[ScanRoot] | None = None) -> ScanPlan:
    worktrees = {repo: WorktreeRef(repo, repo, "main", True, "proj")}
    roots = [ScanRoot(repo, "proj", DiskRootOrigin.WORKSPACE, "proj")]
    if linked is not None:
        worktrees[linked] = WorktreeRef(linked, repo, "feature", False, "proj")
        roots.append(ScanRoot(linked, "proj worktree", DiskRootOrigin.WORKTREES, "proj"))
    return ScanPlan(
        roots=roots + (extra or []),
        worktrees=worktrees,
        workspace_roots=frozenset({repo}),
        unreadable_workspaces=[],
        missing_added=[],
    )


def _ctx(plan: ScanPlan, cwds=(), rows=None, states=None) -> SafetyContext:
    main_trees = [ref.path for ref in plan.worktrees.values() if ref.is_main]
    return SafetyContext(
        plan=plan,
        protected=[Path("/"), Path.home().resolve(), *plan.workspace_roots, *main_trees],
        loregarden_data=Path("/nonexistent-loregarden-data"),
        process_cwds=list(cwds) if cwds is not None else None,
        worktree_rows=rows or {},
        ticket_states=states or {},
    )


# --- scanner -----------------------------------------------------------------


def _scan(root: Path, hints: KindHints | None = None):
    return scan_roots(
        [(root, DiskKind.DIRECTORY)],
        hints or KindHints(frozenset(), frozenset(), frozenset()),
        progress=ScanProgress(),
        cancel=threading.Event(),
        on_progress=lambda _p: None,
    )[0]


def test_scan_counts_a_hard_linked_file_once_and_never_follows_a_symlink(tmp_path) -> None:
    root = tmp_path / "root"
    big = _write(root / "a" / "big.bin", 3 * MB)
    os.link(big, root / "b-link.bin")
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "huge.bin", 5 * MB)
    (root / "points-away").symlink_to(elsewhere)

    tree = _scan(root)

    assert 3 * MB <= tree.size_bytes < 4 * MB


def test_scan_folds_small_entries_and_keeps_the_total_exact(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "large.bin", 2 * MB)
    for index in range(30):
        _write(root / f"small-{index}.txt", 100)

    tree = _scan(root)

    names = [child.name for child in tree.children]
    assert names[0] == "large.bin"
    other = tree.children[-1]
    assert other.kind is DiskKind.OTHER and other.folded_count == 30
    assert sum(child.size_bytes for child in tree.children) == tree.size_bytes
    assert all(
        c.size_bytes >= MIN_NODE_BYTES for c in tree.children if c.kind is not DiskKind.OTHER
    )


def test_scan_marks_dependencies_and_everything_under_them(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "node_modules" / "pkg" / "lib" / "index.js", 2 * MB)

    tree = _scan(root)

    deps = tree.children[0]
    assert deps.kind is DiskKind.DEPENDENCIES
    assert deps.children[0].kind is DiskKind.DEPENDENCIES


def test_scan_marks_a_known_worktree(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "wt" / "f.bin", 2 * MB)
    tree = _scan(root, KindHints(frozenset({(root / "wt")}), frozenset(), frozenset()))
    assert tree.children[0].kind is DiskKind.WORKTREE


# --- safety: shape and protected paths ----------------------------------------


def test_ignored_dependencies_inside_a_checkout_are_allowed(repo) -> None:
    _write(repo / "node_modules" / "pkg" / "index.js")
    verdict = assess(_ctx(_plan(repo)), str(repo / "node_modules"))
    assert verdict.allowed, verdict.reasons
    assert verdict.method is DiskCleanupMethod.TRASH
    assert verdict.kind is DiskKind.DEPENDENCIES


def test_tracked_files_are_refused(repo) -> None:
    verdict = assess(_ctx(_plan(repo)), str(repo / "src"))
    assert not verdict.allowed
    assert "tracked by git" in verdict.reasons[0]


def test_untracked_work_is_refused(repo) -> None:
    _write(repo / "notes" / "draft.md")
    verdict = assess(_ctx(_plan(repo)), str(repo / "notes"))
    assert not verdict.allowed
    assert "untracked" in verdict.reasons[0]


def test_an_ignored_folder_holding_a_database_is_refused(repo) -> None:
    _write(repo / "data" / "app.sqlite3")
    verdict = assess(_ctx(_plan(repo)), str(repo / "data"))
    assert not verdict.allowed
    assert "app.sqlite3" in verdict.reasons[0]


def test_the_main_checkout_is_refused(repo) -> None:
    outer = ScanRoot(repo.parent, "ws", DiskRootOrigin.ADDED)
    plan = _plan(repo, extra=[outer])
    plan.roots.remove(plan.roots[0])
    verdict = assess(_ctx(plan), str(repo))
    assert not verdict.allowed
    assert "never touches" in verdict.reasons[0]


def test_a_folder_containing_a_checkout_is_refused(repo) -> None:
    outer = ScanRoot(repo.parent.parent, "tmp", DiskRootOrigin.ADDED)
    plan = _plan(repo, extra=[outer])
    plan.roots.remove(plan.roots[0])
    verdict = assess(_ctx(plan), str(repo.parent))
    assert not verdict.allowed


def test_a_path_outside_every_scanned_root_is_refused(repo, tmp_path) -> None:
    stray = tmp_path / "elsewhere" / "node_modules"
    _write(stray / "x.js")
    verdict = assess(_ctx(_plan(repo)), str(stray))
    assert not verdict.allowed
    assert "outside every scanned folder" in verdict.reasons[0]


def test_a_symlink_is_refused(repo, tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (repo / "node_modules").symlink_to(target)
    verdict = assess(_ctx(_plan(repo)), str(repo / "node_modules"))
    assert not verdict.allowed
    assert "symlink" in verdict.reasons[0]


def test_a_path_that_is_not_normalized_is_refused(repo) -> None:
    _write(repo / "node_modules" / "x.js")
    verdict = assess(_ctx(_plan(repo)), f"{repo}/src/../node_modules")
    assert not verdict.allowed


def test_inside_a_git_object_store_is_refused(repo) -> None:
    verdict = assess(_ctx(_plan(repo)), str(repo / ".git" / "objects"))
    assert not verdict.allowed
    assert "object store" in verdict.reasons[0]


def test_a_folder_a_process_works_in_is_refused(repo) -> None:
    deps = repo / "node_modules"
    _write(deps / "pkg" / "x.js")
    ctx = _ctx(_plan(repo), cwds=[("4242", "node", deps / "pkg")])
    verdict = assess(ctx, str(deps))
    assert not verdict.allowed
    assert "node (pid 4242)" in verdict.reasons[0]


def test_unknown_process_list_refuses_everything(repo) -> None:
    _write(repo / "node_modules" / "x.js")
    verdict = assess(_ctx(_plan(repo), cwds=None), str(repo / "node_modules"))
    assert not verdict.allowed
    assert "Could not list running processes" in verdict.reasons[0]


def test_outside_git_only_caches_dependencies_and_builds_are_allowed(tmp_path, repo) -> None:
    added = (tmp_path / "home-ish").resolve()
    _write(added / ".cache" / "tool" / "blob.bin")
    _write(added / "Documents" / "taxes.pdf")
    plan = _plan(repo, extra=[ScanRoot(added, "home-ish", DiskRootOrigin.ADDED)])

    cache = assess(_ctx(plan), str(added / ".cache" / "tool"))
    personal = assess(_ctx(plan), str(added / "Documents"))

    assert cache.allowed and cache.kind is DiskKind.CACHE
    assert not personal.allowed
    assert "Delete it yourself" in personal.reasons[0]


def test_outside_git_a_nested_repository_is_never_a_cache(tmp_path, repo) -> None:
    added = (tmp_path / "added").resolve()
    nested = added / "projects"
    _write(nested / "thing" / "file.txt")
    _git(nested / "thing", "init", "-q")
    plan = _plan(repo, extra=[ScanRoot(added, "added", DiskRootOrigin.ADDED)])
    verdict = assess(_ctx(plan), str(nested))
    assert not verdict.allowed


# --- safety: worktrees ---------------------------------------------------------


def test_a_clean_linked_worktree_is_allowed_and_keeps_its_branch(repo, linked) -> None:
    verdict = assess(_ctx(_plan(repo, linked)), str(linked))
    assert verdict.allowed, verdict.reasons
    assert verdict.method is DiskCleanupMethod.TRASH_WORKTREE
    assert verdict.worktree_repo == str(repo)
    assert any("feature" in note for note in verdict.notes)


def test_a_worktree_with_an_untracked_file_is_refused(repo, linked) -> None:
    _write(linked / "scratch.txt")
    verdict = assess(_ctx(_plan(repo, linked)), str(linked))
    assert not verdict.allowed
    assert "uncommitted or untracked" in verdict.reasons[0]


def test_a_worktree_whose_head_left_its_branch_is_refused(repo, linked) -> None:
    _git(linked, "checkout", "-q", "--detach")
    _write(linked / "src" / "more.py")
    _git(linked, "add", ".")
    _git(linked, "commit", "-q", "-m", "detached work")
    verdict = assess(_ctx(_plan(repo, linked)), str(linked))
    assert not verdict.allowed
    assert "branch or tag" in verdict.reasons[0]


def test_a_worktree_holding_an_ignored_env_file_is_refused(repo, linked) -> None:
    (linked / ".env").write_text("TOKEN=x\n")
    verdict = assess(_ctx(_plan(repo, linked)), str(linked))
    assert not verdict.allowed
    assert ".env" in verdict.reasons[0]


def test_a_worktree_with_only_regenerable_ignored_files_is_allowed(repo, linked) -> None:
    _write(linked / "node_modules" / "pkg" / "cert.pem")
    verdict = assess(_ctx(_plan(repo, linked)), str(linked))
    assert verdict.allowed, verdict.reasons


def test_a_worktree_an_unfinished_ticket_owns_is_refused(isolated_db, repo, linked) -> None:
    with Session(isolated_db) as session:
        ws = make_workspace(session)
        ticket = make_ticket(session, workspace_id=ws.id, state=TicketState.IN_PROGRESS)
        run = make_agent_run(session, workspace_id=ws.id)
        row = Worktree(
            workspace_id=ws.id,
            agent_run_id=run.id,
            ticket_id=ticket.id,
            worktree_path=str(linked),
            branch="feature",
        )
        ctx = _ctx(_plan(repo, linked), rows={linked: row}, states={ticket.id: ticket.state})
    verdict = assess(ctx, str(linked))
    assert not verdict.allowed
    assert "unfinished ticket" in verdict.reasons[0]


def test_a_finished_tickets_worktree_is_allowed(isolated_db, repo, linked) -> None:
    with Session(isolated_db) as session:
        ws = make_workspace(session)
        ticket = make_ticket(session, workspace_id=ws.id, state=TicketState.DONE)
        run = make_agent_run(session, workspace_id=ws.id)
        row = Worktree(
            workspace_id=ws.id, agent_run_id=run.id, ticket_id=ticket.id, worktree_path=str(linked)
        )
        ctx = _ctx(_plan(repo, linked), rows={linked: row}, states={ticket.id: ticket.state})
    assert assess(ctx, str(linked)).allowed


def test_scan_marks_any_folder_with_a_git_file_as_a_worktree(repo, linked) -> None:
    _write(linked / "big.bin", 2 * MB)
    _write(repo / "big.bin", 2 * MB)
    tree = _scan(linked.parent)
    kinds = {child.name: child.kind for child in tree.children}
    assert kinds[linked.name] is DiskKind.WORKTREE
    # The checkout itself has a .git directory, not a file: it is not a worktree.
    assert kinds.get("repo") is not DiskKind.WORKTREE


def test_a_worktree_of_an_unregistered_repo_is_found_and_judged_like_any_other(
    repo, linked, tmp_path
) -> None:
    """The repository is not a workspace; its worktree sits in an added folder."""
    added = ScanRoot(linked.parent, "added", DiskRootOrigin.ADDED)
    plan = ScanPlan(
        roots=[added],
        worktrees={},
        workspace_roots=frozenset(),
        unreadable_workspaces=[],
        missing_added=[],
    )
    clean = assess(_ctx(plan), str(linked))
    assert clean.allowed, clean.reasons
    assert clean.method is DiskCleanupMethod.TRASH_WORKTREE
    assert clean.worktree_repo == str(repo)

    _write(linked / "wip.txt")
    dirty = assess(_ctx(plan), str(linked))
    assert not dirty.allowed

    main = assess(_ctx(plan), str(repo))
    assert not main.allowed


# --- roots ---------------------------------------------------------------------


def test_scan_plan_scans_a_worktree_directory_strays_included(isolated_db, repo) -> None:
    trees = repo.parent / ".worktrees"
    for name in ("one", "two"):
        _git(repo, "worktree", "add", "-q", "-b", name, str(trees / name))
    with Session(isolated_db) as session:
        make_workspace(session, slug="proj", repo_path=str(repo))
        plan = scan_plan(session)

    roots = {root.path: root.origin for root in plan.roots}
    assert roots[repo] is DiskRootOrigin.WORKSPACE
    assert roots[trees.resolve()] is DiskRootOrigin.WORKTREES
    assert plan.worktrees[(trees / "one").resolve()].branch == "one"
    assert plan.worktrees[repo].is_main


def test_worktrees_beside_the_checkout_never_scan_their_shared_parent(
    isolated_db, repo, linked
) -> None:
    second = repo.parent / "wt-second"
    _git(repo, "worktree", "add", "-q", "-b", "second", str(second))
    with Session(isolated_db) as session:
        make_workspace(session, slug="proj", repo_path=str(repo))
        plan = scan_plan(session)

    paths = {root.path for root in plan.roots}
    assert paths == {repo, linked, second.resolve()}


# --- cleanup: preview, then execute -----------------------------------------------


def _fake_trash(trash: Path):
    def move(path: str) -> None:
        trash.mkdir(exist_ok=True)
        shutil.move(path, trash / Path(path).name)

    return move


@pytest.fixture(name="real_context")
def real_context_fixture(repo, linked):
    """`build_context` on a hand-made plan, with lsof answering 'nobody is there'."""
    plan = _plan(repo, linked)
    with (
        mock.patch.object(cleanup, "scan_plan", return_value=plan),
        mock.patch("loregarden.services.disk_usage.safety.list_process_cwds", return_value=[]),
    ):
        yield plan


def test_preview_then_execute_moves_only_confirmed_allowed_paths(
    isolated_db, repo, linked, real_context, tmp_path
) -> None:
    _write(repo / "node_modules" / "pkg" / "index.js")
    trash = tmp_path / "trash"
    with Session(isolated_db) as session:
        plan = cleanup.preview(session, [str(repo / "node_modules"), str(repo / "src")])
        assert plan.allowed_count == 1 and plan.refused_count == 1
        with mock.patch.object(cleanup, "send2trash", side_effect=_fake_trash(trash)) as trash_mock:
            result = cleanup.execute(session, plan.plan_id, [str(repo / "node_modules")])
        events = session.exec(select(DiskCleanupEvent)).all()

    trash_mock.assert_called_once_with(str(repo / "node_modules"))
    assert result.trashed_count == 1
    assert not (repo / "node_modules").exists()
    assert (repo / "src").exists()
    assert [e.outcome for e in events] == [DiskCleanupOutcome.TRASHED]


def test_execute_refuses_a_refused_path_and_a_reused_plan(isolated_db, repo, real_context) -> None:
    _write(repo / "node_modules" / "x.js")
    with Session(isolated_db) as session:
        plan = cleanup.preview(session, [str(repo / "node_modules"), str(repo / "src")])
        with pytest.raises(ValueError, match="Not allowed by this preview"):
            cleanup.execute(session, plan.plan_id, [str(repo / "src")])
        with pytest.raises(cleanup.PlanNotFound):
            cleanup.execute(session, plan.plan_id, [str(repo / "node_modules")])


def test_a_path_that_changed_after_preview_is_refused_not_moved(
    isolated_db, repo, real_context
) -> None:
    deps = repo / "node_modules"
    _write(deps / "x.js")
    with Session(isolated_db) as session:
        plan = cleanup.preview(session, [str(deps)])
        _write(deps / "added-later.js")
        os.utime(deps, ns=(1, 1))
        with mock.patch.object(cleanup, "send2trash") as trash_mock:
            result = cleanup.execute(session, plan.plan_id, [str(deps)])

    trash_mock.assert_not_called()
    assert result.results[0].outcome is DiskCleanupOutcome.REFUSED
    assert "changed after the preview" in result.results[0].detail


def test_a_worktree_that_became_dirty_after_preview_is_refused(
    isolated_db, repo, linked, real_context
) -> None:
    with Session(isolated_db) as session:
        plan = cleanup.preview(session, [str(linked)])
        assert plan.allowed_count == 1
        _write(linked / "new-work.py")
        with mock.patch.object(cleanup, "send2trash") as trash_mock:
            result = cleanup.execute(session, plan.plan_id, [str(linked)])

    trash_mock.assert_not_called()
    assert result.refused_count == 1


def test_trashing_a_worktree_prunes_it_and_keeps_the_branch(
    isolated_db, repo, linked, real_context, tmp_path
) -> None:
    with Session(isolated_db) as session:
        ws = make_workspace(session)
        run = make_agent_run(session, workspace_id=ws.id)
        session.add(
            Worktree(
                workspace_id=ws.id,
                agent_run_id=run.id,
                worktree_path=str(linked),
                state=WorktreeState.MERGED,
            )
        )
        session.commit()
        plan = cleanup.preview(session, [str(linked)])
        with mock.patch.object(cleanup, "send2trash", side_effect=_fake_trash(tmp_path / "trash")):
            result = cleanup.execute(session, plan.plan_id, [str(linked)])
        row = session.exec(select(Worktree)).one()

    assert result.trashed_count == 1, result
    assert str(linked) not in _git(repo, "worktree", "list")
    assert "feature" in _git(repo, "branch", "--list", "feature")
    assert row.state is WorktreeState.CLEANUP


def test_selecting_a_folder_and_its_child_previews_the_folder_once(
    isolated_db, repo, real_context
) -> None:
    _write(repo / "node_modules" / "pkg" / "x.js")
    with Session(isolated_db) as session:
        plan = cleanup.preview(
            session, [str(repo / "node_modules" / "pkg"), str(repo / "node_modules")]
        )
    assert [item.verdict.path for item in plan.items] == [str(repo / "node_modules")]
    assert plan.covered == [str(repo / "node_modules" / "pkg")]


def test_a_path_inside_a_refused_folder_is_judged_on_its_own(
    isolated_db, repo, real_context
) -> None:
    _write(repo / "node_modules" / "x.js")
    _write(repo / "notes" / "draft.md")
    with Session(isolated_db) as session:
        plan = cleanup.preview(session, [str(repo / "node_modules"), str(repo)])
    by_path = {item.verdict.path: item.verdict.allowed for item in plan.items}
    assert by_path == {str(repo): False, str(repo / "node_modules"): True}
    assert plan.covered == []


# --- store ---------------------------------------------------------------------


def test_a_corrupt_stored_scan_raises_rather_than_reading_as_never_scanned(scan_file) -> None:
    scan_file.write_text("{not json")
    with pytest.raises(ValueError):
        store.latest_result()


# --- MCP tool --------------------------------------------------------------------


def test_the_tool_is_read_only_and_returns_names_and_sizes_only(isolated_db, tmp_path) -> None:
    root = tmp_path / "root"
    secret = _write(root / "secret.txt", 2 * MB)
    tree = _scan(root)
    from datetime import UTC, datetime  # noqa: PLC0415 - test-local fixture data

    now = datetime.now(UTC)
    store._write_result(
        store.ScanResult(started_at=now, finished_at=now, roots=[], tree=[tree], entries=1)
    )

    with Session(isolated_db) as session:
        answer = json.loads(disk_usage_tool(session, {}))

    assert McpTool.DISK_USAGE in READ_ONLY_MCP_TOOLS
    assert answer["tree"][0]["children"][0]["name"] == "secret.txt"
    assert secret.read_bytes().decode("latin-1") not in json.dumps(answer)


def test_the_tool_rejects_unknown_arguments(isolated_db) -> None:
    with Session(isolated_db) as session, pytest.raises(ValueError, match="invalid arguments"):
        disk_usage_tool(session, {"delete": "/"})


# --- API -------------------------------------------------------------------------


def test_overview_before_any_scan_says_never_scanned(client) -> None:
    body = client.get("/api/machine/disk").json()
    assert body["scan"] is None
    assert body["status"]["state"] == "idle"
    assert body["volume"]["total_bytes"] > 0


def test_tree_without_a_scan_is_a_404_not_an_empty_list(client) -> None:
    assert client.get("/api/machine/disk/tree").status_code == 404


def test_added_folders_must_be_existing_absolute_folders(client, tmp_path) -> None:
    assert client.post("/api/machine/disk/roots", json={"path": "relative/dir"}).status_code == 400
    assert client.post("/api/machine/disk/roots", json={"path": "/"}).status_code == 400
    assert (
        client.post("/api/machine/disk/roots", json={"path": str(tmp_path / "nope")}).status_code
        == 400
    )
    added = client.post("/api/machine/disk/roots", json={"path": str(tmp_path)})
    assert added.status_code == 201
    assert added.json() == [str(tmp_path.resolve())]
    assert (
        client.delete("/api/machine/disk/roots", params={"path": str(tmp_path.resolve())}).json()
        == []
    )


def test_execute_with_an_unknown_plan_is_a_conflict(client) -> None:
    response = client.post(
        "/api/machine/disk/cleanup/execute", json={"plan_id": "nope", "paths": ["/x"]}
    )
    assert response.status_code == 409
