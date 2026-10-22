"""Branch triage's PR listing: completeness comes from GitHub's page boundary, never a row count."""

import json
import logging
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import PrListingState, Workspace
from loregarden.services import branch_triage_service
from loregarden.services.branch_triage_service import (
    PR_LISTING_TIMEOUT_SECONDS,
    SNAPSHOT_BUDGET_SECONDS,
    _fetch_pr_listing,
    branch_pull_requests,
    branch_triage_snapshot,
)
from sqlmodel import Session, select

RUN_GH = "loregarden.services.github_pr_listing.run_gh"
#: This repository's scale on 2026-10-21, when the fan-out was found.
MEASURED_BRANCH_COUNT = 221


def _pr(number: int, head: str, state: str = "MERGED") -> dict:
    return {
        "number": number,
        "headRefName": head,
        "state": state,
        "isDraft": False,
        "url": f"https://github.com/example/repo/pull/{number}",
        "title": f"PR {number}",
    }


def _pages(prs: list[dict], *, total: int, last_has_next: bool, page_size: int = 100) -> str:
    """What `gh api graphql --paginate --slurp` prints: one envelope per page."""
    chunks = [prs[i : i + page_size] for i in range(0, len(prs), page_size)] or [[]]
    return json.dumps(
        [
            {
                "data": {
                    "repository": {
                        "pullRequests": {
                            "totalCount": total,
                            "pageInfo": {
                                "hasNextPage": last_has_next if i == len(chunks) - 1 else True,
                                "endCursor": f"cursor-{i}",
                            },
                            "nodes": chunk,
                        }
                    }
                }
            }
            for i, chunk in enumerate(chunks)
        ]
    )


def _gh_ok(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=stdout, stderr="")


def _git(repo: Path, *args: str, stdin: str | None = None) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, input=stdin
    )


@pytest.fixture(autouse=True)
def empty_listing_cache():
    branch_triage_service._pr_listing_cache.clear()
    yield
    branch_triage_service._pr_listing_cache.clear()


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(path))
    monkeypatch.setattr(settings, "repo_root", path.resolve())
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    return path


def _add_branches(repo: Path, names: list[str], at: str = "HEAD") -> None:
    """Many branches in one git call; a `git branch` per name would dominate the test."""
    _git(
        repo, "update-ref", "--stdin", stdin="".join(f"create refs/heads/{n} {at}\n" for n in names)
    )


@pytest.fixture
def workspace(repo, isolated_db) -> Workspace:
    with Session(isolated_db) as session:
        ws = Workspace(name="Demo", slug="demo", repo_path=".")
        session.add(ws)
        session.commit()
        session.refresh(ws)
        session.expunge(ws)
        return ws


# --- The boundary the bug lived on ------------------------------------------------


def test_a_full_last_page_with_no_next_page_is_complete(repo):
    """Exactly 500 PRs, and GitHub says there are no more: that is all of them.

    `len(rows) < 500` called this incomplete and sent every branch without a
    PR to its own `gh` call. Reverting to that comparison fails here.
    """
    prs = [_pr(n, f"feature/{n}") for n in range(1, 501)]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=500, last_has_next=False))):
        listing = _fetch_pr_listing(repo)

    assert listing.state is PrListingState.COMPLETE
    assert listing.listed_count == 500


def test_a_full_last_page_with_a_next_page_is_not_complete(repo):
    """Exactly 500 PRs returned, and GitHub says more remain: never 'that is all of them'.

    A comparison like `len(rows) <= limit`, or any inference from the row count
    rather than `hasNextPage`, fails here.
    """
    prs = [_pr(n, f"feature/{n}") for n in range(1, 501)]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=674, last_has_next=True))):
        listing = _fetch_pr_listing(repo)

    assert listing.state is PrListingState.INCOMPLETE
    assert (listing.listed_count, listing.total_count) == (500, 674)
    assert listing.reason


def test_the_listing_asks_for_every_state_not_only_open_prs(repo):
    """A merged or closed PR is what marks a branch done; an open-only query hides it."""
    with patch(RUN_GH, return_value=_gh_ok(_pages([], total=0, last_has_next=False))) as run_gh:
        _fetch_pr_listing(repo)
    (query,) = [arg for arg in run_gh.call_args.args[0] if arg.startswith("query=")]
    assert "pullRequests(first:" in query


def test_an_empty_repository_is_complete_and_not_unavailable(repo):
    with patch(RUN_GH, return_value=_gh_ok(_pages([], total=0, last_has_next=False))):
        listing = _fetch_pr_listing(repo)

    assert listing.state is PrListingState.COMPLETE
    assert listing.by_head == {}


def test_the_open_pr_wins_over_an_older_closed_one_on_the_same_head(repo):
    prs = [_pr(3, "feature/x", "CLOSED"), _pr(9, "feature/x", "OPEN"), _pr(12, "feature/x")]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=3, last_has_next=False))):
        listing = _fetch_pr_listing(repo)

    assert listing.by_head["feature/x"]["number"] == 9
    assert listing.by_head["feature/x"]["state"] == "open"


# --- Could not establish completeness ------------------------------------------------


def test_a_failed_gh_call_is_unavailable_and_says_why(repo, caplog):
    failed = subprocess.CompletedProcess(
        args=["gh"], returncode=1, stdout="", stderr="HTTP 502: Bad Gateway"
    )
    with patch(RUN_GH, return_value=failed), caplog.at_level(logging.WARNING):
        listing = branch_triage_service._pr_listing(repo)

    assert listing.state is PrListingState.UNAVAILABLE
    assert listing.total_count is None
    assert "HTTP 502" in listing.reason
    assert any("unavailable" in record.getMessage() for record in caplog.records)


def test_a_github_timeout_is_unavailable_within_the_stated_budget(repo):
    with patch(
        RUN_GH, side_effect=subprocess.TimeoutExpired("gh", PR_LISTING_TIMEOUT_SECONDS)
    ) as run_gh:
        listing = _fetch_pr_listing(repo)

    assert listing.state is PrListingState.UNAVAILABLE
    assert run_gh.call_args.kwargs["timeout"] == PR_LISTING_TIMEOUT_SECONDS


def test_an_unreadable_response_is_unavailable(repo):
    with patch(RUN_GH, return_value=_gh_ok('{"not": "pages"}')):
        listing = _fetch_pr_listing(repo)

    assert listing.state is PrListingState.UNAVAILABLE


def test_an_incomplete_listing_is_logged(repo, caplog):
    prs = [_pr(n, f"feature/{n}") for n in range(1, 101)]
    with (
        patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=674, last_has_next=True))),
        caplog.at_level(logging.WARNING),
    ):
        branch_triage_service._pr_listing(repo)

    messages = [record.getMessage() for record in caplog.records]
    assert any("incomplete" in m and "100 of 674" in m for m in messages)


# --- Caching -----------------------------------------------------------------------


def test_a_complete_listing_is_cached_and_a_failed_one_is_retried(repo):
    complete = _gh_ok(_pages([_pr(1, "feature/a")], total=1, last_has_next=False))
    with patch(RUN_GH, return_value=complete) as run_gh:
        branch_triage_service._pr_listing(repo)
        branch_triage_service._pr_listing(repo)
    assert run_gh.call_count == 1

    branch_triage_service._pr_listing_cache.clear()
    failed = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="down")
    with patch(RUN_GH, return_value=failed) as run_gh:
        branch_triage_service._pr_listing(repo)
        branch_triage_service._pr_listing(repo)
    assert run_gh.call_count == 2


# --- What the workspace endpoint returns ---------------------------------------------


def test_one_remote_call_however_many_branches_there_are(repo, workspace):
    """The fan-out this listing exists to avoid: a branch with no PR costs no `gh` call."""
    _add_branches(repo, [f"feature/b{i}" for i in range(MEASURED_BRANCH_COUNT)])
    prs = [_pr(1, "feature/b0", "OPEN")]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=1, last_has_next=False))) as run_gh:
        result = branch_pull_requests(workspace)

    assert run_gh.call_count == 1
    assert result["state"] == "complete"
    assert set(result["pull_requests"]) == {"feature/b0"}


def test_only_local_branches_are_returned(repo, workspace):
    _add_branches(repo, ["feature/local"])
    prs = [_pr(1, "feature/local"), _pr(2, "feature/deleted-locally")]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=2, last_has_next=False))):
        result = branch_pull_requests(workspace)

    assert set(result["pull_requests"]) == {"feature/local"}


def test_an_incomplete_listing_reports_its_state_and_counts(repo, workspace):
    _add_branches(repo, ["feature/listed", "feature/beyond-the-page"])
    prs = [_pr(1, "feature/listed")]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=674, last_has_next=True))):
        result = branch_pull_requests(workspace)

    assert result["state"] == "incomplete"
    assert (result["listed_count"], result["total_count"]) == (1, 674)
    # Present where it was read; absent means unknown here, which `state` says.
    assert set(result["pull_requests"]) == {"feature/listed"}


def test_the_endpoint_serves_the_listing(client: TestClient, repo, db_session: Session):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()
    _add_branches(repo, ["feature/api"])

    prs = [_pr(4, "feature/api", "OPEN")]
    with patch(RUN_GH, return_value=_gh_ok(_pages(prs, total=1, last_has_next=False))):
        res = client.get("/api/workspaces/loregarden/branch-triage/pull-requests")

    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "complete"
    assert body["pull_requests"]["feature/api"]["number"] == 4


# --- First paint -------------------------------------------------------------------


def test_the_branch_snapshot_makes_no_remote_call_and_fits_its_budget(repo, workspace, isolated_db):
    """First paint is local git only, at this repository's measured branch count.

    Every branch sits on an unmerged commit, so each one takes the squash-merge
    check, the costliest per-branch step measured on the live repository.
    """
    _git(repo, "checkout", "-b", "seed")
    (repo / "work.txt").write_text("unmerged\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "unmerged work")
    _git(repo, "checkout", "main")
    _add_branches(repo, [f"agent/b{i}" for i in range(MEASURED_BRANCH_COUNT - 1)], at="seed")

    with (
        patch(RUN_GH, side_effect=AssertionError("the snapshot must not call GitHub")),
        Session(isolated_db) as session,
    ):
        started = time.monotonic()
        snapshot = branch_triage_snapshot(session, workspace)
        elapsed = time.monotonic() - started

    assert len(snapshot["branches"]) == MEASURED_BRANCH_COUNT + 1
    assert sum(1 for b in snapshot["branches"] if b["ahead"]) == MEASURED_BRANCH_COUNT
    assert "pr" not in snapshot["branches"][0]
    assert elapsed < SNAPSHOT_BUDGET_SECONDS, f"snapshot took {elapsed:.2f}s"
