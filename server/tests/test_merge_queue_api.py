"""The merge queue over HTTP (lg-milestone-that-970)."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.models.domain import (
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    PullRequestState,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services import merge_queue_requests
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.merge_queue import Candidate, enqueue, transition
from loregarden.services.merge_queue_github import StagedPullRequest
from loregarden.services.ticket_pull_request import GhCheck
from sqlmodel import Session, select

REQUIRED = ["Client (TypeScript)", "Server (Python)"]


class _GitHub:
    def __init__(self, *, unreachable: bool = False) -> None:
        self.unreachable = unreachable

    def required_contexts(self, base_branch: str) -> list[str]:
        if self.unreachable:
            raise GitHubReadError("gh timed out after 30s")
        return list(REQUIRED)

    def read_pull_request(self, number: int) -> StagedPullRequest:
        return StagedPullRequest(
            number=number,
            url=f"https://github.com/o/r/pull/{number}",
            state=PullRequestState.OPEN,
            head_branch="publish/integration/lg-1",
            head_sha="s" * 40,
            head_tree="t" * 40,
            base_branch="main",
            base_sha="b" * 40,
            checks=[GhCheck(name="Server (Python)", status="COMPLETED", conclusion="SUCCESS")],
        )


@pytest.fixture(name="queue")
def queue_fixture(client, isolated_db):
    """A workspace ``mq`` with two entries: a testing head and a queued one behind it."""
    with Session(isolated_db) as session:
        ws = Workspace(slug="mq", name="mq", repo_path="/nonexistent")
        session.add(ws)
        session.commit()
        ticket = Ticket(
            external_id="lg-1", workspace_id=ws.id, title="t", work_item_type=WorkItemType.FEATURE
        )
        session.add(ticket)
        session.commit()
        entries = []
        for pr, head in ((7, "a"), (8, "c")):
            entries.append(
                enqueue(
                    session,
                    ticket,
                    ws,
                    Candidate(
                        pr,
                        f"https://github.com/o/r/pull/{pr}",
                        "publish/integration/lg-1",
                        "integration/lg-1",
                        head * 40,
                        "main",
                    ),
                ).entry
            )
        head, behind = entries
        recorded = [{"name": "Server (Python)", "outcome": "pending", "url": ""}]
        transition(
            session,
            head,
            MergeQueueState.QUEUED,
            state=MergeQueueState.TESTING,
            wait=MergeQueueWait.CHECKS_RUNNING,
            staged_sha="s" * 40,
            checks_json=json.dumps(recorded),
        )
        return head.id, behind.id


def test_an_empty_queue_is_an_empty_list_not_an_error(client, isolated_db):
    with Session(isolated_db) as session:
        session.add(Workspace(slug="empty", name="empty", repo_path="/nonexistent"))
        session.commit()

    response = client.get("/api/merge-queue/empty")

    assert response.status_code == 200
    assert response.json() == {"workspace_slug": "empty", "entries": [], "head_id": None}


def test_an_unknown_workspace_is_a_404(client):
    assert client.get("/api/merge-queue/nope").status_code == 404


def test_the_waiting_head_says_what_it_waits_on(client, queue):
    head_id, behind_id = queue

    listed = client.get("/api/merge-queue/mq").json()

    assert listed["head_id"] == head_id
    by_id = {entry["id"]: entry for entry in listed["entries"]}
    assert [entry["id"] for entry in listed["entries"]] == [head_id, behind_id]
    assert by_id[head_id]["wait"] == MergeQueueWait.CHECKS_RUNNING.value
    assert by_id[head_id]["is_head"] and not by_id[behind_id]["is_head"]
    assert by_id[head_id]["can_withdraw"], "waiting on checks is cancellable"


def test_the_heads_detail_reads_its_checks_live(client, queue):
    head_id, _ = queue

    with mock.patch.object(merge_queue_requests, "_github", return_value=_GitHub()):
        detail = client.get(f"/api/merge-queue/entries/{head_id}").json()

    assert detail["checks_live"] is True
    assert {(c["name"], c["outcome"]) for c in detail["checks"]} == {
        ("Server (Python)", "passing"),
        ("Client (TypeScript)", "pending"),
    }


def test_a_live_read_that_fails_is_a_502_not_the_recorded_checks(client, queue):
    head_id, _ = queue

    with mock.patch.object(merge_queue_requests, "_github", return_value=_GitHub(unreachable=True)):
        response = client.get(f"/api/merge-queue/entries/{head_id}")

    assert response.status_code == 502
    assert "gh timed out" in response.json()["detail"]


def test_a_failed_entry_can_be_retried_to_the_back(client, isolated_db, queue):
    head_id, behind_id = queue
    with Session(isolated_db) as session:
        head = session.get(MergeQueueEntry, head_id)
        transition(
            session, head, MergeQueueState.TESTING, state=MergeQueueState.FAILED, detail="red"
        )

    failed = client.get(f"/api/merge-queue/entries/{head_id}?live=false").json()
    assert (failed["state"], failed["can_retry"], failed["can_withdraw"]) == ("failed", True, False)

    retried = client.post(f"/api/merge-queue/entries/{head_id}/retry")
    assert retried.status_code == 200
    listed = client.get("/api/merge-queue/mq").json()
    assert [e["id"] for e in listed["entries"][:2]] == [behind_id, head_id]
    assert client.post(f"/api/merge-queue/entries/{behind_id}/retry").status_code == 409


def test_withdraw_takes_an_entry_out_and_refuses_twice(client, queue):
    _, behind_id = queue

    first = client.post(f"/api/merge-queue/entries/{behind_id}/withdraw", json={"reason": "op"})
    second = client.post(f"/api/merge-queue/entries/{behind_id}/withdraw", json={})

    assert first.status_code == 200 and first.json()["state"] == "withdrawn"
    assert second.status_code == 409


def test_enqueue_without_an_open_pr_is_a_409_naming_how_to_publish(client, queue):
    with (
        mock.patch.object(
            merge_queue_requests, "published_branch", return_value=("integration/lg-1", "e" * 40)
        ),
        mock.patch.object(merge_queue_requests, "GhQueueGitHub") as gh,
    ):
        gh.return_value.open_pull_request.return_value = None
        response = client.post("/api/merge-queue/mq/entries", json={"ticket_id": "lg-1"})

    assert response.status_code == 409
    assert "loregarden_land_ticket" in response.json()["detail"]


def test_enqueue_when_github_cannot_answer_is_a_502(client, queue):
    with (
        mock.patch.object(
            merge_queue_requests, "published_branch", return_value=("integration/lg-1", "e" * 40)
        ),
        mock.patch.object(merge_queue_requests, "GhQueueGitHub") as gh,
    ):
        gh.return_value.open_pull_request.side_effect = GitHubReadError("no network")
        response = client.post("/api/merge-queue/mq/entries", json={"ticket_id": "lg-1"})

    assert response.status_code == 502


def test_enqueue_with_an_open_pr_admits_it(client, queue):
    with (
        mock.patch.object(
            merge_queue_requests, "published_branch", return_value=("integration/lg-1", "e" * 40)
        ),
        mock.patch.object(merge_queue_requests, "GhQueueGitHub") as gh,
    ):
        gh.return_value.open_pull_request.return_value = (9, "https://github.com/o/r/pull/9")
        response = client.post("/api/merge-queue/mq/entries", json={"ticket_id": "lg-1"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "admitted"
    assert (body["entry"]["pr_number"], body["entry"]["head_sha"]) == (9, "e" * 40)


def _child_of(isolated_db, parent_external_id: str) -> str:
    with Session(isolated_db) as session:
        parent = session.exec(select(Ticket).where(Ticket.external_id == parent_external_id)).one()
        child = Ticket(
            external_id="lg-2",
            workspace_id=parent.workspace_id,
            title="child",
            work_item_type=WorkItemType.TASK,
            parent_ticket_id=parent.id,
        )
        session.add(child)
        session.commit()
        return child.id


def test_a_child_ticket_reads_its_trees_entry_and_how_many_are_ahead(client, isolated_db, queue):
    child_id = _child_of(isolated_db, "lg-1")

    response = client.get(f"/api/merge-queue/tickets/{child_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["root_external_id"] == "lg-1"
    assert body["entry"]["pr_number"] == 8, "the tree's latest entry, not its first"
    assert body["entry"]["state"] == "queued"
    assert body["ahead"] == 1


def test_a_ticket_never_queued_has_no_entry(client, isolated_db):
    with Session(isolated_db) as session:
        ws = Workspace(slug="quiet", name="quiet", repo_path="/nonexistent")
        session.add(ws)
        session.commit()
        ticket = Ticket(
            external_id="q-1", workspace_id=ws.id, title="t", work_item_type=WorkItemType.FEATURE
        )
        initiative = Ticket(
            external_id="q-2", workspace_id=None, title="i", work_item_type=WorkItemType.INITIATIVE
        )
        session.add_all([ticket, initiative])
        session.commit()
        ids = (ticket.id, initiative.id)

    for ticket_id in ids:
        body = client.get(f"/api/merge-queue/tickets/{ticket_id}").json()
        assert (body["entry"], body["ahead"]) == (None, 0)


def test_an_unknown_ticket_is_a_404(client):
    assert client.get("/api/merge-queue/tickets/nope-404").status_code == 404


def test_the_overview_lists_each_workspace_that_has_held_an_entry(client, isolated_db, queue):
    with Session(isolated_db) as session:
        session.add(Workspace(slug="never-queued", name="n", repo_path="/nonexistent"))
        session.commit()

    body = client.get("/api/merge-queue").json()

    assert [view["workspace_slug"] for view in body["queues"]] == ["mq"]
    assert [entry["pr_number"] for entry in body["queues"][0]["entries"]] == [7, 8]
