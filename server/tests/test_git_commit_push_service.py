"""Coverage for commit_and_push_ticket_branch."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket, Workspace
from loregarden.services import git_commit_push_service
from sqlmodel import Session, select


def _create_ticket(client: TestClient, **overrides) -> dict:
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    body = {
        "workspace_slug": "loregarden",
        "title": "Commit push edge case",
        "work_item_type": "feature",
        "parent_ticket_id": milestone_id,
        **overrides,
    }
    res = client.post("/api/tickets", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _seeded_repo_root(isolated_db) -> Path:
    with Session(isolated_db) as session:
        ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
        assert ws is not None
        return Path(ws.repo_path)


def _add_bare_origin(repo: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    return bare


def _set_ticket_branch(isolated_db, ticket_id: str, branch: str) -> Ticket:
    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.branch = branch
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        return ticket


def test_commits_dirty_worktree_and_pushes_new_branch(client: TestClient, isolated_db, tmp_path):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    bare = _add_bare_origin(repo, tmp_path)
    _git(repo, "checkout", "-b", "feature/dirty")
    (repo / "new-file.txt").write_text("uncommitted work\n", encoding="utf-8")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/dirty")

    with Session(isolated_db) as session:
        result = git_commit_push_service.commit_and_push_ticket_branch(session, ticket)

    assert result == {"branch": "feature/dirty", "committed": True, "pushed": True}
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    heads = subprocess.run(["git", "show-ref"], cwd=bare, capture_output=True, text=True).stdout
    assert "refs/heads/feature/dirty" in heads


def test_nothing_to_commit_raises_dedicated_error(client: TestClient, isolated_db, tmp_path):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _git(repo, "checkout", "-b", "feature/clean")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/clean")

    with Session(isolated_db) as session:
        with pytest.raises(git_commit_push_service.NothingToCommitError):
            git_commit_push_service.commit_and_push_ticket_branch(session, ticket)


def test_endpoint_returns_409_for_nothing_to_commit(client: TestClient, isolated_db, tmp_path):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _git(repo, "checkout", "-b", "feature/clean-endpoint")
    _set_ticket_branch(isolated_db, ticket["id"], "feature/clean-endpoint")

    res = client.post(f"/api/tickets/{ticket['id']}/commit-push")

    assert res.status_code == 409, res.text
    assert "Branch Triage" in res.json()["detail"]


def test_endpoint_commits_and_pushes_dirty_changes(client: TestClient, isolated_db, tmp_path):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    bare = _add_bare_origin(repo, tmp_path)
    _git(repo, "checkout", "-b", "feature/dirty-endpoint")
    (repo / "another.txt").write_text("more work\n", encoding="utf-8")
    _set_ticket_branch(isolated_db, ticket["id"], "feature/dirty-endpoint")

    res = client.post(f"/api/tickets/{ticket['id']}/commit-push")

    assert res.status_code == 200, res.text
    heads = subprocess.run(["git", "show-ref"], cwd=bare, capture_output=True, text=True).stdout
    assert "refs/heads/feature/dirty-endpoint" in heads


def test_missing_ticket_returns_404(client: TestClient):
    res = client.post("/api/tickets/does-not-exist/commit-push")
    assert res.status_code == 404
