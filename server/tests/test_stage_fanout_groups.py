import importlib
import json
from datetime import datetime, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
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
from sqlmodel import Session, SQLModel, create_engine


def _fanout_service():
    return importlib.import_module("loregarden.services.stage_fanout_groups")


def _seed_ticket_with_workflow(session: Session, *, suffix: str = "main") -> dict[str, str]:
    workspace = Workspace(slug=f"fanout-{suffix}", name=f"Fanout {suffix}")
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    stages = [
        WorkflowStageDef(key="spec", name="Specification", order=1, agent_id="spec"),
        WorkflowStageDef(
            key="implement", name="Implement", order=2, agent_id="backend_implementer"
        ),
        WorkflowStageDef(key="verify", name="Verify", order=3, agent_id="verifier"),
    ]
    template = WorkflowTemplate(
        slug=f"fanout-template-{suffix}",
        name=f"Fanout Template {suffix}",
        stages_json=json.dumps([stage.model_dump(mode="json") for stage in stages]),
        transitions_json="[]",
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    ticket = Ticket(
        external_id=f"fanout-ticket-{suffix}",
        workspace_id=workspace.id,
        title=f"Fanout ticket {suffix}",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="spec",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="spec",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="spec",
        stages_json=json.dumps(
            [
                {"key": "spec", "status": "running"},
                {"key": "implement", "status": "pending"},
                {"key": "verify", "status": "pending"},
            ]
        ),
    )
    session.add(instance)
    session.commit()
    session.refresh(instance)

    run = OrchestrationRun(
        run_code=f"orch_{suffix}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
        status=OrchestrationRunStatus.RUNNING,
        current_stage_key="spec",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    return {
        "workspace_id": workspace.id,
        "template_id": template.id,
        "ticket_id": ticket.id,
        "workflow_instance_id": instance.id,
        "orchestration_run_id": run.id,
    }


def _create_run(
    session: Session,
    *,
    run_id: str,
    ticket_id: str | None,
    workspace_id: str,
    orchestration_run_id: str | None,
    stage_key: str = "implement",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> AgentRun:
    run = AgentRun(
        id=run_id,
        run_code=f"run_{run_id}",
        ticket_id=ticket_id,
        workspace_id=workspace_id,
        orchestration_run_id=orchestration_run_id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _create_worktree(
    session: Session,
    *,
    worktree_id: str,
    workspace_id: str,
    agent_run_id: str,
    branch: str,
) -> Worktree:
    worktree = Worktree(
        id=worktree_id,
        workspace_id=workspace_id,
        agent_run_id=agent_run_id,
        parent_branch="main",
        worktree_path=f"/tmp/{worktree_id}",
        branch=branch,
    )
    session.add(worktree)
    session.commit()
    session.refresh(worktree)
    return worktree


def _create_ticket_in_workspace(
    session: Session,
    *,
    workspace_id: str,
    external_id: str,
) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace_id,
        title=f"Fanout ticket {external_id}",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_stage_fanout_models_and_enums_are_exported_from_domain():
    """R1.AC1/R1.AC2/R1.AC6/R2.AC1/R2.AC2/R2.AC7: domain exports are the public model API."""
    from loregarden.models.domain import (  # pylint: disable=import-outside-toplevel
        StageFanoutAttempt,
        StageFanoutAttemptStatus,
        StageFanoutGroup,
        StageFanoutGroupStatus,
        StageFanoutOutcome,
    )

    assert StageFanoutGroup.__tablename__ == "stage_fanout_groups"
    assert StageFanoutAttempt.__tablename__ == "stage_fanout_attempts"
    assert [status.value for status in StageFanoutGroupStatus] == [
        "open",
        "settling",
        "settled",
        "cancelled",
        "failed",
    ]
    assert [outcome.value for outcome in StageFanoutOutcome] == [
        "pending",
        "promoted",
        "declined",
        "cancelled",
        "failed",
    ]
    assert [status.value for status in StageFanoutAttemptStatus] == [
        "planned",
        "queued",
        "running",
        "awaiting_permission",
        "succeeded",
        "failed",
        "cancelled",
        "declined",
        "promoted",
    ]


def test_fresh_sqlmodel_metadata_contains_stage_fanout_tables_and_constraints(tmp_path):
    """R1.AC1-R1.AC8/R2.AC1-R2.AC9: metadata-created schemas include the durable contract."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fanout-metadata.db'}")
    SQLModel.metadata.create_all(engine)

    with engine.connect() as conn:
        group_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(stage_fanout_groups)")
        }
        attempt_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(stage_fanout_attempts)")
        }
        group_indexes = conn.exec_driver_sql("PRAGMA index_list(stage_fanout_groups)").fetchall()
        attempt_indexes = conn.exec_driver_sql(
            "PRAGMA index_list(stage_fanout_attempts)"
        ).fetchall()
        group_index_columns = [
            [column[2] for column in conn.exec_driver_sql(f"PRAGMA index_info({index[1]})")]
            for index in group_indexes
        ]
        attempt_index_columns = [
            [column[2] for column in conn.exec_driver_sql(f"PRAGMA index_info({index[1]})")]
            for index in attempt_indexes
        ]

    assert group_columns >= {
        "id",
        "workspace_id",
        "ticket_id",
        "orchestration_run_id",
        "stage_key",
        "attempt_count",
        "pre_fanout_workflow_stage_key",
        "pre_fanout_workflow_stage_status",
        "pre_fanout_stage_map_json",
        "pre_fanout_next_agent",
        "status",
        "outcome",
        "winner_attempt_id",
        "declined_reason",
        "failure_summary",
        "created_at",
        "updated_at",
        "settled_at",
    }
    assert attempt_columns >= {
        "id",
        "group_id",
        "attempt_index",
        "attempt_name",
        "agent_run_id",
        "worktree_id",
        "branch",
        "status",
        "failure_details",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    }
    assert ["workspace_id"] in group_index_columns
    assert ["ticket_id", "stage_key"] in group_index_columns
    assert ["orchestration_run_id"] in group_index_columns
    assert ["status"] in group_index_columns
    assert ["winner_attempt_id"] in group_index_columns
    assert ["group_id"] in attempt_index_columns
    assert ["agent_run_id"] in attempt_index_columns
    assert ["worktree_id"] in attempt_index_columns
    assert ["status"] in attempt_index_columns
    assert ["group_id", "attempt_index"] in attempt_index_columns
    assert any(index[2] for index in attempt_indexes)


def test_create_group_snapshots_ticket_and_workflow_state(db_session: Session):
    """R4.AC1-R4.AC3/R5.AC2: creation captures pre-fan-out state without mutating the ticket."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)

    group = service.create_group(
        db_session,
        ticket_id=ids["ticket_id"],
        stage_key="implement",
        attempt_count=3,
        orchestration_run_id=ids["orchestration_run_id"],
    )
    serialized = service.serialize_group(db_session, group.id)

    ticket = db_session.get(Ticket, ids["ticket_id"])
    assert ticket is not None
    assert ticket.workflow_stage_key == "spec"
    assert ticket.workflow_stage_status == StageStatus.RUNNING
    assert ticket.next_agent == "spec"
    assert serialized["workspace_id"] == ids["workspace_id"]
    assert serialized["ticket_id"] == ids["ticket_id"]
    assert serialized["orchestration_run_id"] == ids["orchestration_run_id"]
    assert serialized["stage_key"] == "implement"
    assert serialized["attempt_count"] == 3
    assert serialized["pre_fanout_workflow_stage_key"] == "spec"
    assert serialized["pre_fanout_workflow_stage_status"] == "running"
    assert serialized["pre_fanout_next_agent"] == "spec"
    assert json.loads(serialized["pre_fanout_stage_map_json"]) == [
        {"key": "spec", "status": "running"},
        {"key": "implement", "status": "pending"},
        {"key": "verify", "status": "pending"},
    ]
    assert serialized["status"] == "open"
    assert serialized["outcome"] == "pending"
    assert serialized["winner_attempt_id"] is None
    assert serialized["settled_at"] is None


def test_create_group_rejects_invalid_attempt_count_and_unknown_stage(db_session: Session):
    """R1.AC8/R4.AC2: callers get actionable validation for invalid group requests."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)

    with pytest.raises(ValueError, match="attempt_count"):
        service.create_group(db_session, ids["ticket_id"], "implement", 0)

    with pytest.raises(ValueError, match="stage_key"):
        service.create_group(db_session, ids["ticket_id"], "not-a-stage", 2)
    with pytest.raises(ValueError, match="stage_key"):
        service.create_group(db_session, ids["ticket_id"], "   ", 2)


def test_create_group_rejects_foreign_orchestration_run(db_session: Session):
    """R4.AC1/R4.AC2: group snapshots cannot point at another ticket's orchestration."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    other_ids = _seed_ticket_with_workflow(db_session, suffix="foreign-orchestration")

    with pytest.raises(ValueError, match="orchestration"):
        service.create_group(
            db_session,
            ids["ticket_id"],
            "implement",
            2,
            orchestration_run_id=other_ids["orchestration_run_id"],
        )

    with pytest.raises(ValueError, match="orchestration"):
        service.create_group(
            db_session,
            ids["ticket_id"],
            "implement",
            2,
            orchestration_run_id="missing-orchestration-run",
        )


def test_create_attempts_are_unique_zero_based_and_serialized_in_order(db_session: Session):
    """R2.AC4/R4.AC4/R4.AC9/R5.AC3: attempts are stable, ordered children of a group."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    group = service.create_group(db_session, ids["ticket_id"], "implement", 3)

    third = service.create_attempt(db_session, group.id, attempt_index=2)
    first = service.create_attempt(db_session, group.id, attempt_index=0)
    second = service.create_attempt(db_session, group.id, attempt_index=1, attempt_name="Variant B")

    with pytest.raises(ValueError, match="attempt_index"):
        service.create_attempt(db_session, group.id, attempt_index=1)
    with pytest.raises(ValueError, match="attempt_index"):
        service.create_attempt(db_session, group.id, attempt_index=-1)

    serialized = service.serialize_group(db_session, group.id)
    assert [attempt["id"] for attempt in serialized["attempts"]] == [
        first.id,
        second.id,
        third.id,
    ]
    assert [
        (attempt["attempt_index"], attempt["attempt_name"]) for attempt in serialized["attempts"]
    ] == [
        (0, "Attempt 1"),
        (1, "Variant B"),
        (2, "Attempt 3"),
    ]
    assert [attempt["status"] for attempt in serialized["attempts"]] == [
        "planned",
        "planned",
        "planned",
    ]


def test_link_attempt_run_validates_ticket_workspace_orchestration_and_duplicates(
    db_session: Session,
):
    """R2.AC5/R4.AC5/R5.AC4: run links cannot cross group context or duplicate within a group."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    other_ids = _seed_ticket_with_workflow(db_session, suffix="other")
    group = service.create_group(
        db_session, ids["ticket_id"], "implement", 2, ids["orchestration_run_id"]
    )
    first = service.create_attempt(db_session, group.id, attempt_index=0)
    second = service.create_attempt(db_session, group.id, attempt_index=1)
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    run = _create_run(
        db_session,
        run_id="run-main",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=ids["orchestration_run_id"],
        started_at=now,
    )
    wrong_ticket_run = _create_run(
        db_session,
        run_id="run-wrong-ticket",
        ticket_id=other_ids["ticket_id"],
        workspace_id=other_ids["workspace_id"],
        orchestration_run_id=other_ids["orchestration_run_id"],
    )
    wrong_orchestration_run = _create_run(
        db_session,
        run_id="run-wrong-orchestration",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=other_ids["orchestration_run_id"],
    )

    linked = service.link_attempt_run(db_session, first.id, run.id)
    assert linked.agent_run_id == run.id
    assert linked.started_at == now

    with pytest.raises(ValueError, match="agent_run_id"):
        service.link_attempt_run(db_session, second.id, run.id)
    with pytest.raises(ValueError, match="ticket|workspace"):
        service.link_attempt_run(db_session, second.id, wrong_ticket_run.id)
    with pytest.raises(ValueError, match="orchestration"):
        service.link_attempt_run(db_session, second.id, wrong_orchestration_run.id)


def test_link_attempt_worktree_validates_workspace_run_match_and_duplicates(db_session: Session):
    """R2.AC6/R4.AC6/R5.AC5: worktree links copy branch metadata and stay inside context."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    other_ids = _seed_ticket_with_workflow(db_session, suffix="worktree-other")
    group = service.create_group(db_session, ids["ticket_id"], "implement", 2)
    first = service.create_attempt(db_session, group.id, attempt_index=0)
    second = service.create_attempt(db_session, group.id, attempt_index=1)
    run = _create_run(
        db_session,
        run_id="run-worktree-main",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=None,
    )
    mismatch_run = _create_run(
        db_session,
        run_id="run-worktree-mismatch",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=None,
    )
    other_workspace_run = _create_run(
        db_session,
        run_id="run-other-workspace",
        ticket_id=other_ids["ticket_id"],
        workspace_id=other_ids["workspace_id"],
        orchestration_run_id=None,
    )
    worktree = _create_worktree(
        db_session,
        worktree_id="wt-main",
        workspace_id=ids["workspace_id"],
        agent_run_id=run.id,
        branch="fanout/attempt-1",
    )
    wrong_workspace_worktree = _create_worktree(
        db_session,
        worktree_id="wt-wrong-workspace",
        workspace_id=other_ids["workspace_id"],
        agent_run_id=other_workspace_run.id,
        branch="other/attempt",
    )
    mismatched_run_worktree = _create_worktree(
        db_session,
        worktree_id="wt-mismatched-run",
        workspace_id=ids["workspace_id"],
        agent_run_id=mismatch_run.id,
        branch="fanout/wrong-run",
    )

    service.link_attempt_run(db_session, first.id, run.id)
    linked = service.link_attempt_worktree(db_session, first.id, worktree.id)
    assert linked.worktree_id == worktree.id
    assert linked.branch == "fanout/attempt-1"

    with pytest.raises(ValueError, match="worktree_id"):
        service.link_attempt_worktree(db_session, second.id, worktree.id)
    with pytest.raises(ValueError, match="workspace"):
        service.link_attempt_worktree(db_session, second.id, wrong_workspace_worktree.id)
    with pytest.raises(ValueError, match="agent_run_id"):
        service.link_attempt_worktree(db_session, first.id, mismatched_run_worktree.id)


def test_link_attempt_worktree_validates_owning_run_context_before_attempt_run(
    db_session: Session,
):
    """R4.AC6: worktree-first links still reject worktrees owned by foreign runs."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    other_ticket = _create_ticket_in_workspace(
        db_session,
        workspace_id=ids["workspace_id"],
        external_id="fanout-worktree-foreign-ticket",
    )
    group = service.create_group(
        db_session, ids["ticket_id"], "implement", 3, ids["orchestration_run_id"]
    )
    first = service.create_attempt(db_session, group.id, attempt_index=0)
    second = service.create_attempt(db_session, group.id, attempt_index=1)
    third = service.create_attempt(db_session, group.id, attempt_index=2)
    valid_run = _create_run(
        db_session,
        run_id="run-worktree-first-valid",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=ids["orchestration_run_id"],
    )
    wrong_ticket_run = _create_run(
        db_session,
        run_id="run-worktree-first-wrong-ticket",
        ticket_id=other_ticket.id,
        workspace_id=ids["workspace_id"],
        orchestration_run_id=ids["orchestration_run_id"],
    )
    wrong_orchestration_run = _create_run(
        db_session,
        run_id="run-worktree-first-wrong-orchestration",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=None,
    )
    valid_worktree = _create_worktree(
        db_session,
        worktree_id="wt-worktree-first-valid",
        workspace_id=ids["workspace_id"],
        agent_run_id=valid_run.id,
        branch="fanout/worktree-first-valid",
    )
    wrong_ticket_worktree = _create_worktree(
        db_session,
        worktree_id="wt-worktree-first-wrong-ticket",
        workspace_id=ids["workspace_id"],
        agent_run_id=wrong_ticket_run.id,
        branch="fanout/worktree-first-wrong-ticket",
    )
    wrong_orchestration_worktree = _create_worktree(
        db_session,
        worktree_id="wt-worktree-first-wrong-orchestration",
        workspace_id=ids["workspace_id"],
        agent_run_id=wrong_orchestration_run.id,
        branch="fanout/worktree-first-wrong-orchestration",
    )

    linked = service.link_attempt_worktree(db_session, first.id, valid_worktree.id)
    assert linked.worktree_id == valid_worktree.id
    assert linked.branch == "fanout/worktree-first-valid"

    with pytest.raises(ValueError, match="ticket|workspace"):
        service.link_attempt_worktree(db_session, second.id, wrong_ticket_worktree.id)
    with pytest.raises(ValueError, match="orchestration"):
        service.link_attempt_worktree(db_session, third.id, wrong_orchestration_worktree.id)


def test_link_attempt_run_rejects_conflict_with_already_linked_worktree(
    db_session: Session,
):
    """R4.AC5/R4.AC6: run-after-worktree linking cannot overwrite the worktree owner."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    group = service.create_group(db_session, ids["ticket_id"], "implement", 1)
    attempt = service.create_attempt(db_session, group.id, attempt_index=0)
    worktree_run = _create_run(
        db_session,
        run_id="run-linked-worktree-owner",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=None,
    )
    conflicting_run = _create_run(
        db_session,
        run_id="run-linked-worktree-conflict",
        ticket_id=ids["ticket_id"],
        workspace_id=ids["workspace_id"],
        orchestration_run_id=None,
    )
    worktree = _create_worktree(
        db_session,
        worktree_id="wt-linked-before-run",
        workspace_id=ids["workspace_id"],
        agent_run_id=worktree_run.id,
        branch="fanout/worktree-before-run",
    )

    linked_worktree = service.link_attempt_worktree(db_session, attempt.id, worktree.id)
    assert linked_worktree.agent_run_id is None
    with pytest.raises(ValueError, match="worktree"):
        service.link_attempt_run(db_session, attempt.id, conflicting_run.id)

    linked_run = service.link_attempt_run(db_session, attempt.id, worktree_run.id)
    assert linked_run.agent_run_id == worktree_run.id


def test_update_attempt_status_sets_timestamps_and_serializes_failure_details(
    db_session: Session,
):
    """R4.AC7/R4.AC9/R5.AC6: status transitions persist enum values and explicit failure data."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    group = service.create_group(db_session, ids["ticket_id"], "implement", 1)
    attempt = service.create_attempt(db_session, group.id, attempt_index=0)

    running = service.update_attempt_status(db_session, attempt.id, "running")
    assert running.started_at is not None
    assert running.finished_at is None

    failed = service.update_attempt_status(
        db_session, attempt.id, "failed", failure_details="pytest failed"
    )
    assert failed.started_at == running.started_at
    assert failed.finished_at is not None
    assert failed.failure_details == "pytest failed"

    failed_again = service.update_attempt_status(db_session, attempt.id, "failed")
    assert failed_again.finished_at == failed.finished_at

    with pytest.raises(ValueError, match="status"):
        service.update_attempt_status(db_session, attempt.id, "not-a-status")

    serialized = service.serialize_group(db_session, group.id)
    assert serialized["attempts"] == [
        {
            "id": attempt.id,
            "attempt_index": 0,
            "attempt_name": "Attempt 1",
            "agent_run_id": None,
            "worktree_id": None,
            "branch": "",
            "status": "failed",
            "started_at": running.started_at.isoformat(),
            "finished_at": failed.finished_at.isoformat(),
            "failure_details": "pytest failed",
        }
    ]


def test_settle_group_validates_winner_and_preserves_decline_failure_fields(
    db_session: Session,
):
    """R4.AC8/R4.AC9/R5.AC7: settlement semantics are group-level, not run-level."""
    service = _fanout_service()
    ids = _seed_ticket_with_workflow(db_session)
    other_ids = _seed_ticket_with_workflow(db_session, suffix="settle-other")
    group = service.create_group(db_session, ids["ticket_id"], "implement", 2)
    winner = service.create_attempt(db_session, group.id, attempt_index=0)
    service.create_attempt(db_session, group.id, attempt_index=1)
    other_group = service.create_group(db_session, other_ids["ticket_id"], "implement", 1)
    other_attempt = service.create_attempt(db_session, other_group.id, attempt_index=0)

    with pytest.raises(ValueError, match="winner"):
        service.settle_group(db_session, group.id, outcome="promoted")
    with pytest.raises(ValueError, match="same group"):
        service.settle_group(
            db_session, group.id, outcome="promoted", winner_attempt_id=other_attempt.id
        )

    promoted = service.settle_group(
        db_session, group.id, outcome="promoted", winner_attempt_id=winner.id
    )
    assert promoted.status.value == "settled"
    serialized = service.serialize_group(db_session, group.id)
    assert serialized["status"] == "settled"
    assert serialized["outcome"] == "promoted"
    assert serialized["winner_attempt_id"] == winner.id
    assert serialized["settled_at"] is not None

    declined = service.settle_group(
        db_session,
        other_group.id,
        outcome="declined",
        declined_reason="all attempts worse than baseline",
        failure_summary="no clean candidate",
    )
    assert declined.winner_attempt_id is None
    declined_serialized = service.serialize_group(db_session, other_group.id)
    assert declined_serialized["status"] == "settled"
    assert declined_serialized["outcome"] == "declined"
    assert declined_serialized["winner_attempt_id"] is None
    assert declined_serialized["declined_reason"] == "all attempts worse than baseline"
    assert declined_serialized["failure_summary"] == "no clean candidate"
