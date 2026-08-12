"""The four moments of a fan-out, over HTTP.

Launch, look, promote, decline. The service tests cover the git and the
isolation; these cover the contract the review surface is written against —
including that a settled group cannot be settled twice, which is the one a
double-clicked button would hit.
"""

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
    Worktree,
)
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session
from tests.worktree_helpers import make_repo

STAGES = [
    WorkflowStageDef(
        key="implement",
        name="Implement",
        stage_type="agent",
        order=1,
        agent_id="backend_implementer",
    ),
    WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
]

PASS_REPORT = (
    '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "pass", "confidence": 0.9}\n<<<END_STAGE_REPORT>>>\n'
)


@pytest.fixture(name="fanout_ticket")
def fanout_ticket_fixture(db_session: Session, tmp_path):
    repo = make_repo(tmp_path, name="fanout-repo")
    workspace = Workspace(slug=f"fan-{uuid4().hex[:6]}", name="Fan", repo_path=str(repo))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    template = WorkflowTemplate(
        slug=f"fanout-api-{uuid4()}",
        name="Fan-out API template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id="FAN-1",
        workspace_id=workspace.id,
        title="Try it three ways",
        branch="loregarden/fan-1-try-it",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()
    return ticket


def _fake_execute(self, run, ticket, *, advance_workflow=True, skip_git_branch=False):
    worktree = self.session.get(Worktree, run.worktree_id)
    (Path(worktree.worktree_path) / "answer.txt").write_text(f"{worktree.branch}\n")
    return self.orchestration.complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=PASS_REPORT, advance_workflow=False
    )


def _launch(client, ticket_id, count=2):
    with patch.object(CliAgentExecutor, "execute", _fake_execute):
        return client.post(
            f"/api/tickets/{ticket_id}/fanout",
            json={"stage_key": "implement", "attempt_count": count},
        )


def test_launch_returns_the_group_with_one_attempt_each(client, fanout_ticket):
    response = _launch(client, fanout_ticket.id, count=3)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempt_count"] == 3
    assert len(body["attempts"]) == 3
    assert len({a["branch"] for a in body["attempts"]}) == 3


def test_reading_a_group_includes_a_manifest_per_attempt(client, fanout_ticket):
    group = _launch(client, fanout_ticket.id).json()

    response = client.get(f"/api/tickets/{fanout_ticket.id}/fanout/{group['id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["diffs"]) == 2
    for diff in body["diffs"]:
        assert [f["path"] for f in diff["files"]] == ["answer.txt"]
        assert diff["additions"] == 1


def test_a_file_patch_is_a_separate_request(client, fanout_ticket):
    group = _launch(client, fanout_ticket.id).json()
    attempt = group["attempts"][0]

    response = client.get(
        f"/api/tickets/{fanout_ticket.id}/fanout/{group['id']}/attempts/{attempt['id']}/file",
        params={"path": "answer.txt"},
    )

    assert response.status_code == 200, response.text
    assert attempt["branch"] in response.json()["patch"]


def test_listing_names_the_group_still_awaiting_a_verdict(client, fanout_ticket):
    group = _launch(client, fanout_ticket.id).json()

    response = client.get(f"/api/tickets/{fanout_ticket.id}/fanout")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["open_group_id"] == group["id"]
    assert len(body["groups"]) == 1
    assert body["groups"][0]["diffs"]


def test_promoting_settles_the_group(client, fanout_ticket):
    group = _launch(client, fanout_ticket.id).json()
    winner = group["attempts"][0]["id"]

    response = client.post(f"/api/tickets/{fanout_ticket.id}/fanout/{group['id']}/promote/{winner}")

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "promoted"
    assert response.json()["winner_attempt_id"] == winner


def test_a_settled_group_refuses_a_second_verdict(client, fanout_ticket):
    group = _launch(client, fanout_ticket.id).json()
    client.post(f"/api/tickets/{fanout_ticket.id}/fanout/{group['id']}/decline", json={})

    again = client.post(
        f"/api/tickets/{fanout_ticket.id}/fanout/{group['id']}/promote/{group['attempts'][0]['id']}"
    )

    assert again.status_code == 409
    assert "already settled" in again.json()["detail"]


def test_declining_reports_what_it_threw_away(client, fanout_ticket):
    group = _launch(client, fanout_ticket.id).json()

    response = client.post(
        f"/api/tickets/{fanout_ticket.id}/fanout/{group['id']}/decline",
        json={"reason": "both missed the point"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "declined"
    assert body["declined_reason"] == "both missed the point"
    assert len(body["discarded_attempts"]) == 2


def test_a_group_belonging_to_another_ticket_is_not_found(client, fanout_ticket, db_session):
    group = _launch(client, fanout_ticket.id).json()
    other = Ticket(
        external_id="FAN-2",
        workspace_id=fanout_ticket.workspace_id,
        title="Unrelated",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    response = client.get(f"/api/tickets/{other.id}/fanout/{group['id']}")

    assert response.status_code == 404
