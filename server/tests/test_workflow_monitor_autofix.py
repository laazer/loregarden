"""The monitor may repair two things, and must never spend an agent run doing it.

Autonomy is configurable per run so a hierarchy can be driven with the monitor
reporting only, or repairing what it safely can. The auto-fixable set is exactly
two conditions and should stay that small: both are idempotent, reversible, and
cost nothing. Clearing a stale retry counter is deliberately absent — re-arming a
circuit breaker without a human is how a ticket reaches 28 attempts at one stage.
"""

import json
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    MonitorCondition,
    MonitorMode,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration_profile import MonitorConfig, OrchestrationProfile
from loregarden.services.workflow_monitor import (
    AUTO_FIXABLE,
    AUTOFIX_REFUSED_TITLE,
    MonitorFinding,
    apply_autofixes,
    scan,
)
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select

GROUP = "impl"
BACKEND = "backend-impl"
FRONTEND = "frontend-impl"


def _stages() -> list[WorkflowStageDef]:
    return [
        WorkflowStageDef(key="plan", name="Plan", order=1, agent_id="planner"),
        WorkflowStageDef(
            key=BACKEND, name="Backend", order=2, optional=True, alternative_group=GROUP
        ),
        WorkflowStageDef(
            key=FRONTEND, name="Frontend", order=3, optional=True, alternative_group=GROUP
        ),
        WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
    ]


def _setup(db_session: Session, external_id: str) -> tuple[Ticket, WorkflowInstance, list]:
    stages = _stages()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    template = WorkflowTemplate(
        slug=f"tpl-{external_id}",
        name=external_id,
        stages_json=json.dumps([s.model_dump() for s in stages]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title=external_id,
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="plan",
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
    db_session.commit()
    db_session.refresh(instance)
    return ticket, instance, stages


def _empty_the_group(db_session: Session, instance: WorkflowInstance, stages) -> None:
    """Write both members WONT_DO directly. skip_stage refuses this (562), which
    is the point — this is the state a ticket pruned before that guard landed,
    or by any other write path, is sitting in."""
    stage_map = parse_stage_map(instance, stages)
    stage_map[BACKEND] = StageStatus.WONT_DO
    stage_map[FRONTEND] = StageStatus.WONT_DO
    from loregarden.services.workflow_state import parse_stage_notes, serialize_stage_map

    instance.stages_json = serialize_stage_map(stage_map, stages, notes=parse_stage_notes(instance))
    db_session.add(instance)
    db_session.commit()


def _profile(mode: MonitorMode, autofix: list[MonitorCondition]) -> OrchestrationProfile:
    return OrchestrationProfile(slug="test", monitor=MonitorConfig(mode=mode, autofix=autofix))


def _with_profile(profile: OrchestrationProfile):
    return patch(
        "loregarden.services.workflow_monitor.resolve_orchestration_profile",
        return_value=profile,
    )


# --- AC1: config validation -------------------------------------------------


def test_a_condition_that_cannot_be_auto_fixed_is_refused_at_load():
    """AC1. An error, not a silent no-op: a config that quietly does nothing
    reads, to whoever wrote it, exactly like one that works."""
    with pytest.raises(ValueError, match="cannot be auto-fixed"):
        MonitorConfig(autofix=[MonitorCondition.STAGE_THRASH])


def test_the_auto_fixable_set_is_exactly_two():
    """A guard on scope. Growing this set is a decision, and it should be made
    deliberately rather than by a passing edit."""
    assert AUTO_FIXABLE == {MonitorCondition.STALE_CURSOR, MonitorCondition.EMPTIED_GROUP}


def test_the_default_mode_is_report():
    assert MonitorConfig().mode is MonitorMode.REPORT
    assert MonitorConfig().autofix == []


# --- AC3 and AC4: mode and the allow-list both gate the repair ---------------


def test_report_mode_mutates_nothing(db_session: Session):
    """AC3."""
    ticket, instance, stages = _setup(db_session, "autofix-report")
    _empty_the_group(db_session, instance, stages)
    before = instance.stages_json

    with _with_profile(_profile(MonitorMode.REPORT, [MonitorCondition.EMPTIED_GROUP])):
        findings = scan(db_session)
        repaired = apply_autofixes(db_session, findings)

    db_session.refresh(instance)
    assert repaired == []
    assert instance.stages_json == before


def test_autofix_mode_repairs_only_the_conditions_it_names(db_session: Session):
    """AC4. Turning the mode on does not opt into every repair."""
    ticket, instance, stages = _setup(db_session, "autofix-narrow")
    _empty_the_group(db_session, instance, stages)
    before = instance.stages_json

    # Autofix mode, but this condition is not in the list.
    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.STALE_CURSOR])):
        repaired = apply_autofixes(db_session, scan(db_session))

    db_session.refresh(instance)
    assert repaired == []
    assert instance.stages_json == before


def test_autofix_restores_the_earliest_member_of_an_emptied_group(db_session: Session):
    ticket, instance, stages = _setup(db_session, "autofix-group")
    _empty_the_group(db_session, instance, stages)

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])):
        repaired = apply_autofixes(db_session, scan(db_session))

    assert [f.condition for f in repaired] == [MonitorCondition.EMPTIED_GROUP]
    db_session.refresh(instance)
    stage_map = parse_stage_map(instance, stages)
    assert stage_map[BACKEND] is StageStatus.PENDING
    assert stage_map[FRONTEND] is StageStatus.WONT_DO


def test_the_restored_stage_carries_a_note_saying_why(db_session: Session):
    """A stage that silently changed status is indistinguishable from one an
    agent moved. The note is what makes the repair auditable."""
    from loregarden.services.workflow_state import parse_stage_notes

    ticket, instance, stages = _setup(db_session, "autofix-note")
    _empty_the_group(db_session, instance, stages)

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])):
        apply_autofixes(db_session, scan(db_session))

    db_session.refresh(instance)
    assert "workflow monitor" in parse_stage_notes(instance)[BACKEND].lower()


# --- AC5: idempotence -------------------------------------------------------


def test_a_second_scan_and_fix_is_a_no_op(db_session: Session):
    """AC5. The sweep runs on the reconcile timer, so a fix that is not
    idempotent is a fix that runs forever."""
    ticket, instance, stages = _setup(db_session, "autofix-idempotent")
    _empty_the_group(db_session, instance, stages)

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])):
        apply_autofixes(db_session, scan(db_session))
        db_session.refresh(instance)
        after_first = instance.stages_json

        second = apply_autofixes(db_session, scan(db_session))

    db_session.refresh(instance)
    assert second == [], "nothing left to repair, so nothing should be repaired"
    assert instance.stages_json == after_first


# --- AC2: per-run override --------------------------------------------------


def test_a_run_override_beats_the_profile(db_session: Session):
    """AC2."""
    ticket, instance, stages = _setup(db_session, "autofix-override")
    _empty_the_group(db_session, instance, stages)
    db_session.add(
        OrchestrationRun(
            run_code="orch-override",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
            monitor_mode=MonitorMode.AUTOFIX,
        )
    )
    db_session.commit()

    # Profile says report; the run says autofix.
    with _with_profile(_profile(MonitorMode.REPORT, [MonitorCondition.EMPTIED_GROUP])):
        repaired = apply_autofixes(db_session, scan(db_session))

    assert [f.condition for f in repaired] == [MonitorCondition.EMPTIED_GROUP]


def test_a_run_with_no_override_defers_to_the_profile(db_session: Session):
    """Null is 'made no choice', not 'report'. A run written before the column
    existed must not be read as having opted out."""
    ticket, instance, stages = _setup(db_session, "autofix-null")
    _empty_the_group(db_session, instance, stages)
    db_session.add(
        OrchestrationRun(
            run_code="orch-null",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
            monitor_mode=None,
        )
    )
    db_session.commit()

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])):
        repaired = apply_autofixes(db_session, scan(db_session))

    assert [f.condition for f in repaired] == [MonitorCondition.EMPTIED_GROUP]


# --- AC6: never dispatches an agent -----------------------------------------


def test_no_autofix_path_dispatches_an_agent_run(db_session: Session):
    """AC6, and the reason this ticket exists inside this milestone.

    The point of the milestone is fewer wasted runs; an auto-fixer that spends
    runs is a loop generator. Asserted at the dispatch seam itself rather than by
    counting AgentRun rows, so a path that reaches dispatch and fails still fails
    this test.
    """
    ticket, instance, stages = _setup(db_session, "autofix-nodispatch")
    # BOTH repairs, not just one: a test that exercises a single fix path proves
    # nothing about the other, and this assertion is the milestone's whole point.
    _empty_the_group(db_session, instance, stages)
    ticket.workflow_stage_key = "a-stage-this-workflow-does-not-have"
    db_session.add(ticket)
    db_session.commit()

    from loregarden.services import orchestration

    with (
        _with_profile(_profile(MonitorMode.AUTOFIX, list(AUTO_FIXABLE))),
        patch.object(
            orchestration.OrchestrationService,
            "start_run",
            side_effect=AssertionError("an auto-fix must never dispatch an agent"),
        ) as dispatch,
    ):
        findings = scan(db_session)
        repaired = apply_autofixes(db_session, findings)

    dispatch.assert_not_called()
    assert {f.condition for f in repaired} == AUTO_FIXABLE, (
        "both repair paths must have run, or this asserts nothing about the other"
    )


# --- AC7: one approval, not one per sweep -----------------------------------


def _pending_refusals(db_session: Session) -> list[Approval]:
    return list(
        db_session.exec(
            select(Approval).where(
                Approval.title == AUTOFIX_REFUSED_TITLE,
                Approval.status == ApprovalStatus.PENDING,
            )
        ).all()
    )


def test_a_refused_fix_raises_exactly_one_approval_across_sweeps(db_session: Session):
    """AC7. The approvals queue is human decisions and holds 395 rows in all of
    history; one row per sweep would bury every one of them."""
    ticket, instance, stages = _setup(db_session, "autofix-refused")
    _empty_the_group(db_session, instance, stages)

    with (
        _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])),
        patch(
            "loregarden.services.workflow_monitor._repair_emptied_group",
            return_value=False,
        ),
    ):
        for _ in range(3):
            apply_autofixes(db_session, scan(db_session))

    assert len(_pending_refusals(db_session)) == 1


def test_a_successful_fix_raises_no_approval(db_session: Session):
    ticket, instance, stages = _setup(db_session, "autofix-clean")
    _empty_the_group(db_session, instance, stages)

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])):
        apply_autofixes(db_session, scan(db_session))

    assert _pending_refusals(db_session) == []


# --- lg-workflow-integrity-678: a repair reports what happened, not its intent ---


def _make_the_cursor_stale(db_session: Session, ticket: Ticket) -> None:
    """Park the ticket on a stage its instance does not have."""
    ticket.workflow_stage_key = "a-stage-this-workflow-never-had"
    db_session.add(ticket)
    db_session.commit()


def test_a_repaired_stale_cursor_is_counted_and_raises_nothing(db_session: Session):
    """The healthy path, so the unrepairable test below is not the only evidence
    that this branch is reachable at all."""
    ticket, _instance, _stages = _setup(db_session, "autofix-cursor-ok")
    _make_the_cursor_stale(db_session, ticket)

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.STALE_CURSOR])):
        repaired = apply_autofixes(db_session, scan(db_session, ticket_id=ticket.id))

    assert [f.condition for f in repaired] == [MonitorCondition.STALE_CURSOR]
    refusals = db_session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).all()
    assert refusals == []


def test_an_unrepairable_stale_cursor_is_not_counted_as_repaired(db_session: Session):
    """AC1/AC2/AC3. `_repair_stale_cursor` used to `return True` unconditionally.

    The unrepairable case is real, not simulated: `reconcile_ticket` resolves
    stages through `resolve_ticket_stages`, which returns nothing for a ticket
    with `workflow_disabled`, so it takes its early return and moves no cursor.
    The detector resolves stages through the instance's own template pin, so it
    still reports the finding. Two resolution paths, one disagreement — and the
    old code called that a repair.
    """
    ticket, _instance, _stages = _setup(db_session, "autofix-cursor-stuck")
    _make_the_cursor_stale(db_session, ticket)
    ticket.workflow_disabled = True
    db_session.add(ticket)
    db_session.commit()

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.STALE_CURSOR])):
        repaired = apply_autofixes(db_session, scan(db_session, ticket_id=ticket.id))

    assert repaired == []
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == "a-stage-this-workflow-never-had"

    # AC2: the finding escalates instead of being silently re-found every sweep.
    refusals = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.title == AUTOFIX_REFUSED_TITLE,
        )
    ).all()
    assert len(refusals) == 1


def test_the_refusal_is_raised_once_not_once_per_sweep(db_session: Session):
    """An unrepairable finding is re-found on every pass. One approval per
    (ticket, condition) is what keeps that from burying the inbox."""
    ticket, _instance, _stages = _setup(db_session, "autofix-cursor-repeat")
    _make_the_cursor_stale(db_session, ticket)
    ticket.workflow_disabled = True
    db_session.add(ticket)
    db_session.commit()

    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.STALE_CURSOR])):
        for _ in range(3):
            apply_autofixes(db_session, scan(db_session, ticket_id=ticket.id))

    refusals = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.title == AUTOFIX_REFUSED_TITLE,
        )
    ).all()
    assert len(refusals) == 1


def test_an_emptied_group_repair_that_changes_nothing_is_not_counted(db_session: Session):
    """AC4, the other auto-fixable condition. `_repair_emptied_group` returns
    early when the named group has no members, and used to report success on the
    way out."""
    ticket, instance, stages = _setup(db_session, "autofix-group-missing")
    _empty_the_group(db_session, instance, stages)

    phantom = MonitorFinding(
        condition=MonitorCondition.EMPTIED_GROUP,
        ticket_id=ticket.id,
        stage_key="",
        summary="a group this workflow does not have",
        evidence={"group": "no-such-group"},
    )
    with _with_profile(_profile(MonitorMode.AUTOFIX, [MonitorCondition.EMPTIED_GROUP])):
        repaired = apply_autofixes(db_session, [phantom])

    assert repaired == []
    refusals = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.title == AUTOFIX_REFUSED_TITLE,
        )
    ).all()
    assert len(refusals) == 1
