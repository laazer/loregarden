"""An oversized ticket is split into ordered children before any work is committed."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loregarden.models.domain import (
    DomainEvent,
    EventType,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.acceptance_criteria import load_criteria, serialize_criteria
from loregarden.services.branch_names import resolve_ticket_branch
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


def _set_stage(db_session: Session, ticket: Ticket, key: str, status: StageStatus) -> None:
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, key, status)
    db_session.add(instance)
    db_session.commit()


def _decisions(db_session: Session, ticket: Ticket) -> list[dict]:
    rows = db_session.exec(
        select(DomainEvent.payload_json)
        .where(DomainEvent.ticket_id == ticket.id)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
    ).all()
    return [json.loads(row) for row in rows]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(name="in_repo")
def in_repo_fixture(db_session: Session, oversized: Ticket, tmp_path: Path) -> Path:
    """The oversized ticket's workspace on a real repository, with the ticket's
    branch cut from main and nothing committed on it yet."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "branch", resolve_ticket_branch(oversized))
    workspace = db_session.get(Workspace, oversized.workspace_id)
    workspace.repo_path = str(repo)
    db_session.add(workspace)
    db_session.commit()
    return repo


def _commit_on_ticket_branch(repo: Path, ticket: Ticket, *, merge: bool = False) -> None:
    branch = resolve_ticket_branch(ticket)
    _git(repo, "checkout", "-q", branch)
    if merge:
        _git(repo, "checkout", "-q", "main")
        (repo / "main.txt").write_text("landed on main\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "on main")
        _git(repo, "checkout", "-q", branch)
        _git(repo, "merge", "-q", "--no-ff", "-m", "take main", "main")
    else:
        (repo / "work.txt").write_text("the ticket's own work\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "work")
    _git(repo, "checkout", "-q", "main")


def test_a_ticket_that_grew_after_a_stage_ran_is_split_while_nothing_is_committed(
    db_session: Session, oversized: Ticket, in_repo: Path
):
    """lg-external-harness-1026's shape: small at triage, oversized once spec
    wrote its criteria, and nothing on its branch yet. A stage having run is not
    a reason to refuse; work a split would strand is."""
    _set_stage(db_session, oversized, "work", StageStatus.DONE)

    result = split_oversized_ticket(
        db_session, oversized, MagicMock(), generate=lambda _p: _reply(HALVES)
    )

    assert result.outcome is SplitOutcome.SPLIT
    assert len(_children(db_session, oversized)) == 2
    assert [d["decision"] for d in _decisions(db_session, oversized)] == [
        OrchestratorDecision.SPLIT_OVERSIZED_TICKET.value
    ]


def test_a_merge_of_main_alone_is_not_work_a_split_would_strand(
    db_session: Session, oversized: Ticket, in_repo: Path
):
    _set_stage(db_session, oversized, "work", StageStatus.DONE)
    _commit_on_ticket_branch(in_repo, oversized, merge=True)

    result = split_oversized_ticket(
        db_session, oversized, MagicMock(), generate=lambda _p: _reply(HALVES)
    )

    assert result.outcome is SplitOutcome.SPLIT


def test_a_branch_carrying_its_own_commit_runs_unsplit_and_says_so_once(
    db_session: Session, oversized: Ticket, in_repo: Path
):
    _set_stage(db_session, oversized, "work", StageStatus.DONE)
    _commit_on_ticket_branch(in_repo, oversized)
    generate = MagicMock()
    callbacks = MagicMock()

    first = split_oversized_ticket(db_session, oversized, callbacks, generate=generate)
    second = split_oversized_ticket(db_session, oversized, callbacks, generate=generate)

    assert first.outcome is SplitOutcome.WORK_STARTED
    assert "1 commit" in first.detail
    assert second.outcome is SplitOutcome.NOT_NEEDED
    generate.assert_not_called()
    assert _children(db_session, oversized) == []
    callbacks.attach_artifact.assert_called_once()
    assert [d["decision"] for d in _decisions(db_session, oversized)] == [
        OrchestratorDecision.RAN_OVERSIZED_UNSPLIT.value
    ]


def test_an_unreadable_branch_counts_as_work_started(db_session: Session, oversized: Ticket):
    """The fixture's workspace has no repository: whether work exists cannot be
    read, and an unprovable "nothing to lose" is not a reason to split."""
    _set_stage(db_session, oversized, "work", StageStatus.DONE)
    generate = MagicMock()

    result = split_oversized_ticket(db_session, oversized, MagicMock(), generate=generate)

    assert result.outcome is SplitOutcome.WORK_STARTED
    generate.assert_not_called()
    assert _children(db_session, oversized) == []


@pytest.mark.parametrize("status", [StageStatus.RUNNING, StageStatus.AWAITING])
def test_a_ticket_mid_stage_waits_for_the_boundary(
    db_session: Session, oversized: Ticket, status: StageStatus
):
    _set_stage(db_session, oversized, "work", status)
    generate = MagicMock()
    callbacks = MagicMock()

    result = split_oversized_ticket(db_session, oversized, callbacks, generate=generate)

    assert result.outcome is SplitOutcome.NOT_NEEDED
    generate.assert_not_called()
    callbacks.attach_artifact.assert_not_called()
    assert _decisions(db_session, oversized) == []


def test_a_failed_split_is_not_retried_until_the_ticket_changes(
    db_session: Session, oversized: Ticket
):
    calls: list[str] = []

    def boom(prompt: str) -> str:
        calls.append(prompt)
        raise RuntimeError("Ticket split exited 1")

    split_oversized_ticket(db_session, oversized, MagicMock(), generate=boom)
    again = split_oversized_ticket(db_session, oversized, MagicMock(), generate=boom)
    assert again.outcome is SplitOutcome.NOT_NEEDED
    assert len(calls) == 1

    oversized.acceptance_criteria_json = serialize_criteria([*CRITERIA, "AC99 — one more"])
    db_session.add(oversized)
    db_session.commit()
    changed = split_oversized_ticket(db_session, oversized, MagicMock(), generate=boom)

    assert changed.outcome is SplitOutcome.FAILED
    assert len(calls) == 2
    decisions = _decisions(db_session, oversized)
    assert [d["decision"] for d in decisions] == [
        OrchestratorDecision.RAN_OVERSIZED_UNSPLIT.value
    ] * 2
    assert decisions[0]["content_digest"] != decisions[1]["content_digest"]


def test_an_oversized_bug_splits_into_tasks(db_session: Session, oversized: Ticket):
    oversized.work_item_type = WorkItemType.BUG
    db_session.add(oversized)
    db_session.commit()

    result = split_oversized_ticket(
        db_session,
        oversized,
        MagicMock(),
        generate=lambda _p: _reply(HALVES, work_item_type="task"),
    )

    assert result.outcome is SplitOutcome.SPLIT
    children = _children(db_session, oversized)
    assert {child.work_item_type for child in children} == {WorkItemType.TASK}


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
