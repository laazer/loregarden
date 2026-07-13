"""Tests for branch triage snapshot and diff review."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.branch_triage_service import (
    PR_STATUS_TERMINAL_TTL_SECONDS,
    PR_STATUS_TTL_SECONDS,
    _pr_status_ttl,
    branch_triage_snapshot,
    delete_branch,
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

    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Checkout main and delete this branch.")
    sent = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/messages",
        params={"branch": "feature/chat"},
        json={"content": "Should I delete this branch?"},
    )
    assert sent.status_code == 200
    assert "assistant_message" in sent.json()

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/chat"},
    )
    assert snapshot.status_code == 200
    assert len(snapshot.json()["messages"]) == 2
