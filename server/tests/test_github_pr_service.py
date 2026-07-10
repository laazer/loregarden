"""Edge-case coverage for create_ticket_pull_request.

Real `git` runs against a disposable repo + bare "origin" remote (both under tmp_path) so
push/diff behavior is exercised for real. Only `gh` (which would need real network + GitHub
auth) is faked, dispatched per-test via a small in-process router.
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket, Workspace
from loregarden.services import github_pr_service
from sqlmodel import Session, select


def _create_ticket(client: TestClient, **overrides) -> dict:
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    body = {
        "workspace_slug": "loregarden",
        "title": "PR service edge case",
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


def _make_branch_with_commit(repo: Path, branch: str, filename: str = "feature.txt") -> None:
    _git(repo, "checkout", "-b", branch)
    (repo / filename).write_text(f"content for {branch}\n", encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"work on {branch}")
    _git(repo, "checkout", "main")


def _set_ticket_branch(isolated_db, ticket_id: str, branch: str) -> Ticket:
    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.branch = branch
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        return ticket


def _mock_gh(monkeypatch, router):
    """router: callable(argv: list[str]) -> subprocess.CompletedProcess for `gh ...` calls.

    `git` calls pass through to the real subprocess unmodified.
    """
    real_run = subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv[0] == "gh":
            return router(argv)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(github_pr_service.subprocess, "run", fake_run)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_no_diff_from_base_raises_clear_error(client: TestClient, isolated_db, tmp_path):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    # Branch left pointing at the same commit as main — nothing to PR.
    _git(repo, "branch", "empty-branch")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "empty-branch")

    with Session(isolated_db) as session:
        with pytest.raises(ValueError, match="no commits ahead"):
            github_pr_service.create_ticket_pull_request(session, ticket)


def test_unpushed_branch_is_pushed_automatically(client: TestClient, isolated_db, tmp_path, monkeypatch):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    bare = _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/unpushed")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/unpushed")

    _mock_gh(
        monkeypatch,
        lambda argv: _completed(stdout="https://github.com/acme/repo/pull/1")
        if argv[1:3] == ["pr", "create"]
        else _completed(stdout="[]")
        if argv[1:3] == ["pr", "list"]
        else _completed(stdout='{"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN", "state": "OPEN"}')
        if argv[1:3] == ["pr", "view"]
        else _completed(returncode=1, stderr="not mergeable yet"),
    )

    with Session(isolated_db) as session:
        result = github_pr_service.create_ticket_pull_request(session, ticket)

    assert result["created"] is True
    assert result["number"] == "1"
    # The branch must now actually exist on the bare "origin" remote.
    remote_branches = subprocess.run(
        ["git", "branch", "-r"], cwd=bare, capture_output=True, text=True
    ).stdout
    assert "feature/unpushed" not in remote_branches  # bare repos don't show remote-tracking refs of themselves
    heads = subprocess.run(["git", "show-ref"], cwd=bare, capture_output=True, text=True).stdout
    assert "refs/heads/feature/unpushed" in heads


def test_existing_open_pr_is_reused_not_recreated(client: TestClient, isolated_db, tmp_path, monkeypatch):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/already-open")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/already-open")
    _git(repo, "push", "-u", "origin", "feature/already-open")

    calls = []

    def router(argv):
        calls.append(argv)
        if argv[1:3] == ["pr", "list"]:
            return _completed(
                stdout='[{"number": 42, "url": "https://github.com/acme/repo/pull/42", '
                '"title": "existing", "state": "OPEN", "body": "existing body"}]'
            )
        if argv[1:3] == ["pr", "create"]:
            raise AssertionError("should not create a new PR when one already exists")
        if argv[1:3] == ["pr", "view"]:
            return _completed(stdout='{"mergeable": "CONFLICTING", "state": "OPEN"}')
        return _completed(returncode=1, stderr="unexpected gh call")

    _mock_gh(monkeypatch, router)

    with Session(isolated_db) as session:
        result = github_pr_service.create_ticket_pull_request(session, ticket)

    assert result["created"] is False
    assert result["number"] == "42"
    assert result["needs_triage"] is True


def test_conflicting_pr_marked_needs_triage_not_error(client: TestClient, isolated_db, tmp_path, monkeypatch):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/conflict")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/conflict")

    def router(argv):
        if argv[1:3] == ["pr", "list"]:
            return _completed(stdout="[]")
        if argv[1:3] == ["pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/7")
        if argv[1:3] == ["pr", "view"]:
            return _completed(stdout='{"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY", "state": "OPEN"}')
        raise AssertionError(f"should not attempt gh pr merge when conflicting: {argv}")

    _mock_gh(monkeypatch, router)

    with Session(isolated_db) as session:
        result = github_pr_service.create_ticket_pull_request(session, ticket)

    assert result["merged"] is False
    assert result["needs_triage"] is True
    assert "conflict" in (result["reason"] or "").lower()


def test_merge_command_failure_marked_needs_triage_with_reason(
    client: TestClient, isolated_db, tmp_path, monkeypatch
):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/blocked-checks")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/blocked-checks")

    def router(argv):
        if argv[1:3] == ["pr", "list"]:
            return _completed(stdout="[]")
        if argv[1:3] == ["pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/9")
        if argv[1:3] == ["pr", "view"]:
            return _completed(stdout='{"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "state": "OPEN"}')
        if argv[1:3] == ["pr", "merge"]:
            return _completed(returncode=1, stderr="required status check \"ci\" is failing")
        raise AssertionError(f"unexpected gh call: {argv}")

    _mock_gh(monkeypatch, router)

    with Session(isolated_db) as session:
        result = github_pr_service.create_ticket_pull_request(session, ticket)

    assert result["merged"] is False
    assert result["needs_triage"] is True
    assert "ci" in result["reason"]


def test_successful_merge_marks_merged(client: TestClient, isolated_db, tmp_path, monkeypatch):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/clean")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/clean")

    def router(argv):
        if argv[1:3] == ["pr", "list"]:
            return _completed(stdout="[]")
        if argv[1:3] == ["pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/11")
        if argv[1:3] == ["pr", "view"]:
            return _completed(stdout='{"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "state": "OPEN"}')
        if argv[1:3] == ["pr", "merge"]:
            return _completed(returncode=0, stdout="Squashed and merged")
        raise AssertionError(f"unexpected gh call: {argv}")

    _mock_gh(monkeypatch, router)

    with Session(isolated_db) as session:
        result = github_pr_service.create_ticket_pull_request(session, ticket)

    assert result["merged"] is True
    assert result["needs_triage"] is False


def test_already_merged_pr_is_left_alone(client: TestClient, isolated_db, tmp_path, monkeypatch):
    ticket = _create_ticket(client)
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/done")
    ticket = _set_ticket_branch(isolated_db, ticket["id"], "feature/done")

    def router(argv):
        if argv[1:3] == ["pr", "list"]:
            return _completed(
                stdout='[{"number": 3, "url": "https://github.com/acme/repo/pull/3", '
                '"title": "done", "state": "MERGED", "body": ""}]'
            )
        raise AssertionError(f"should not touch gh pr view/create/merge for an already-merged PR: {argv}")

    _mock_gh(monkeypatch, router)

    with Session(isolated_db) as session:
        result = github_pr_service.create_ticket_pull_request(session, ticket)

    assert result["merged"] is True
    assert result["needs_triage"] is False


def test_repeated_calls_update_single_artifact_not_accumulate(
    client: TestClient, isolated_db, tmp_path, monkeypatch
):
    ticket_id = _create_ticket(client)["id"]
    repo = _seeded_repo_root(isolated_db)
    _add_bare_origin(repo, tmp_path)
    _make_branch_with_commit(repo, "feature/retry")
    _set_ticket_branch(isolated_db, ticket_id, "feature/retry")

    attempt = {"n": 0}

    def router(argv):
        if argv[1:3] == ["pr", "list"]:
            if attempt["n"] == 0:
                return _completed(stdout="[]")
            return _completed(
                stdout='[{"number": 5, "url": "https://github.com/acme/repo/pull/5", '
                '"title": "retry", "state": "OPEN", "body": ""}]'
            )
        if argv[1:3] == ["pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/5")
        if argv[1:3] == ["pr", "view"]:
            return _completed(stdout='{"mergeable": "CONFLICTING", "state": "OPEN"}')
        raise AssertionError(f"unexpected gh call: {argv}")

    _mock_gh(monkeypatch, router)

    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        github_pr_service.create_ticket_pull_request(session, ticket)
    attempt["n"] = 1
    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        github_pr_service.create_ticket_pull_request(session, ticket)

    res = client.get(f"/api/tickets/{ticket_id}")
    assert res.json()["artifacts"]["pr"]["number"] == "5"


def test_missing_branch_raises_clear_error(client: TestClient, isolated_db):
    ticket = _create_ticket(client)
    with Session(isolated_db) as session:
        ticket_row = session.get(Ticket, ticket["id"])
        with pytest.raises(ValueError, match="Set a branch"):
            github_pr_service.create_ticket_pull_request(session, ticket_row)
