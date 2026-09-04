"""Alternative stage groups: at least one member of a group must run.

`studio-loregarden-tdd-v2` marks `backend-impl` and `frontend-impl` optional so a
backend-only ticket can prune the frontend stage. They are also that template's
ONLY optional stages, and `_derive_ticket_state` filters optional stages out of
the required set entirely — so a run that pruned both left every required stage
resolved and derived DONE having implemented nothing.

`alternative_group` is the smallest thing that states the missing constraint. The
invariant is only "at least one member ends non-WONT_DO"; there is no exclusivity,
because templates already expect both members to run on a full-stack ticket.
"""

import json
from datetime import datetime, timezone

import pytest
from loregarden.core.stage_groups import emptied_groups, group_would_be_emptied
from loregarden.db.migrations_templates import m_alternative_impl_group
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.studio_service import StudioService
from loregarden.services.workflow_state import (
    initial_stages_json,
    parse_stage_map,
    reconcile_workflow_state,
    set_stage_status,
)
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

GROUP = "impl"
BACKEND = "backend-impl"
FRONTEND = "frontend-impl"


def _stages() -> list[WorkflowStageDef]:
    """A minimal v2 shape: one group of two, required stages either side."""
    return [
        WorkflowStageDef(key="plan", name="Plan", order=1, agent_id="planner"),
        WorkflowStageDef(
            key=BACKEND,
            name="Backend",
            order=2,
            agent_id="backend_implementer",
            optional=True,
            alternative_group=GROUP,
        ),
        WorkflowStageDef(
            key=FRONTEND,
            name="Frontend",
            order=3,
            agent_id="frontend_implementer",
            optional=True,
            alternative_group=GROUP,
        ),
        WorkflowStageDef(key="gate", name="Gate", order=4, agent_id="gatekeeper"),
        WorkflowStageDef(key="done", name="Done", order=5, terminal=True),
    ]


def _setup(db_session: Session, *, external_id: str):
    stages = _stages()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    template = WorkflowTemplate(
        slug=f"grouped-{external_id}",
        name="Grouped",
        stages_json=json.dumps([stage.model_dump() for stage in stages]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title="Alternative group test",
        description="Exercise alternative stage groups",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="plan",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="plan",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    orch_run = OrchestrationRun(
        run_code=f"orch_{external_id}",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="plan",
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch_run)
    db_session.commit()

    set_stage_status(ticket, instance, stages, "plan", StageStatus.RUNNING)
    db_session.add_all([ticket, instance])
    db_session.commit()
    return ticket, instance, stages, orch_run


def _stage_map(db_session: Session, ticket: Ticket, stages):
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    return parse_stage_map(instance, stages)


# --- the predicates, on their own ------------------------------------------


def test_a_sibling_that_has_not_run_yet_still_counts_as_a_survivor():
    """PENDING is a survivor, not an absence.

    The refusal is about not removing the last *candidate*. Requiring a sibling
    to have already passed would refuse the first prune of every group, since at
    that point nothing in the group has run.
    """
    stages = _stages()
    stage_map = {s.key: StageStatus.PENDING for s in stages}
    assert group_would_be_emptied(stages, stage_map, BACKEND) == ""


def test_the_last_unpruned_member_would_empty_the_group():
    stages = _stages()
    stage_map = {s.key: StageStatus.PENDING for s in stages}
    stage_map[FRONTEND] = StageStatus.WONT_DO
    assert group_would_be_emptied(stages, stage_map, BACKEND) == GROUP


def test_a_stage_in_no_group_is_never_blocked_by_this():
    stages = _stages()
    stage_map = {s.key: StageStatus.PENDING for s in stages}
    assert group_would_be_emptied(stages, stage_map, "gate") == ""


def test_a_member_that_ran_keeps_the_group_alive():
    """DONE is not WONT_DO, so pruning the other member is fine."""
    stages = _stages()
    stage_map = {s.key: StageStatus.PENDING for s in stages}
    stage_map[BACKEND] = StageStatus.DONE
    assert group_would_be_emptied(stages, stage_map, FRONTEND) == ""
    assert emptied_groups(stages, stage_map) == []


# --- AC2 and AC6: the refusal ----------------------------------------------


def test_pruning_one_member_is_allowed_and_the_second_is_refused(db_session: Session):
    """AC2 and AC6, in the order a run would hit them."""
    ticket, _instance, stages, orch_run = _setup(db_session, external_id="group-refuse")
    svc = OrchestrationCallbackService(db_session)

    svc.skip_stage(orch_run, ticket, stage_key=FRONTEND, reason="Backend-only ticket")
    assert _stage_map(db_session, ticket, stages)[FRONTEND] == StageStatus.WONT_DO

    with pytest.raises(ValueError) as excinfo:
        svc.skip_stage(orch_run, ticket, stage_key=BACKEND, reason="Also not needed")

    message = str(excinfo.value)
    assert GROUP in message
    assert FRONTEND in message, "the refusal must name the sibling that has to run"
    # And the refusal is a refusal: the stage is untouched, not half-pruned.
    assert _stage_map(db_session, ticket, stages)[BACKEND] == StageStatus.PENDING


def test_the_refusal_does_not_fire_when_a_sibling_already_ran(db_session: Session):
    """The guard must not become "you may only ever prune one member"."""
    ticket, instance, stages, orch_run = _setup(db_session, external_id="group-ran")
    set_stage_status(ticket, instance, stages, BACKEND, StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()

    OrchestrationCallbackService(db_session).skip_stage(
        orch_run, ticket, stage_key=FRONTEND, reason="No UI change"
    )
    assert _stage_map(db_session, ticket, stages)[FRONTEND] == StageStatus.WONT_DO


# --- AC3: derive is the backstop -------------------------------------------


def test_a_ticket_whose_group_is_empty_does_not_derive_done(db_session: Session):
    """AC3. The guard above is the decision; this is the recomputation.

    Written by setting WONT_DO directly rather than through skip_stage, because
    skip_stage now refuses — which is the point. This asserts the state a ticket
    pruned before the guard existed, or by any other write path, resolves to.

    The prunes come first because that is the order a run produces them: an
    agent prunes stages ahead of it, then the workflow finishes. Completing the
    required stages first would derive a genuine DONE while both members were
    still PENDING, and `reconcile_workflow_state`'s sticky-done rule then
    refuses to un-finish the ticket — correctly, and for reasons that have
    nothing to do with groups.
    """
    ticket, instance, stages, _orch = _setup(db_session, external_id="group-derive")
    for key in (BACKEND, FRONTEND):
        set_stage_status(ticket, instance, stages, key, StageStatus.WONT_DO)
    for key in ("plan", "gate", "done"):
        set_stage_status(ticket, instance, stages, key, StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()

    reconcile_workflow_state(ticket, instance, stages)
    assert ticket.state == TicketState.IN_PROGRESS


def test_the_same_ticket_derives_done_once_one_member_runs(db_session: Session):
    """The control for the test above: without the emptied group, it IS done.

    Without this, a bug that made `_derive_ticket_state` never return DONE would
    pass the assertion above for entirely the wrong reason.
    """
    ticket, instance, stages, _orch = _setup(db_session, external_id="group-derive-ok")
    set_stage_status(ticket, instance, stages, FRONTEND, StageStatus.WONT_DO)
    for key in ("plan", BACKEND, "gate", "done"):
        set_stage_status(ticket, instance, stages, key, StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()

    reconcile_workflow_state(ticket, instance, stages)
    assert ticket.state == TicketState.DONE


# --- AC4: publish validation ------------------------------------------------


def _publish(db_session: Session, slug: str, stages: list[StudioWorkflowStage]) -> None:
    svc = StudioService(db_session)
    svc.create_workflow(StudioWorkflowCreate(slug=slug, name=slug, stages=stages))
    svc.publish_workflow(slug)


def test_publish_rejects_a_group_containing_the_terminal_stage(db_session: Session):
    with pytest.raises(ValueError, match="terminal stage"):
        _publish(
            db_session,
            "group-terminal",
            [
                StudioWorkflowStage(
                    key="work", name="Work", order=1, optional=True, alternative_group=GROUP
                ),
                StudioWorkflowStage(
                    key="done", name="Done", order=2, terminal=True, alternative_group=GROUP
                ),
            ],
        )


def test_publish_rejects_a_group_with_a_required_member(db_session: Session):
    with pytest.raises(ValueError, match="required stage"):
        _publish(
            db_session,
            "group-required",
            [
                StudioWorkflowStage(
                    key="a", name="A", order=1, optional=True, alternative_group=GROUP
                ),
                StudioWorkflowStage(key="b", name="B", order=2, alternative_group=GROUP),
                StudioWorkflowStage(key="done", name="Done", order=3, terminal=True),
            ],
        )


def test_publish_allows_a_one_member_group(db_session: Session):
    """An author mid-edit has exactly this, and it enforces correctly meanwhile."""
    _publish(
        db_session,
        "group-single",
        [
            StudioWorkflowStage(key="a", name="A", order=1, optional=True, alternative_group=GROUP),
            StudioWorkflowStage(key="done", name="Done", order=2, terminal=True),
        ],
    )
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-group-single")
    ).one()
    stages = [WorkflowStageDef(**s) for s in json.loads(template.stages_json)]
    assert next(s for s in stages if s.key == "a").alternative_group == GROUP


# --- AC5: the live template -------------------------------------------------


def _insert_template(engine, slug: str, stages: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, source_path, "
                "version, built_in, created_at) "
                "VALUES (:id, :slug, :name, '', :st, '[]', :sp, 1, 1, :now)"
            ),
            {
                "id": slug,
                "slug": slug,
                "name": slug,
                "st": json.dumps(stages),
                "sp": f"studio:{slug}",
                "now": now,
            },
        )


def _v2_shape() -> list[dict]:
    """The live `studio-loregarden-tdd-v2` stage keys and optional flags."""
    return [
        {"key": "plan", "name": "Plan", "order": 1},
        {"key": "spec", "name": "Spec", "order": 2},
        {"key": "test-design", "name": "Design Tests", "order": 3},
        {"key": "test-break", "name": "Break Tests", "order": 4},
        {"key": BACKEND, "name": "Backend", "order": 5, "optional": True},
        {"key": FRONTEND, "name": "Frontend", "order": 6, "optional": True},
        {"key": "qa", "name": "QA", "order": 7},
        {"key": "gate", "name": "Gate", "order": 8},
        {"key": "done", "name": "Done", "order": 9, "terminal": True},
    ]


def _stages_of(engine, slug: str) -> dict[str, dict]:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT stages_json FROM workflow_templates WHERE slug=:s"), {"s": slug}
        ).scalar_one()
    return {stage["key"]: stage for stage in json.loads(row)}


def test_the_migration_groups_v2s_two_implementation_stages(tmp_path):
    """AC5. The two stages that made this ticket necessary."""
    engine = create_engine(f"sqlite:///{tmp_path / 'tpl.db'}")
    SQLModel.metadata.create_all(engine)
    _insert_template(engine, "studio-loregarden-tdd-v2", _v2_shape())

    with engine.begin() as conn:
        m_alternative_impl_group(conn)

    stages = _stages_of(engine, "studio-loregarden-tdd-v2")
    assert stages[BACKEND]["alternative_group"] == GROUP
    assert stages[FRONTEND]["alternative_group"] == GROUP
    # Nothing else is touched: only an author knows which optional stages are
    # alternatives, so the migration is keyed by slug, not inferred from
    # `optional`.
    assert [k for k, v in stages.items() if v.get("alternative_group")] == [BACKEND, FRONTEND]


def test_the_migration_leaves_other_templates_alone(tmp_path):
    """The control for the test above.

    v3's `ui-design` is optional and is nobody's alternative. Inferring groups
    from `optional` would bind it to something, which is why the migration is
    keyed by slug — and this is what proves it is.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'tpl.db'}")
    SQLModel.metadata.create_all(engine)
    _insert_template(
        engine,
        "studio-loregarden-tdd-v3",
        [
            {"key": "ui-design", "name": "UI Design", "order": 1, "optional": True},
            {"key": "implement", "name": "Implement", "order": 2},
            {"key": "done", "name": "Done", "order": 3, "terminal": True},
        ],
    )

    with engine.begin() as conn:
        m_alternative_impl_group(conn)

    stages = _stages_of(engine, "studio-loregarden-tdd-v3")
    assert [k for k, v in stages.items() if v.get("alternative_group")] == []


def test_the_migration_is_idempotent(tmp_path):
    """Migrations bump the template version on every change; a second run must
    find nothing to do rather than snapshotting an identical version again."""
    engine = create_engine(f"sqlite:///{tmp_path / 'tpl.db'}")
    SQLModel.metadata.create_all(engine)
    _insert_template(engine, "studio-loregarden-tdd-v2", _v2_shape())

    def _version() -> int:
        with engine.begin() as conn:
            return conn.execute(
                text("SELECT version FROM workflow_templates WHERE slug=:s"),
                {"s": "studio-loregarden-tdd-v2"},
            ).scalar_one()

    with engine.begin() as conn:
        m_alternative_impl_group(conn)
    after_first = _version()
    with engine.begin() as conn:
        m_alternative_impl_group(conn)
    assert _version() == after_first
