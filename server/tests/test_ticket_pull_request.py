"""The PR tab asks GitHub for the branch's PR, and keeps "none" apart from "failed".

It used to read only the `pr` artifact that its own "Open PR" button writes, so
a PR opened any other way showed as "No pull request opened".
"""

import json
import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    PullRequestCheckOutcome,
    PullRequestLookup,
    PullRequestReview,
    PullRequestState,
    Ticket,
    Workspace,
)
from loregarden.services.ticket_pull_request import ticket_pull_request
from sqlmodel import Session

BRANCH = "loregarden/lg-1-add-the-thing"

GH_PR = {
    "number": 555,
    "url": "https://github.com/o/r/pull/555",
    "title": "feat: the thing",
    "state": "OPEN",
    "isDraft": False,
    "baseRefName": "main",
    "headRefName": BRANCH,
    "additions": 10,
    "deletions": 2,
    "changedFiles": 3,
    "reviewDecision": "",
    "mergeable": "MERGEABLE",
    "body": "Body",
    "statusCheckRollup": [
        {
            "__typename": "CheckRun",
            "name": "Server",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "https://ci/1",
        },
        {
            "__typename": "CheckRun",
            "name": "Client",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "detailsUrl": "https://ci/2",
        },
        {
            "__typename": "CheckRun",
            "name": "Lint",
            "status": "IN_PROGRESS",
            "conclusion": "",
            "detailsUrl": "",
        },
        {
            "__typename": "CheckRun",
            "name": "Docs",
            "status": "COMPLETED",
            "conclusion": "SKIPPED",
            "detailsUrl": "",
        },
        {
            "__typename": "StatusContext",
            "context": "legacy/ci",
            "state": "PENDING",
            "targetUrl": "https://ci/3",
        },
    ],
}


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    (tmp_path / ".git").mkdir()
    ws = Workspace(slug="proj", name="proj", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(
        external_id="LG-1", workspace_id=workspace.id, title="Add the thing", branch=BRANCH
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _gh(returncode: int, stdout: str = "", stderr: str = ""):
    proc = subprocess.CompletedProcess(["gh"], returncode, stdout=stdout, stderr=stderr)
    return mock.patch("loregarden.services.ticket_pull_request.run_gh", return_value=proc)


def test_a_pr_opened_outside_loregarden_is_found(session, workspace, ticket):
    with _gh(0, json.dumps(GH_PR)) as run_gh:
        result = ticket_pull_request(session, ticket, workspace)

    assert run_gh.call_args.args[0][:3] == ["pr", "view", BRANCH]
    assert result.lookup is PullRequestLookup.FOUND
    pr = result.pull_request
    assert pr is not None
    assert (pr.number, pr.state, pr.review, pr.has_conflicts) == (
        555,
        PullRequestState.OPEN,
        PullRequestReview.NOT_REQUIRED,
        False,
    )
    assert [(c.name, c.outcome) for c in pr.checks] == [
        ("Server", PullRequestCheckOutcome.FAILING),
        ("Client", PullRequestCheckOutcome.PASSING),
        ("Lint", PullRequestCheckOutcome.PENDING),
        ("Docs", PullRequestCheckOutcome.SKIPPED),
        ("legacy/ci", PullRequestCheckOutcome.PENDING),
    ]
    assert pr.checks[4].url == "https://ci/3"


def test_merged_and_conflicting_are_read_from_github(session, workspace, ticket):
    payload = {**GH_PR, "state": "MERGED", "mergeable": "CONFLICTING", "reviewDecision": "APPROVED"}
    with _gh(0, json.dumps(payload)):
        pr = ticket_pull_request(session, ticket, workspace).pull_request

    assert pr is not None
    assert (pr.state, pr.has_conflicts, pr.review) == (
        PullRequestState.MERGED,
        True,
        PullRequestReview.APPROVED,
    )


def test_no_pr_is_none_not_a_failure(session, workspace, ticket):
    with _gh(1, stderr=f'no pull requests found for branch "{BRANCH}"'):
        result = ticket_pull_request(session, ticket, workspace)

    assert (result.lookup, result.pull_request, result.error) == (PullRequestLookup.NONE, None, "")


def test_a_failed_lookup_says_why_and_offers_the_recorded_pr(session, workspace, ticket):
    recorded = {
        "url": "https://github.com/o/r/pull/9",
        "number": "9",
        "title": "t",
        "branch": BRANCH,
        "body": "",
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.PR,
            title="PR #9",
            content_json=json.dumps(recorded),
        )
    )
    session.commit()

    with _gh(1, stderr="HTTP 401: Bad credentials"):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert "Bad credentials" in result.error
    assert result.recorded is not None
    assert result.recorded.url == recorded["url"]


@pytest.mark.parametrize(
    "side_effect",
    [subprocess.TimeoutExpired(["gh"], 15), FileNotFoundError("gh")],
)
def test_gh_that_cannot_run_is_a_failure(session, workspace, ticket, side_effect):
    with mock.patch("loregarden.services.ticket_pull_request.run_gh", side_effect=side_effect):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FAILED
    assert result.error


def test_unparseable_gh_output_is_a_failure(session, workspace, ticket):
    with _gh(0, "not json"):
        result = ticket_pull_request(session, ticket, workspace)

    assert result.lookup is PullRequestLookup.FAILED
