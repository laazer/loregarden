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
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True
    )
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


def test_head_commit_sha_on_a_missing_directory_is_empty_not_an_error(tmp_path):
    """It documents "" for anything that is not a repo, and a path that does not
    exist is the commonest case of that — a workspace whose repo is not mounted
    on this machine. It used to inherit subprocess's exception for the missing
    cwd, so merely asking which commit was current could raise."""
    assert git_commit_push_service.head_commit_sha(tmp_path / "never-existed") == ""


@pytest.fixture
def repo_with_staged_deletion(tmp_path: Path) -> Path:
    """A repo holding a tracked file whose deletion is ALREADY staged.

    That state is what `git add -- <path>` cannot express: the path is in
    neither the index nor the worktree, so the pathspec matches nothing and git
    exits 128. It is reached in the wild by an agent committing a stray file and
    a later step removing it with `git rm` (lg-milestone-that-2599's `p`).
    """
    repo = tmp_path / "staged-deletion"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", ".")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "p").write_text("", encoding="utf-8")
    (repo / "keep.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "rm", "-q", "p")
    assert _git(repo, "status", "--porcelain").stdout.strip() == "D  p"
    return repo


def test_commit_paths_in_commits_a_path_whose_deletion_is_already_staged(
    repo_with_staged_deletion: Path,
):
    """`git add -- p` exits 128 here, which used to fail the whole commit and
    block every later stage of the ticket. The deletion is already in the
    index, so the commit is what the path needs, not another add."""
    assert git_commit_push_service.commit_paths_in(repo_with_staged_deletion, "drop p", ["p"])

    assert _git(repo_with_staged_deletion, "status", "--porcelain").stdout.strip() == ""
    tracked = _git(repo_with_staged_deletion, "ls-files").stdout.split()
    assert "p" not in tracked


def test_commit_all_dirty_in_mixes_a_staged_deletion_with_an_unstaged_edit(
    repo_with_staged_deletion: Path,
):
    """The add still has to run for the unstaged half; only the already-staged
    path is left out of the pathspec."""
    (repo_with_staged_deletion / "keep.txt").write_text("two\n", encoding="utf-8")

    assert git_commit_push_service.commit_all_dirty_in(repo_with_staged_deletion, "both halves")

    assert _git(repo_with_staged_deletion, "status", "--porcelain").stdout.strip() == ""
    landed = _git(repo_with_staged_deletion, "show", "--name-status", "--format=", "HEAD").stdout
    assert "D\tp" in landed
    assert "M\tkeep.txt" in landed


def test_a_pathspec_that_matches_nothing_is_still_an_error(repo_with_staged_deletion: Path):
    """The exemption is for paths git status reported as staged, not a licence
    to swallow a failing add: a path that is dirty but unmatchable must still
    raise rather than commit something else under its message."""
    with pytest.raises(ValueError, match="did not match any files"):
        git_commit_push_service._add_and_commit(
            repo_with_staged_deletion, "bad", {"never-existed"}, frozenset()
        )
