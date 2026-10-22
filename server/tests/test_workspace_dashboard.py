"""The workspace dashboard's reads: repo link, open PRs in review order, catch-up digest."""

import json
import subprocess
from datetime import timedelta
from unittest import mock

import pytest
from loregarden.models.domain import (
    ActivityHighlightKind,
    CIStatus,
    DomainEvent,
    EventType,
    PrListingState,
    PullRequestReviewTier,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.github_pr_listing import PR_PAGE_SIZE, GitHubReadError
from loregarden.services.workspace_activity import workspace_activity
from loregarden.services.workspace_pull_requests import workspace_pull_requests
from loregarden.services.workspace_remote import web_url_for, workspace_remote
from sqlmodel import Session

PASSING = {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}
FAILING = {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}


def _pr(
    number: int, *, files: list[str], size: int = 10, checks: list[dict] | None = None, **fields
) -> dict:
    """One node of the GraphQL listing, shaped as GitHub returns it."""
    row = {
        "number": number,
        "url": f"https://github.com/o/r/pull/{number}",
        "title": f"PR {number}",
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "headRefName": f"claude/branch-{number}",
        "additions": size,
        "deletions": 0,
        "changedFiles": len(files),
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "headRefOid": f"sha{number}",
        "commits": {
            "nodes": [
                {"commit": {"statusCheckRollup": {"contexts": {"nodes": checks or [PASSING]}}}}
            ]
        },
        "body": "",
        "files": {"nodes": [{"path": path} for path in files]},
        "createdAt": "2026-10-01T00:00:00Z",
        "updatedAt": "2026-10-02T00:00:00Z",
        "author": {"login": "someone"},
    }
    row.update(fields)
    return row


def _page(rows: list[dict], *, has_next_page: bool = False) -> dict:
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "totalCount": len(rows),
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": "c"},
                    "nodes": rows,
                }
            }
        }
    }


def _gh(
    rows: list[dict] | None = None,
    *,
    pages: list[dict] | None = None,
    returncode: int = 0,
    stderr: str = "",
):
    """`gh api graphql --paginate --slurp`: one array holding every page."""
    stdout = json.dumps(pages if pages is not None else [_page(rows or [])])
    proc = subprocess.CompletedProcess(["gh"], returncode, stdout=stdout, stderr=stderr)
    return mock.patch("loregarden.services.github_pr_listing.run_gh", return_value=proc)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="proj", name="proj", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session, workspace, external_id: str, **fields) -> Ticket:
    ticket = Ticket(external_id=external_id, workspace_id=workspace.id, title=external_id, **fields)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


# --- remote -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote", "web"),
    [
        ("git@github.com:laazer/blobert.git", "https://github.com/laazer/blobert"),
        ("ssh://git@github.com/laazer/blobert.git", "https://github.com/laazer/blobert"),
        ("https://github.com/laazer/blobert.git", "https://github.com/laazer/blobert"),
        ("https://token@github.com/laazer/blobert", "https://github.com/laazer/blobert"),
        ("/srv/git/blobert.git", ""),
    ],
)
def test_web_url_for_reads_ssh_and_https_remotes(remote, web):
    assert web_url_for(remote) == web


def test_remote_links_origin_and_says_why_when_there_is_none(session, workspace, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env={"PATH": "/usr/bin:/bin"})

    missing = workspace_remote(workspace)
    assert (missing.web_url, missing.remote_url) == ("", "")
    assert missing.error  # git's own words for "no such remote"

    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:o/r.git"],
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    found = workspace_remote(workspace)
    assert (found.web_url, found.error) == ("https://github.com/o/r", "")


# --- pull requests ------------------------------------------------------------


def test_review_order_is_tier_then_overlap_then_size(session, workspace):
    rows = [
        _pr(1, files=["a.py"], isDraft=True),
        _pr(2, files=["b.py"], mergeable="CONFLICTING"),
        _pr(3, files=["c.py"], checks=[FAILING]),
        _pr(4, files=["shared.py", "d.py"], size=5),
        _pr(5, files=["shared.py"], size=500),
        _pr(6, files=["e.py"], size=900),
        _pr(7, files=["f.py"], reviewDecision="APPROVED"),
    ]
    with _gh(rows):
        result = workspace_pull_requests(session, workspace)

    order = [(r.pull_request.number, r.tier) for r in result.pull_requests]
    assert order == [
        (7, PullRequestReviewTier.READY_TO_MERGE),
        # No overlaps beats a smaller diff that overlaps another PR.
        (6, PullRequestReviewTier.TO_REVIEW),
        (4, PullRequestReviewTier.TO_REVIEW),
        (5, PullRequestReviewTier.TO_REVIEW),
        (3, PullRequestReviewTier.NEEDS_WORK),
        (2, PullRequestReviewTier.CONFLICTING),
        (1, PullRequestReviewTier.DRAFT),
    ]
    assert [r.review_rank for r in result.pull_requests] == list(range(1, 8))
    by_number = {r.pull_request.number: r for r in result.pull_requests}
    assert [(o.number, o.shared_files) for o in by_number[4].overlaps] == [(5, 1)]
    assert by_number[3].ci is CIStatus.FAILING
    assert by_number[4].pull_request.body == ""
    assert result.listing_state is PrListingState.COMPLETE


@pytest.mark.parametrize(
    ("has_next_page", "expected"),
    [(True, PrListingState.INCOMPLETE), (False, PrListingState.COMPLETE)],
)
def test_a_full_page_is_complete_only_when_github_says_no_page_follows(
    session, workspace, has_next_page, expected
):
    # Exactly a page of rows: the count is the same either way, so only
    # GitHub's `hasNextPage` can tell the two apart.
    rows = [_pr(n, files=[f"{n}.py"]) for n in range(1, PR_PAGE_SIZE + 1)]
    with _gh(pages=[_page(rows, has_next_page=has_next_page)]):
        result = workspace_pull_requests(session, workspace)
    assert len(result.pull_requests) == PR_PAGE_SIZE
    assert result.listing_state is expected


def test_every_page_is_listed_and_the_last_one_decides_completeness(session, workspace):
    first = [_pr(n, files=[f"{n}.py"]) for n in range(1, PR_PAGE_SIZE + 1)]
    pages = [_page(first, has_next_page=True), _page([_pr(101, files=["x.py"])])]
    with _gh(pages=pages):
        result = workspace_pull_requests(session, workspace)
    assert len(result.pull_requests) == PR_PAGE_SIZE + 1
    assert result.listing_state is PrListingState.COMPLETE


def test_a_listing_with_no_repository_raises_rather_than_reporting_no_prs(session, workspace):
    with _gh(pages=[{"data": {"repository": None}}]):
        with pytest.raises(GitHubReadError, match="no repository"):
            workspace_pull_requests(session, workspace)


def test_tickets_link_by_branch_and_by_an_exact_id_mention(session, workspace):
    by_branch = _ticket(session, workspace, "blob-x-7", branch="claude/branch-1")
    named = _ticket(session, workspace, "blob-x-150")
    _ticket(session, workspace, "blob-x-15")  # a prefix of the id named; must not match
    rows = [
        _pr(1, files=["a"]),
        _pr(2, files=["b"], body="Clears the review leftovers of blob-x-150."),
        _pr(3, files=["c"], headRefName="integration/blob-x-7"),
    ]
    with _gh(rows):
        result = workspace_pull_requests(session, workspace)

    linked = {r.pull_request.number: [t.id for t in r.tickets] for r in result.pull_requests}
    assert linked == {1: [by_branch.id], 2: [named.id], 3: [by_branch.id]}


def test_files_truncated_when_github_returned_fewer_files_than_changed(session, workspace):
    with _gh([_pr(1, files=["a"], changedFiles=250)]):
        (row,) = workspace_pull_requests(session, workspace).pull_requests
    assert row.files_truncated is True


def test_gh_failure_raises_rather_than_reporting_no_prs(session, workspace):
    with _gh(returncode=1, stderr="Could not resolve to a Repository"):
        with pytest.raises(GitHubReadError, match="Could not resolve"):
            workspace_pull_requests(session, workspace)


def test_pull_requests_api_maps_gh_failure_to_502(client, isolated_db, tmp_path):
    with Session(isolated_db) as session:
        session.add(Workspace(slug="dash", name="dash", repo_path=str(tmp_path)))
        session.commit()
    with _gh(returncode=1, stderr="HTTP 401"):
        response = client.get("/api/workspaces/dash/pull-requests")
    assert response.status_code == 502
    assert "HTTP 401" in response.json()["detail"]
    with _gh([]):
        assert client.get("/api/workspaces/dash/pull-requests").json()["pull_requests"] == []


# --- activity -----------------------------------------------------------------


def _event(session, workspace, ticket, kind: EventType, payload: dict, *, age: timedelta):
    session.add(
        DomainEvent(
            type=kind,
            workspace_id=workspace.id,
            ticket_id=ticket.id if ticket else None,
            payload_json=json.dumps(payload),
            created_at=utcnow() - age,
        )
    )
    session.commit()


def test_activity_digests_what_changed_per_ticket(session, workspace):
    hour = timedelta(hours=1)
    landed = _ticket(session, workspace, "T-1", state=TicketState.DONE)
    stuck = _ticket(session, workspace, "T-2", state=TicketState.BLOCKED)
    quiet = _ticket(session, workspace, "T-3")
    _event(
        session, workspace, landed, EventType.TICKET_STATE_CHANGED, {"state": "done"}, age=3 * hour
    )
    _event(
        session,
        workspace,
        landed,
        EventType.TICKET_LANDED,
        {"ok": True, "target": "main"},
        age=2 * hour,
    )
    for _ in range(2):
        _event(
            session, workspace, stuck, EventType.AGENT_RUN_COMPLETED, {"status": "failed"}, age=hour
        )
    _event(
        session,
        workspace,
        stuck,
        EventType.STAGE_COMPLETED,
        {"outcome": "reject", "stage_key": "review"},
        age=hour,
    )
    _event(
        session,
        workspace,
        stuck,
        EventType.GATE_EVALUATED,
        {"outcome": "unavailable", "stage_key": "gate"},
        age=hour,
    )
    # Noise the digest leaves out, and a highlight from before the window.
    _event(session, workspace, quiet, EventType.ARTIFACT_CREATED, {"kind": "context"}, age=hour)
    _event(
        session, workspace, quiet, EventType.AGENT_RUN_COMPLETED, {"status": "succeeded"}, age=hour
    )
    _event(session, workspace, quiet, EventType.TICKET_CREATED, {}, age=timedelta(days=30))
    _event(
        session,
        workspace,
        quiet,
        EventType.TICKET_STATE_CHANGED,
        {"state": "not-a-state"},
        age=hour,
    )

    result = workspace_activity(session, workspace, utcnow() - timedelta(days=1))

    assert [t.external_id for t in result.tickets] == ["T-2", "T-1"]
    stuck_row, landed_row = result.tickets
    assert [(h.kind, h.detail, h.count) for h in stuck_row.highlights] == [
        (ActivityHighlightKind.STAGE_REJECTED, "review", 1),
        (ActivityHighlightKind.RUN_FAILED, "", 2),
        (ActivityHighlightKind.GATE_FAILED, "gate", 1),
    ]
    assert [(h.kind, h.detail) for h in landed_row.highlights] == [
        (ActivityHighlightKind.MOVED, "done"),
        (ActivityHighlightKind.LANDED, "main"),
    ]
    assert result.totals[ActivityHighlightKind.RUN_FAILED] == 2
    assert ActivityHighlightKind.CREATED not in result.totals
    assert (result.more_tickets, result.events_truncated) == (0, False)

    failures = workspace_activity(
        session, workspace, utcnow() - timedelta(days=1), frozenset({ActivityHighlightKind.LANDED})
    )
    assert [t.external_id for t in failures.tickets] == ["T-1"]
    assert failures.totals == result.totals


def test_activity_api_validates_the_window(client, isolated_db, tmp_path):
    with Session(isolated_db) as session:
        session.add(Workspace(slug="dash", name="dash", repo_path=str(tmp_path)))
        session.commit()
    now = utcnow()
    future = (now + timedelta(days=1)).isoformat()
    ancient = (now - timedelta(days=400)).isoformat()
    assert client.get("/api/workspaces/dash/activity", params={"since": future}).status_code == 422
    assert client.get("/api/workspaces/dash/activity", params={"since": ancient}).status_code == 422
    ok = client.get(
        "/api/workspaces/dash/activity",
        params={"since": (now - timedelta(days=7)).isoformat(), "kind": ["landed", "run_failed"]},
    )
    assert ok.status_code == 200
    assert ok.json()["kinds"] == ["landed", "run_failed"]
    assert (
        client.get("/api/workspaces/nope/activity", params={"since": now.isoformat()}).status_code
        == 404
    )
