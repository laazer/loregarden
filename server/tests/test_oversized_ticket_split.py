"""An oversized ticket is split into ordered children before its pipeline runs."""

import json
from unittest.mock import MagicMock

import pytest
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.acceptance_criteria import load_criteria, serialize_criteria
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.oversized_ticket_split import (
    MAX_ACCEPTANCE_CRITERIA,
    SplitOutcome,
    split_oversized_ticket,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_state import set_stage_status
from loregarden.testing.factories import NO_REPO, make_ticket
from sqlmodel import Session, select

CRITERIA = [f"AC{n} — criterion number {n}" for n in range(1, MAX_ACCEPTANCE_CRITERIA + 2)]


def _reply(groups: list[list[str]], *, work_item_type: str = "capability") -> str:
    children = [
        {
            "external_id": f"part-{i}",
            "title": f"Part {i}",
            "work_item_type": work_item_type,
            "description": f"Part {i} of the work.",
            "acceptance_criteria": group,
            "priority": 2,
            "children": [],
        }
        for i, group in enumerate(groups, start=1)
    ]
    return f"Here is the split:\n```json\n{json.dumps({'children': children})}\n```"


HALVES = [CRITERIA[:8], CRITERIA[8:]]

_STAGES = [
    {
        "key": "work",
        "name": "Work",
        "agent_id": "planner",
        "skill_name": "",
        "stage_type": "agent",
        "order": 1,
    },
    {
        "key": "done",
        "name": "Done",
        "agent_id": "",
        "skill_name": "",
        "stage_type": "agent",
        "order": 2,
        "terminal": True,
    },
]


@pytest.fixture(name="oversized")
def oversized_fixture(db_session: Session) -> Ticket:
    template = WorkflowTemplate(
        slug="split-tpl",
        name="Split test template",
        stages_json=json.dumps(_STAGES),
        transitions_json=json.dumps([{"from": "work", "to": "done", "when": "pass"}]),
        version=1,
    )
    db_session.add(template)
    db_session.commit()
    ws = Workspace(
        slug="split-ws", name="split-ws", repo_path=NO_REPO, workflow_template_id=template.id
    )
    db_session.add(ws)
    db_session.commit()
    ticket = make_ticket(
        db_session,
        workspace_id=ws.id,
        external_id="split-parent-1",
        title="Big feature",
        work_item_type=WorkItemType.FEATURE,
    )
    ticket.acceptance_criteria_json = serialize_criteria(CRITERIA)
    db_session.add(ticket)
    db_session.commit()
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    db_session.refresh(ticket)
    return ticket


def _children(db_session: Session, parent: Ticket) -> list[Ticket]:
    return list(
        db_session.exec(
            select(Ticket)
            .where(Ticket.parent_ticket_id == parent.id)
            .where(Ticket.is_integration_review == False)  # noqa: E712
        ).all()
    )


def test_oversized_ticket_splits_into_ordered_children(db_session: Session, oversized: Ticket):
    callbacks = MagicMock()

    result = split_oversized_ticket(
        db_session, oversized, callbacks, generate=lambda _prompt: _reply(HALVES)
    )

    assert result.outcome is SplitOutcome.SPLIT
    first, second = (db_session.get(Ticket, cid) for cid in result.child_ids)
    assert [first.parent_ticket_id, second.parent_ticket_id] == [oversized.id, oversized.id]
    assert {first.work_item_type, second.work_item_type} == {WorkItemType.CAPABILITY}
    assert (
        load_criteria(first.acceptance_criteria_json)
        + load_criteria(second.acceptance_criteria_json)
        == CRITERIA
    )
    assert TicketDependencyService(db_session).prerequisites(second.id) == [first.id]
    reviews = db_session.exec(
        select(Ticket).where(
            Ticket.parent_ticket_id == oversized.id,
            Ticket.is_integration_review == True,  # noqa: E712
        )
    ).all()
    assert len(reviews) == 1, "the split parent gets an integration review, as Studio's do"
    callbacks.attach_artifact.assert_called_once()


def test_a_split_that_drops_a_criterion_is_refused(db_session: Session, oversized: Ticket):
    callbacks = MagicMock()

    result = split_oversized_ticket(
        db_session, oversized, callbacks, generate=lambda _p: _reply([CRITERIA[:8], CRITERIA[8:-1]])
    )

    assert result.outcome is SplitOutcome.FAILED
    assert _children(db_session, oversized) == []
    callbacks.attach_artifact.assert_called_once()


def test_a_failed_model_turn_leaves_the_ticket_whole_and_visible(
    db_session: Session, oversized: Ticket
):
    callbacks = MagicMock()

    def boom(_prompt: str) -> str:
        raise RuntimeError("Ticket split exited 1")

    result = split_oversized_ticket(db_session, oversized, callbacks, generate=boom)

    assert result.outcome is SplitOutcome.FAILED
    assert "exited 1" in result.detail
    assert _children(db_session, oversized) == []
    callbacks.attach_artifact.assert_called_once()


def test_a_ticket_under_the_threshold_is_left_alone(db_session: Session, oversized: Ticket):
    oversized.acceptance_criteria_json = serialize_criteria(CRITERIA[:MAX_ACCEPTANCE_CRITERIA])
    db_session.add(oversized)
    db_session.commit()
    generate = MagicMock()
    callbacks = MagicMock()

    result = split_oversized_ticket(db_session, oversized, callbacks, generate=generate)

    assert result.outcome is SplitOutcome.NOT_NEEDED
    generate.assert_not_called()
    callbacks.attach_artifact.assert_not_called()


def test_a_ticket_already_in_flight_is_never_split(db_session: Session, oversized: Ticket):
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(oversized)
    set_stage_status(oversized, instance, stages, "work", StageStatus.DONE)
    db_session.add(instance)
    db_session.commit()
    generate = MagicMock()

    result = split_oversized_ticket(db_session, oversized, MagicMock(), generate=generate)

    assert result.outcome is SplitOutcome.NOT_NEEDED
    generate.assert_not_called()


def test_a_task_cannot_hold_children_so_runs_unsplit(db_session: Session, oversized: Ticket):
    oversized.work_item_type = WorkItemType.TASK
    db_session.add(oversized)
    db_session.commit()
    generate = MagicMock()
    callbacks = MagicMock()

    result = split_oversized_ticket(db_session, oversized, callbacks, generate=generate)

    assert result.outcome is SplitOutcome.UNSPLITTABLE
    generate.assert_not_called()
    callbacks.attach_artifact.assert_called_once()
