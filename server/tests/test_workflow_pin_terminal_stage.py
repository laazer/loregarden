"""A workflow pin may not freeze a ticket on a version that cannot finish.

Two halves of the same defect: the guard that refuses such a pin at the write
(`db.workflow_pin_guard`), and migration 0088, which moves the instances that
were already pinned that way before the guard existed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from loregarden.db.migrations_templates import m_repin_terminal_less_instances
from loregarden.db.workflow_pin_guard import WorkflowPinWithoutTerminalStageError
from loregarden.models.domain import (
    Ticket,
    WorkflowInstance,
    WorkflowTemplate,
    WorkflowTemplateVersion,
    WorkItemType,
    Workspace,
)
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

# The pre-fix shape: studio-loregarden-tdd-v3 v9 ended at `gate`, so a passing
# gate had nowhere to advance to.
_STAGES_NO_TERMINAL = [
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 1},
    {"key": "gate", "name": "Gate", "stage_type": "gate", "order": 2},
]
_DONE_STAGE = {"key": "done", "name": "Done", "order": 3, "terminal": True}
_STAGES_WITH_TERMINAL = [*_STAGES_NO_TERMINAL, _DONE_STAGE]
# A later version that also changed a stage definition — not a safe repin target.
_STAGES_DIVERGED = [
    {"key": "implement", "name": "Implement", "agent_id": "frontend_implementer", "order": 1},
    {"key": "gate", "name": "Gate", "stage_type": "gate", "order": 2},
    _DONE_STAGE,
]


@pytest.fixture(name="db")
def db_fixture(tmp_path):
    """A current-schema database — the shape both the guard and a migration see."""
    engine = create_engine(f"sqlite:///{tmp_path / 'pins.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _template(session: Session, *, slug: str, version: int, stages: list[dict]) -> WorkflowTemplate:
    template = WorkflowTemplate(
        id=str(uuid4()),
        slug=slug,
        name=slug,
        stages_json=json.dumps(stages),
        transitions_json="[]",
        version=version,
    )
    session.add(template)
    session.commit()
    return template


def _snapshot(session: Session, template: WorkflowTemplate, version: int, stages: list[dict]):
    session.add(
        WorkflowTemplateVersion(
            id=str(uuid4()),
            template_id=template.id,
            version=version,
            snapshot_json=json.dumps(
                {
                    "slug": template.slug,
                    "name": template.name,
                    "description": "",
                    "stages_json": json.dumps(stages),
                    "transitions_json": "[]",
                    "source_path": "",
                    "built_in": False,
                }
            ),
            created_by="test",
            change_note="",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()


def _ticket(session: Session, external_id: str) -> Ticket:
    workspace = session.exec(select(Workspace).where(Workspace.slug == "pin-ws")).first()
    if workspace is None:
        workspace = Workspace(id=str(uuid4()), slug="pin-ws", name="Pin WS", repo_path=".")
        session.add(workspace)
        session.commit()
    ticket = Ticket(
        id=str(uuid4()),
        external_id=external_id,
        workspace_id=workspace.id,
        title=external_id,
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _instance(session: Session, ticket: Ticket, template: WorkflowTemplate, version: int | None):
    return WorkflowInstance(
        id=str(uuid4()),
        ticket_id=ticket.id,
        template_id=template.id,
        template_version=version,
        current_stage_key="implement",
        stages_json=json.dumps([{"key": "implement", "status": "pending"}]),
    )


def _pinned_versions(engine) -> dict[str, int | None]:
    with Session(engine) as session:
        return {
            instance.ticket_id: instance.template_version
            for instance in session.exec(select(WorkflowInstance)).all()
        }


def _write_terminal_less_pin(engine, template_id: str, ticket_id: str, version: int):
    """Insert a bad pin the way history did — raw SQL, bypassing the ORM guard."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_instances "
                "(id, ticket_id, template_id, template_version, current_stage_key, stages_json, "
                "created_at, updated_at) "
                "VALUES (:id, :tid, :tpl, :v, 'gate', '[]', :now, :now)"
            ),
            {
                "id": str(uuid4()),
                "tid": ticket_id,
                "tpl": template_id,
                "v": version,
                "now": datetime.now(timezone.utc),
            },
        )


# ---- The guard ------------------------------------------------------------------


def test_pin_to_terminal_less_version_is_refused(db):
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_WITH_TERMINAL)
        ticket = _ticket(session, "pin-bad")

        session.add(_instance(session, ticket, template, 1))
        with pytest.raises(WorkflowPinWithoutTerminalStageError) as excinfo:
            session.commit()
        assert "no terminal stage" in str(excinfo.value)


def test_repinning_an_existing_instance_onto_a_terminal_less_version_is_refused(db):
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_WITH_TERMINAL)
        ticket = _ticket(session, "pin-repin")
        instance = _instance(session, ticket, template, 2)
        session.add(instance)
        session.commit()

        instance.template_version = 1
        session.add(instance)
        with pytest.raises(WorkflowPinWithoutTerminalStageError):
            session.commit()


def test_good_pin_and_ordinary_updates_pass(db):
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_WITH_TERMINAL)
        ticket = _ticket(session, "pin-good")
        instance = _instance(session, ticket, template, 2)
        session.add(instance)
        session.commit()

        # Advancing the stage cursor is not a pin write and must not be checked.
        instance.current_stage_key = "gate"
        session.add(instance)
        session.commit()
        assert instance.template_version == 2


def test_unpinned_instance_is_allowed(db):
    """A null pin follows the live template, which create/publish already guards."""
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        ticket = _ticket(session, "pin-null")
        session.add(_instance(session, ticket, template, None))
        session.commit()
        assert _pinned_versions(db) == {ticket.id: None}


def test_template_with_no_terminal_stage_of_its_own_is_not_the_pins_business(db):
    """Aggregator parents legitimately run terminal-less workflows (subtree_auto_run
    finalizes them directly). The pin only refuses *losing* an exit the template has."""
    with Session(db) as session:
        template = _template(session, slug="flat", version=1, stages=_STAGES_NO_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        ticket = _ticket(session, "pin-flat")
        session.add(_instance(session, ticket, template, 1))
        session.commit()
        assert _pinned_versions(db) == {ticket.id: 1}


# ---- Migration 0088 -------------------------------------------------------------


def _migrate(engine) -> None:
    with engine.begin() as conn:
        m_repin_terminal_less_instances(conn)


def test_migration_repins_terminal_less_instances(db):
    with Session(db) as session:
        template = _template(session, slug="tdd", version=3, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_WITH_TERMINAL)
        _snapshot(session, template, 3, _STAGES_WITH_TERMINAL)
        stranded = _ticket(session, "mig-stranded")
        healthy = _ticket(session, "mig-healthy")
        stranded_id, healthy_id, template_id = stranded.id, healthy.id, template.id
        session.add(_instance(session, healthy, template, 3))
        session.commit()
    _write_terminal_less_pin(db, template_id, stranded_id, 1)

    _migrate(db)

    pins = _pinned_versions(db)
    assert pins[stranded_id] == 2, "repinned to the lowest version that only added the exit"
    assert pins[healthy_id] == 3, "a correctly pinned instance is untouched"


def test_migration_is_idempotent(db):
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_WITH_TERMINAL)
        ticket_id, template_id = _ticket(session, "mig-idem").id, template.id
    _write_terminal_less_pin(db, template_id, ticket_id, 1)

    _migrate(db)
    once = _pinned_versions(db)
    _migrate(db)
    assert _pinned_versions(db) == once == {ticket_id: 2}


def test_migration_leaves_a_pin_with_no_safe_successor_alone(db):
    """Version 2 both added the exit and swapped an agent. Moving the ticket there
    would change the workflow it is running, which is what pinning exists to prevent."""
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_DIVERGED)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_DIVERGED)
        ticket_id, template_id = _ticket(session, "mig-diverged").id, template.id
    _write_terminal_less_pin(db, template_id, ticket_id, 1)

    _migrate(db)

    assert _pinned_versions(db) == {ticket_id: 1}


def test_migration_does_not_rewrite_version_snapshots(db):
    """The remedy is a repin, not a backfill: applied snapshots stay immutable."""
    with Session(db) as session:
        template = _template(session, slug="tdd", version=2, stages=_STAGES_WITH_TERMINAL)
        _snapshot(session, template, 1, _STAGES_NO_TERMINAL)
        _snapshot(session, template, 2, _STAGES_WITH_TERMINAL)
        ticket_id, template_id = _ticket(session, "mig-immutable").id, template.id
        before = session.exec(
            select(WorkflowTemplateVersion).where(WorkflowTemplateVersion.version == 1)
        ).first()
        before_snapshot = before.snapshot_json
    _write_terminal_less_pin(db, template_id, ticket_id, 1)

    _migrate(db)

    with Session(db) as session:
        after = session.exec(
            select(WorkflowTemplateVersion).where(WorkflowTemplateVersion.version == 1)
        ).first()
        assert after.snapshot_json == before_snapshot
