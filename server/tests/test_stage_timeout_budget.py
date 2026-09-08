"""A stage gets a budget sized for the work it does.

`lg-workflow-integrity-686`. Every stage shared one run-wide timeout, so the
budget that suited `triage` was the budget `implement` got. Measured across 1049
succeeded runs: `triage` has a median of 83s, `implement` a p90 of 1989s and a
max of 10801s — and at the 600s default, 35% of `implement` runs that DID
succeed would have been killed.

The sizing came from runs that SUCCEEDED, deliberately. Reading a cap off the
runs that timed out would set it just above the worst failure and no higher,
which is survivorship bias with a number attached.
"""

from __future__ import annotations

import json

from loregarden.models.domain import WorkflowStageDef
from loregarden.models.domain.schemas import StudioWorkflowStage
from loregarden.services.builtin_orchestrator import (
    STAGE_TIMEOUT_BUDGETS,
    stage_timeout_seconds,
)


def test_an_unbudgeted_stage_inherits_the_runs():
    """A stage with no entry in the table behaves exactly as it did."""
    stage = WorkflowStageDef(key="triage", name="Triage")
    assert stage_timeout_seconds(stage, 600) == 600
    assert stage_timeout_seconds(stage, None) is None


def test_a_stage_with_a_budget_uses_its_own():
    stage = WorkflowStageDef(key="implement", name="Implement", timeout_seconds=2400)
    assert stage_timeout_seconds(stage, 600) == 2400
    assert stage_timeout_seconds(stage, None) == 2400


def test_the_budget_is_still_a_wall_clock_bound():
    """AC3. Two of the recorded timeouts produced no output at all, and a hard cap
    is exactly what catches those. This changes the size of the bound, never
    removes it — a stage can never resolve to 'no limit' when the run has one."""
    stage = WorkflowStageDef(key="implement", name="Implement", timeout_seconds=2400)
    assert stage_timeout_seconds(stage, 600) is not None


def test_both_stage_models_carry_the_field():
    """AC4. `WorkflowStageDef` and `StudioWorkflowStage` drift silently otherwise,
    and a field on one but not the other is dropped on publish — the failure
    lg-workflow-integrity-559 made structurally impossible and pins reflectively.
    Asserted here too so this ticket's own field is covered by name."""
    assert "timeout_seconds" in WorkflowStageDef.model_fields
    assert "timeout_seconds" in StudioWorkflowStage.model_fields


def test_a_heavy_stage_gets_its_floor_without_declaring_one():
    """The point of the ticket: `implement` under a 600s run no longer inherits
    a budget sized for `triage`."""
    stage = WorkflowStageDef(key="implement", name="Implement")
    assert stage_timeout_seconds(stage, 600) == 2400


def test_a_light_stage_is_left_alone():
    stage = WorkflowStageDef(key="ui-design", name="UI")
    assert stage_timeout_seconds(stage, 600) == 600


def test_the_floor_never_tightens_a_larger_run_budget():
    """An operator who raised the run budget has more context than this table."""
    stage = WorkflowStageDef(key="implement", name="Implement")
    assert stage_timeout_seconds(stage, 9000) == 9000


def test_a_run_with_no_bound_does_not_gain_one():
    """The budget resizes a bound, it does not introduce one — otherwise this
    silently caps runs that were deliberately unlimited."""
    stage = WorkflowStageDef(key="implement", name="Implement")
    assert stage_timeout_seconds(stage, None) is None


def test_no_template_row_is_rewritten_to_carry_these():
    """Regression pin. Written as a migration first, this rewrote 6 templates —
    all `built_in=0` operator-authored rows — and bumped a version that pins
    refer to. Resolution belongs at dispatch, so no migration writes budgets."""
    import loregarden.db.migration_ids as ids

    assert not [i for i in ids.SHIPPED_MIGRATION_IDS if "stage_timeout" in i]


def test_the_table_holds_only_canonical_stage_keys():
    """Migration 0114 renames the forked keys, so a `test_design` entry here
    would be a stale fork mapping of the kind lg-workflow-integrity-660 deleted."""
    forks = {"implementation", "test_design", "test_break", "planning", "specification"}
    assert not (set(STAGE_TIMEOUT_BUDGETS) & forks)


def test_every_budgeted_stage_gets_more_than_the_default_it_replaces():
    """A budget below the run-wide default would silently TIGHTEN the bound on a
    heavy stage — the opposite of the point."""
    assert all(seconds > 600 for seconds in STAGE_TIMEOUT_BUDGETS.values())


def test_the_orchestrator_dispatches_a_stage_with_its_own_budget(db_session, monkeypatch):
    """The wiring. A resolver nothing calls is a unit test passing over a feature
    that does not exist, so this asserts the value reaching dispatch — the run-wide
    budget for a stage that declares none, the stage's own where it does.
    """
    from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
    from loregarden.models.domain import (
        Ticket,
        TicketState,
        WorkflowInstance,
        WorkflowTemplate,
        WorkItemType,
        Workspace,
    )
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
    from loregarden.services.orchestration_profile import OrchestrationProfile
    from loregarden.services.workflow_state import initial_stages_json
    from sqlmodel import select

    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    stages = get_template_stages(template)
    # Give the first stage a budget the run-wide default would not grant.
    stages[0].timeout_seconds = 2400
    template.stages_json = json.dumps([s.model_dump() for s in stages])
    db_session.add(template)
    db_session.commit()

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = Ticket(
        external_id="tb-1",
        workspace_id=ws.id,
        title="budget",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stages[0].key,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=stages[0].key,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()

    seen: dict = {}

    def capture(self, ticket_, orch_run, stage_def, target_key, **kwargs):
        seen["timeout"] = kwargs.get("timeout_seconds")
        seen["stage"] = target_key
        return True  # stop the loop; the dispatched value is all this asserts

    monkeypatch.setattr(BuiltinOrchestrator, "_dispatch_agent_stage", capture)
    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="test"), max_stages=1, timeout_seconds=600
    )

    assert seen["stage"] == stages[0].key
    assert seen["timeout"] == 2400, "the stage's own budget must reach dispatch, not the run's 600s"
