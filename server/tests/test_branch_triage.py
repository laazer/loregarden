"""Tests for branch triage snapshot and diff review."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.executors.permission_bridge import (
    BRANCH_TRIAGE_STAGE_KEY,
    BridgeResult,
)
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    BranchTriageMessage,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.branch_triage_chat_service import invoke_branch_triage_model
from loregarden.services.branch_triage_run_service import fail_interrupted_branch_triage_turns
from loregarden.services.branch_triage_service import (
    PR_STATUS_TERMINAL_TTL_SECONDS,
    PR_STATUS_TTL_SECONDS,
    _pr_status_ttl,
    branch_activity,
    branch_triage_snapshot,
    commit_snapshot,
    delete_branch,
    remove_branch_worktree,
)
from loregarden.services.file_editor import _list_branches
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def triage_repo(tmp_path, monkeypatch):
    repo = tmp_path / "loregarden"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "branch", "loregarden/orphan"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "loregarden/orphan"], cwd=repo, check=True, capture_output=True
    )
    (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
    subprocess.run(["git", "add", "orphan.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "orphan work"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo.resolve())
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    return repo


@pytest.fixture
def triage_session(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture
def triage_workspace(triage_repo, triage_session: Session) -> Workspace:
    ws = Workspace(name="Demo", slug="demo", repo_path=".")
    triage_session.add(ws)
    triage_session.commit()
    triage_session.refresh(ws)
    return ws


def test_branch_triage_flags_orphan_branch(triage_workspace, triage_session: Session):
    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    orphan = next(b for b in snapshot["branches"] if b["name"] == "loregarden/orphan")
    codes = {issue["code"] for issue in orphan["issues"]}
    assert "no_ticket" in codes
    assert orphan["ahead"] >= 1


def test_branch_triage_links_ticket(triage_workspace, triage_session: Session):
    ticket = Ticket(
        external_id="TK-orphan",
        workspace_id=triage_workspace.id,
        title="Orphan branch ticket",
        branch="loregarden/orphan",
    )
    triage_session.add(ticket)
    triage_session.commit()

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    orphan = next(b for b in snapshot["branches"] if b["name"] == "loregarden/orphan")
    assert len(orphan["linked_tickets"]) == 1
    assert orphan["linked_tickets"][0]["external_id"] == "TK-orphan"


def test_branch_triage_treats_squash_merged_branch_as_not_ahead(
    triage_workspace, triage_repo, triage_session: Session
):
    subprocess.run(
        ["git", "checkout", "-b", "feature/squashed"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "squash.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "squash.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "wip 1"], cwd=triage_repo, check=True, capture_output=True
    )
    (triage_repo / "squash.txt").write_text("v2\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "wip 2"], cwd=triage_repo, check=True, capture_output=True
    )

    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "merge", "--squash", "feature/squashed"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "feature/squashed (squashed)"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    squashed = next(b for b in snapshot["branches"] if b["name"] == "feature/squashed")
    assert squashed["ahead"] == 0
    codes = {issue["code"] for issue in squashed["issues"]}
    assert "diverged" not in codes


def test_branch_triage_includes_pr_status_and_caches(
    triage_workspace, triage_repo, triage_session: Session, monkeypatch
):
    subprocess.run(
        ["git", "branch", "feature/has-pr"], cwd=triage_repo, check=True, capture_output=True
    )

    call_count = {"n": 0}
    pr_payload = {
        "state": "open",
        "is_draft": False,
        "url": "https://github.com/example/repo/pull/7",
        "number": 7,
        "title": "Add feature",
    }

    def fake_fetch(repo_root, branch):
        call_count["n"] += 1
        return pr_payload if branch == "feature/has-pr" else None

    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._fetch_pr_status_live", fake_fetch
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    with_pr = next(b for b in snapshot["branches"] if b["name"] == "feature/has-pr")
    assert with_pr["pr"] == pr_payload
    without_pr = next(b for b in snapshot["branches"] if b["name"] == "main")
    assert without_pr["pr"] is None

    calls_after_first = call_count["n"]
    assert calls_after_first > 0

    branch_triage_snapshot(triage_session, triage_workspace)
    assert call_count["n"] == calls_after_first


def test_branch_triage_reads_pr_status_from_a_single_gh_listing(
    triage_workspace, triage_repo, triage_session: Session, monkeypatch
):
    subprocess.run(
        ["git", "branch", "feature/listed-pr"], cwd=triage_repo, check=True, capture_output=True
    )

    listing = {
        "feature/listed-pr": {
            "state": "open",
            "is_draft": False,
            "url": "https://github.com/example/repo/pull/9",
            "number": 9,
            "title": "Listed",
        }
    }

    def fail_if_called(repo_root, branch):  # pragma: no cover - asserts it is never reached
        raise AssertionError(f"per-branch gh lookup for {branch} should not run")

    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._fetch_pr_list",
        lambda repo_root: (listing, True),
    )
    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._fetch_pr_status_live", fail_if_called
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    listed = next(b for b in snapshot["branches"] if b["name"] == "feature/listed-pr")
    assert listed["pr"] == listing["feature/listed-pr"]
    other = next(b for b in snapshot["branches"] if b["name"] == "loregarden/orphan")
    assert other["pr"] is None


def test_branch_triage_falls_back_to_per_branch_gh_when_the_listing_is_truncated(
    triage_workspace, triage_repo, triage_session: Session, monkeypatch
):
    subprocess.run(
        ["git", "branch", "feature/beyond-page"], cwd=triage_repo, check=True, capture_output=True
    )
    pr_payload = {
        "state": "merged",
        "is_draft": False,
        "url": "https://github.com/example/repo/pull/1",
        "number": 1,
        "title": "Old",
    }

    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._fetch_pr_list", lambda repo_root: ({}, False)
    )
    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._fetch_pr_status_live",
        lambda repo_root, branch: pr_payload if branch == "feature/beyond-page" else None,
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    beyond = next(b for b in snapshot["branches"] if b["name"] == "feature/beyond-page")
    assert beyond["pr"] == pr_payload


def test_branch_triage_matches_the_batched_scan_when_for_each_ref_is_unavailable(
    triage_workspace, triage_repo, triage_session: Session, monkeypatch
):
    subprocess.run(
        ["git", "branch", "feature/fallback"], cwd=triage_repo, check=True, capture_output=True
    )
    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._fetch_pr_list", lambda repo_root: ({}, True)
    )

    batched = branch_triage_snapshot(triage_session, triage_workspace)
    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._branch_refs_batch",
        lambda repo_root, base, remote_names: None,
    )
    per_branch = branch_triage_snapshot(triage_session, triage_workspace)

    assert per_branch == batched


def test_pr_status_ttl_is_longer_for_closed_and_merged_prs():
    assert _pr_status_ttl(None) == PR_STATUS_TTL_SECONDS
    assert _pr_status_ttl({"state": "open", "is_draft": False}) == PR_STATUS_TTL_SECONDS
    assert _pr_status_ttl({"state": "closed", "is_draft": False}) == PR_STATUS_TERMINAL_TTL_SECONDS
    assert _pr_status_ttl({"state": "merged", "is_draft": False}) == PR_STATUS_TERMINAL_TTL_SECONDS
    assert PR_STATUS_TERMINAL_TTL_SECONDS > PR_STATUS_TTL_SECONDS


def test_delete_unmerged_branch_requires_force(triage_workspace, triage_repo):
    subprocess.run(
        ["git", "checkout", "-b", "feature/unmerged"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "unmerged.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "unmerged.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "unmerged"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    with pytest.raises(ValueError, match="not fully merged"):
        delete_branch(triage_workspace, "feature/unmerged", force=False)

    delete_branch(triage_workspace, "feature/unmerged", force=True)
    branch_names = _list_branches(resolve_workspace_root(triage_workspace))
    assert "feature/unmerged" not in branch_names


def test_delete_branch_is_idempotent_when_already_gone(triage_workspace, triage_repo):
    subprocess.run(
        ["git", "branch", "feature/gone"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-D", "feature/gone"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    assert delete_branch(triage_workspace, "feature/gone", force=True) is False


def test_delete_branch_api_already_gone(client: TestClient, triage_workspace):
    res = client.post(
        f"/api/workspaces/{triage_workspace.slug}/branch-triage/delete",
        params={"branch": "feature/missing"},
        json={"force": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == "feature/missing"
    assert body["already_gone"] is True


def test_delete_branch_blocked_by_worktree(triage_workspace, triage_repo, tmp_path):
    subprocess.run(
        ["git", "branch", "feature/worktree"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    wt_path = tmp_path / "wt-feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "feature/worktree"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    with pytest.raises(ValueError, match="worktree"):
        delete_branch(triage_workspace, "feature/worktree", force=True)

    assert (
        delete_branch(
            triage_workspace,
            "feature/worktree",
            force=True,
            remove_worktrees=True,
        )
        is True
    )
    branch_names = _list_branches(resolve_workspace_root(triage_workspace))
    assert "feature/worktree" not in branch_names


def test_remove_branch_worktree_leaves_branch_intact(
    triage_workspace, triage_repo, triage_session: Session, tmp_path
):
    subprocess.run(
        ["git", "branch", "feature/wt-cleanup"], cwd=triage_repo, check=True, capture_output=True
    )
    wt_path = tmp_path / "wt-feature-cleanup"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "feature/wt-cleanup"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    branch = next(b for b in snapshot["branches"] if b["name"] == "feature/wt-cleanup")
    assert len(branch["worktrees"]) == 1
    assert branch["worktrees"][0]["is_primary"] is False

    remove_branch_worktree(triage_workspace, "feature/wt-cleanup", str(wt_path))

    branch_names = _list_branches(resolve_workspace_root(triage_workspace))
    assert "feature/wt-cleanup" in branch_names
    assert not wt_path.exists()


def test_remove_branch_worktree_rejects_unknown_path(triage_workspace, triage_repo):
    subprocess.run(
        ["git", "branch", "feature/no-worktree"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="No worktree"):
        remove_branch_worktree(triage_workspace, "feature/no-worktree", "/tmp/not-a-worktree")


def test_remove_branch_worktree_rejects_primary_checkout(triage_workspace, triage_repo):
    with pytest.raises(ValueError, match="primary repository checkout"):
        remove_branch_worktree(triage_workspace, "main", str(triage_repo.resolve()))


def test_remove_worktree_api(client: TestClient, triage_repo, db_session: Session, tmp_path):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    # The client fixture repoints the seeded workspace at its own throwaway repo;
    # restore the relative repo_path so it resolves against triage_repo instead.
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    subprocess.run(
        ["git", "branch", "feature/wt-api"], cwd=triage_repo, check=True, capture_output=True
    )
    wt_path = tmp_path / "wt-feature-api"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "feature/wt-api"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    res = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/worktrees/remove",
        params={"branch": "feature/wt-api"},
        json={"path": str(wt_path)},
    )
    assert res.status_code == 200
    assert res.json() == {"branch": "feature/wt-api", "removed_path": str(wt_path)}
    assert not wt_path.exists()

    bad = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/worktrees/remove",
        params={"branch": "feature/wt-api"},
        json={"path": str(wt_path)},
    )
    assert bad.status_code == 400


def test_branch_diff_endpoint(client: TestClient, triage_repo, db_session: Session):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    # The client fixture repoints the seeded workspace at its own throwaway repo;
    # restore the relative repo_path so it resolves against triage_repo instead.
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "main"},
    )
    assert res.status_code == 404

    subprocess.run(
        ["git", "branch", "feature/triage"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "feature/triage"], cwd=triage_repo, check=True, capture_output=True
    )
    (triage_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feature work"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/triage"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["branch"] == "feature/triage"
    diff = body["diff"]
    assert diff["file_entries"]
    assert diff["file_entries"][0]["path"] == "feature.txt"
    assert diff["sections"] == []

    file_res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/triage", "file": "feature.txt"},
    )
    assert file_res.status_code == 200
    file_diff = file_res.json()["diff"]
    assert file_diff["sections"]
    assert file_diff["sections"][0]["path"] == "feature.txt"
    assert file_diff["sections"][0]["lines"]


def test_branch_diff_remote_and_working_tree_modes(
    client: TestClient, triage_repo, triage_workspace, triage_session: Session
):
    ws = triage_workspace

    subprocess.run(
        ["git", "checkout", "-b", "feature/remote"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "remote.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "remote.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=triage_repo, check=True, capture_output=True)
    v1_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature/remote", v1_sha],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "remote.txt").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "v2"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    remote = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/remote", "mode": "remote"},
    )
    assert remote.status_code == 200
    assert remote.json()["mode"] == "remote"
    remote_diff = remote.json()["diff"]
    assert remote_diff["file_entries"]
    assert remote_diff["sections"] == []

    remote_file = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/remote", "mode": "remote", "file": "remote.txt"},
    )
    assert remote_file.status_code == 200
    assert remote_file.json()["diff"]["sections"]

    (triage_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    unstaged = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "main", "mode": "unstaged"},
    )
    assert unstaged.status_code == 200
    body = unstaged.json()["diff"]
    assert body["range"] == "unstaged changes"
    assert body["file_entries"]
    assert body["sections"] == []

    unstaged_file = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "main", "mode": "unstaged", "file": "dirty.txt"},
    )
    assert unstaged_file.status_code == 200
    file_body = unstaged_file.json()["diff"]
    assert file_body["sections"]
    assert file_body["sections"][0]["lines"][0]["type"] == "a"

    snapshot = branch_triage_snapshot(triage_session, ws)
    main = next(item for item in snapshot["branches"] if item["name"] == "main")
    remote_branch = next(item for item in snapshot["branches"] if item["name"] == "feature/remote")
    assert main["is_current"] is True
    assert any(option["mode"] == "unstaged" for option in main["diff_options"])
    assert any(option["mode"] == "remote" for option in remote_branch["diff_options"])
    assert not any(option["mode"] == "unstaged" for option in remote_branch["diff_options"])


def test_branch_diff_comments_and_submit(
    client: TestClient, triage_repo, db_session: Session, monkeypatch
):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    subprocess.run(
        ["git", "branch", "feature/review"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "feature/review"], cwd=triage_repo, check=True, capture_output=True
    )
    (triage_repo / "review.txt").write_text("review\n", encoding="utf-8")
    subprocess.run(["git", "add", "review.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "review work"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    ticket = Ticket(
        external_id="TK-review",
        workspace_id=ws.id,
        title="Review branch ticket",
        branch="feature/review",
    )
    db_session.add(ticket)
    db_session.commit()

    branch = "feature/review"
    created = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/diff-comments",
        params={"branch": branch},
        json={
            "file_path": "review.txt",
            "line_index": 0,
            "line_kind": "a",
            "content": "Drop this file before merge",
        },
    )
    assert created.status_code == 200

    listed = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff-comments",
        params={"branch": branch},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Acknowledged.")
    submitted = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/diff-comments/submit-to-agent",
        params={"branch": branch},
        json={"instructions": "Please clean up"},
    )
    assert submitted.status_code == 200
    payload = submitted.json()
    assert payload["ticket_id"] == ticket.id
    assert payload["submitted_comments"] == 1


def test_branch_chat_messages(client: TestClient, triage_repo, db_session: Session, monkeypatch):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    subprocess.run(
        ["git", "branch", "feature/chat"], cwd=triage_repo, check=True, capture_output=True
    )

    empty = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/chat"},
    )
    assert empty.status_code == 200
    assert empty.json()["messages"] == []

    assert empty.json()["run_status"] == "idle"

    monkeypatch.setenv(
        "LOREGARDEN_TRIAGE_STUB_RESPONSE",
        "Checkout main and delete this branch.\n"
        '```loregarden\n{"primitive":"thinking","content":"weighing it"}\n```\n',
    )
    sent = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/messages",
        params={"branch": "feature/chat"},
        json={"content": "Should I delete this branch?"},
    )
    assert sent.status_code == 202
    payload = sent.json()
    assert "assistant_message" not in payload
    assert payload["status"] == "queued"

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/chat"},
    )
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert len(body["messages"]) == 2
    assert body["run_status"] == "idle"
    assert "Checkout main and delete this branch." in body["messages"][-1]["content"]
    # Parts are stored with the turn, so the card survives a reload.
    assert any(part.get("primitive") == "thinking" for part in body["messages"][-1]["parts"])

    # ...and the turn's run is settled, so its approvals have a closed parent.
    assistant = db_session.get(BranchTriageMessage, body["messages"][-1]["id"])
    assert assistant is not None
    assert assistant.run_id
    run = db_session.get(AgentRun, assistant.run_id)
    assert run is not None
    assert run.status == RunStatus.SUCCEEDED
    assert run.finished_at is not None


def test_branch_chat_send_does_not_run_the_turn_on_the_request_thread(
    client: TestClient, triage_repo, db_session: Session
):
    """The POST hands off and returns: the turn is queued, not executed inline.

    Patching the scheduler proves the request path never invokes the CLI — the
    property that kept the old blocking endpoint hostage to a 600s subprocess.
    """
    subprocess.run(
        ["git", "branch", "feature/async"], cwd=triage_repo, check=True, capture_output=True
    )

    with patch("loregarden.api.branch_triage.schedule_branch_triage_turn") as scheduled:
        sent = client.post(
            "/api/workspaces/loregarden/branch-triage/chat/messages",
            params={"branch": "feature/async"},
            json={"content": "async please"},
        )

    assert sent.status_code == 202
    payload = sent.json()
    assert payload["status"] == "queued"
    assert "assistant_message" not in payload
    scheduled.assert_called_once_with(payload["active_turn_id"])

    # The turn is durably queued, so the UI reports "running" from server state alone.
    body = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/async"},
    ).json()
    assert body["run_status"] == "running"
    assert body["active_turn_id"] == payload["active_turn_id"]
    assert [m["role"] for m in body["messages"]] == ["user"]

    assistant = db_session.get(BranchTriageMessage, payload["active_turn_id"])
    assert assistant is not None
    assert assistant.run_id
    run = db_session.get(AgentRun, assistant.run_id)
    assert run is not None
    assert run.ticket_id is None
    assert run.workspace_id == assistant.workspace_id
    assert run.stage_key == BRANCH_TRIAGE_STAGE_KEY
    assert run.status == RunStatus.QUEUED


def test_branch_chat_uses_permission_bridge_for_checked_out_branch(
    triage_workspace: Workspace,
    triage_repo: Path,
    triage_session: Session,
    monkeypatch,
):
    from loregarden.services import branch_triage_chat_service

    captured: dict[str, object] = {}
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(
        branch_triage_chat_service, "resolve_effective_adapter", lambda **_: "claude"
    )
    monkeypatch.setattr(branch_triage_chat_service, "_branch_entry", lambda *_: None)

    def fake_invocation(**kwargs):
        captured["root"] = kwargs["workspace_root"]
        return MagicMock(
            adapter="claude",
            argv=["claude"],
            cwd=str(kwargs["workspace_root"]),
            resume_session_id="",
        )

    def fake_bridge(self, **kwargs):
        captured["bridge"] = kwargs
        return BridgeResult(
            status=RunStatus.SUCCEEDED,
            stdout='{"type":"result","result":"changed the branch"}',
            stderr="",
        )

    monkeypatch.setattr(branch_triage_chat_service, "build_interactive_invocation", fake_invocation)
    monkeypatch.setattr(branch_triage_chat_service.PermissionBridgeRunner, "run", fake_bridge)

    reply = invoke_branch_triage_model(
        triage_session,
        triage_workspace,
        "main",
        "Fix the branch",
        run_id="run_branch",
    )

    assert reply == "changed the branch"
    assert captured["root"] == triage_repo.resolve()
    bridge_kwargs = captured["bridge"]
    assert bridge_kwargs["workspace"] is triage_workspace
    assert bridge_kwargs["workspace_stage_key"] == BRANCH_TRIAGE_STAGE_KEY
    assert "real file, shell, git" in bridge_kwargs["prompt"]


def test_branch_chat_stays_advisory_without_a_checkout(
    triage_workspace: Workspace,
    triage_session: Session,
    monkeypatch,
):
    from loregarden.services import branch_triage_chat_service

    captured: dict[str, str] = {}
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(
        branch_triage_chat_service, "resolve_effective_adapter", lambda **_: "claude"
    )
    monkeypatch.setattr(branch_triage_chat_service, "_branch_entry", lambda *_: None)

    def fake_one_shot(_profile, *, prompt, **_kwargs):
        captured["prompt"] = prompt
        captured["read_only"] = str(_kwargs.get("read_only"))
        return "check it out first"

    monkeypatch.setattr(branch_triage_chat_service, "run_cli_agent_turn", fake_one_shot)

    reply = invoke_branch_triage_model(
        triage_session,
        triage_workspace,
        "loregarden/orphan",
        "Edit this branch",
        run_id="run_branch",
    )

    assert reply == "check it out first"
    assert "advisory only" in captured["prompt"]
    assert "not checked out in a worktree" in captured["prompt"]
    assert captured["read_only"] == "True"


def test_branch_chat_rejects_a_second_turn_while_one_is_in_flight(
    client: TestClient, triage_repo, db_session: Session
):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    subprocess.run(
        ["git", "branch", "feature/busy"], cwd=triage_repo, check=True, capture_output=True
    )
    db_session.add(
        BranchTriageMessage(
            workspace_id=ws.id,
            branch="feature/busy",
            role="assistant",
            content="",
            status="pending",
        )
    )
    db_session.commit()

    res = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/messages",
        params={"branch": "feature/busy"},
        json={"content": "second message"},
    )
    assert res.status_code == 409

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/busy"},
    ).json()
    assert snapshot["run_status"] == "running"
    # A pending turn has no content yet, so it must not surface as an empty reply.
    assert snapshot["messages"] == []


def test_interrupted_branch_chat_turn_is_settled_so_the_composer_recovers(
    client: TestClient, triage_repo, db_session: Session
):
    """A restart mid-turn must not leave the branch stuck 'running' forever."""
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    subprocess.run(
        ["git", "branch", "feature/orphan"], cwd=triage_repo, check=True, capture_output=True
    )
    db_session.add(
        BranchTriageMessage(
            workspace_id=ws.id,
            branch="feature/orphan",
            role="assistant",
            content="",
            status="pending",
        )
    )
    db_session.commit()

    settled = fail_interrupted_branch_triage_turns(db_session)
    assert len(settled) == 1

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/orphan"},
    ).json()
    assert snapshot["run_status"] == "idle"
    assert len(snapshot["messages"]) == 1


def test_branch_activity_marks_unpushed_commits_as_local(triage_workspace, triage_repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"], cwd=triage_repo, check=True, capture_output=True
    )

    (triage_repo / "local.txt").write_text("local\n", encoding="utf-8")
    subprocess.run(["git", "add", "local.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "local only"], cwd=triage_repo, check=True, capture_output=True
    )

    activity = branch_activity(triage_workspace, "main")

    assert activity["branch"] == "main"
    assert activity["upstream"]
    assert [commit["message"] for commit in activity["commits"]] == ["local only", "init"]
    pushed_by_message = {c["message"]: c["pushed"] for c in activity["commits"]}
    assert pushed_by_message["local only"] is False
    assert pushed_by_message["init"] is True


def test_branch_activity_without_upstream_reports_nothing_pushed(triage_workspace):
    activity = branch_activity(triage_workspace, "loregarden/orphan")

    assert activity["upstream"] is None
    assert activity["commits"]
    assert all(commit["pushed"] is False for commit in activity["commits"])


def test_commit_snapshot_reads_metadata_and_stats(triage_workspace):
    commit = commit_snapshot(triage_workspace, "HEAD")

    assert commit["message"] == "init"
    assert commit["short_sha"]
    assert commit["author"]
    assert commit["files_changed"] == 1
    assert commit["insertions"] == 1
    assert commit["deletions"] == 0
    assert commit["pushed"] is False


def test_commit_snapshot_rejects_arbitrary_revision(triage_workspace):
    with pytest.raises(ValueError, match="Commit ref"):
        commit_snapshot(triage_workspace, "--all")


def test_branch_activity_endpoint_honours_limit(
    client: TestClient, triage_repo, db_session: Session
):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    for index in range(3):
        (triage_repo / f"f{index}.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=triage_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"commit {index}"],
            cwd=triage_repo,
            check=True,
            capture_output=True,
        )

    res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/activity",
        params={"branch": "main", "limit": 2},
    )
    assert res.status_code == 200
    body = res.json()
    assert [c["message"] for c in body["commits"]] == ["commit 2", "commit 1"]
    assert all(c["short_sha"] and c["author"] for c in body["commits"])
