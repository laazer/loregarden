"""`product-tdd` is v3's shape with nothing of loregarden's in it, and loremaker runs on it."""

from __future__ import annotations

import json

import pytest
from loregarden.db.versions.product_tdd_template import (
    STAGES,
    TEMPLATE,
    TRANSITIONS,
    m_product_tdd_template,
)
from loregarden.models.domain import (
    OrchestrationRun,
    Ticket,
    TicketState,
    WorkflowTemplate,
    WorkflowTemplateVersion,
    Workspace,
)
from loregarden.models.domain.schemas import StudioWorkflowStage
from loregarden.services.exit_actions import validate_against_catalog
from loregarden.services.studio_routing import resolve_stage_execution
from loregarden.services.studio_service import seed_builtin_agents
from loregarden.services.studio_workflow_validation import (
    validate_alternative_groups,
    validate_classify_routes_are_selectable,
    validate_has_terminal_stage,
    validate_stage_agent_ids,
    validate_stage_route_targets,
    validate_stage_skill_names,
)
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_orchestration_run
from tests.template_move_helpers import (
    bound_workspace,
    current_stage_after_a_read,
    forget_template,
    instance_keys,
    instance_of,
    pass_stage,
    template,
    ticket_on,
)

OLD = "loregarden-tdd"
KEYS = [stage["key"] for stage in STAGES]


@pytest.fixture(autouse=True)
def _before_the_migration(db_session: Session) -> None:
    forget_template(db_session, TEMPLATE)


@pytest.fixture
def loremaker(db_session: Session) -> Workspace:
    return bound_workspace(db_session, "loremaker", OLD)


def _migrate(isolated_db, session: Session) -> None:
    session.commit()
    with isolated_db.begin() as conn:
        m_product_tdd_template(conn)
    session.expire_all()


# ---- the template --------------------------------------------------------------------


def test_the_template_passes_every_check_a_studio_publish_runs(db_session: Session):
    seed_builtin_agents(db_session)
    stages = [StudioWorkflowStage.model_validate(stage) for stage in STAGES]

    validate_stage_agent_ids(db_session, stages)
    validate_stage_route_targets(stages)
    validate_classify_routes_are_selectable(stages)
    validate_has_terminal_stage(stages)
    validate_stage_skill_names(stages)
    validate_alternative_groups(stages)
    for stage in stages:
        validate_against_catalog(stage.exit_actions)


def test_transitions_name_only_its_stages_and_every_judge_can_reject():
    assert {edge["from"] for edge in TRANSITIONS} | {edge["to"] for edge in TRANSITIONS} == set(
        KEYS
    )
    rejects = {e["from"]: e["to"] for e in TRANSITIONS if e.get("when") == "reject"}
    assert rejects == {
        "test-design": "spec",
        "verify": "implement",
        "review": "implement",
        "gate": "implement",
    }


def test_review_runs_three_lanes_and_nothing_is_loregardens():
    by_key = {stage["key"]: stage for stage in STAGES}
    lanes = [lane["agent_id"] for lane in by_key["review"]["parallel_agents"]]
    assert lanes == ["architecture_reviewer", "static_qa", "security_reviewer"]
    assert by_key["verify"]["required_evidence"] == ["verify_verdict"]

    text = json.dumps(STAGES)
    for loregardens in ("visual_qa", "gdscript_reviewer", "live database", "server/loregarden"):
        assert loregardens not in text


@pytest.mark.parametrize(
    ("title", "agent"),
    [
        ("Frontend: the world list React component shows an empty state", "frontend_implementer"),
        ("Backend API endpoint: return the Django model's permissions", "backend_implementer"),
        ("Reword the billing rules", "backend_implementer"),
    ],
)
def test_implement_routes_ui_work_to_the_frontend_implementer(title: str, agent: str):
    implement = next(stage for stage in STAGES if stage["key"] == "implement")
    chosen, _ = resolve_stage_execution(
        Ticket(external_id="lmkr-x-1", title=title),
        StudioWorkflowStage.model_validate(implement),
    )
    assert chosen == agent


# ---- who moves -----------------------------------------------------------------------


def test_loremaker_moves_and_the_shared_template_is_untouched(
    isolated_db, db_session: Session, loremaker: Workspace
):
    lore_eden = bound_workspace(db_session, "lore-eden", OLD)
    old = template(db_session, OLD)
    before = (old.id, old.version, old.stages_json, old.transitions_json)

    _migrate(isolated_db, db_session)
    _migrate(isolated_db, db_session)

    product = template(db_session, TEMPLATE)
    assert loremaker.workflow_template_id == product.id
    assert lore_eden.workflow_template_id == before[0]
    old = template(db_session, OLD)
    assert (old.id, old.version, old.stages_json, old.transitions_json) == before

    snapshots = db_session.exec(
        select(WorkflowTemplateVersion).where(WorkflowTemplateVersion.template_id == product.id)
    ).all()
    assert [(s.version, s.created_by) for s in snapshots] == [(1, "migration")]
    assert json.loads(json.loads(snapshots[0].snapshot_json)["stages_json"]) == STAGES


def test_a_workspace_on_a_template_someone_chose_is_left_there(isolated_db, db_session: Session):
    workspace = bound_workspace(db_session, "loremaker", "extended-tdd")
    chosen = workspace.workflow_template_id
    ticket = ticket_on(
        db_session, workspace, "backlog", state=TicketState.BACKLOG, stage_key="plan"
    )

    _migrate(isolated_db, db_session)

    assert workspace.workflow_template_id == chosen
    assert instance_of(db_session, ticket).template_id == chosen


def test_never_run_backlog_starts_at_triage_on_the_new_template(
    isolated_db, db_session: Session, loremaker: Workspace
):
    ticket = ticket_on(
        db_session, loremaker, "backlog", state=TicketState.BACKLOG, stage_key="plan"
    )

    _migrate(isolated_db, db_session)

    instance = instance_of(db_session, ticket)
    assert instance.template_id == template(db_session, TEMPLATE).id
    assert (instance.template_version, instance_keys(db_session, ticket)) == (1, KEYS)
    assert ticket.workflow_stage_key == "triage"
    assert current_stage_after_a_read(db_session, ticket) == "triage"
    assert pass_stage(db_session, ticket, "implement") == "verify"


def test_another_workspaces_backlog_on_the_shared_template_does_not_move(
    isolated_db, db_session: Session, loremaker: Workspace
):
    lore_eden = bound_workspace(db_session, "lore-eden", OLD)
    ticket = ticket_on(
        db_session, lore_eden, "backlog", state=TicketState.BACKLOG, stage_key="plan"
    )

    _migrate(isolated_db, db_session)

    assert instance_of(db_session, ticket).template_id == template(db_session, OLD).id


@pytest.mark.parametrize("pinned", [True, False])
@pytest.mark.parametrize(
    ("state", "stage_key", "next_key"),
    [
        (TicketState.IN_PROGRESS, "context", "ui-design"),
        (TicketState.IN_PROGRESS, "testing", "review"),
        (TicketState.BLOCKED, "review", "approval"),
    ],
)
def test_an_in_progress_ticket_keeps_a_valid_current_stage(
    isolated_db,
    db_session: Session,
    loremaker: Workspace,
    state: TicketState,
    stage_key: str,
    next_key: str,
    pinned: bool,
):
    """`context`, `testing` and `approval` do not exist in product-tdd."""
    keys = [stage["key"] for stage in json.loads(template(db_session, OLD).stages_json)]
    done = dict.fromkeys(keys[: keys.index(stage_key)], "done")
    ticket = ticket_on(
        db_session,
        loremaker,
        f"{stage_key}-{pinned}",
        state=state,
        stage_key=stage_key,
        statuses=done,
        pinned=pinned,
    )

    _migrate(isolated_db, db_session)

    assert instance_of(db_session, ticket).template_id == template(db_session, OLD).id
    assert instance_keys(db_session, ticket) == keys
    assert current_stage_after_a_read(db_session, ticket) == stage_key
    assert pass_stage(db_session, ticket, stage_key) == next_key


@pytest.mark.parametrize("how", ["agent run", "orchestration run", "settled stage"])
def test_backlog_something_has_touched_stays(
    isolated_db, db_session: Session, loremaker: Workspace, how: str
):
    ticket = ticket_on(
        db_session,
        loremaker,
        "touched",
        state=TicketState.BACKLOG,
        stage_key="plan",
        statuses={"plan": "done"} if how == "settled stage" else None,
    )
    if how == "agent run":
        make_agent_run(
            db_session, workspace_id=loremaker.id, ticket_id=ticket.id, run_id="run-touched"
        )
    if how == "orchestration run":
        make_orchestration_run(db_session, workspace_id=loremaker.id, ticket_id=ticket.id)

    _migrate(isolated_db, db_session)

    assert instance_of(db_session, ticket).template_id == template(db_session, OLD).id


@pytest.mark.parametrize("state", [TicketState.IN_PROGRESS, TicketState.BLOCKED])
def test_a_started_ticket_that_ran_nothing_itself_stays(
    isolated_db, db_session: Session, loremaker: Workspace, state: TicketState
):
    """A parent is started by its children, so it has no run and no settled stage."""
    ticket = ticket_on(db_session, loremaker, "parent", state=state, stage_key="plan")

    _migrate(isolated_db, db_session)

    assert instance_of(db_session, ticket).template_id == template(db_session, OLD).id


def test_a_database_with_no_workspaces_still_gets_the_template(isolated_db, db_session: Session):
    _migrate(isolated_db, db_session)

    row = db_session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == TEMPLATE)).one()
    assert json.loads(row.transitions_json) == TRANSITIONS
    assert db_session.exec(select(OrchestrationRun)).first() is None
