"""Migration 0089: move pins off a version that names a skill nothing resolves.

0088 moved instances off the terminal-less version 9 onto the minimal successor
that only added an exit — version 10, which still names the phantom `verify`
skill. This is the orthogonal second repair: the same minimality discipline,
applied to the skill names, resolved against the live registry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from loregarden.db.migrations_templates import m_repin_unregistered_skill_instances
from loregarden.models.domain import (
    Skill,
    Ticket,
    WorkflowInstance,
    WorkflowTemplate,
    WorkflowTemplateVersion,
    WorkItemType,
    Workspace,
)
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

_DONE_STAGE = {"key": "done", "name": "Done", "order": 3, "terminal": True}


def _stages(verify_skill: str, *, agent_id: str = "verifier", terminal: bool = True) -> list[dict]:
    """The live shape: an implement stage, a verify stage naming a skill, an exit."""
    stages = [
        {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 1},
        {
            "key": "verify",
            "name": "Verify",
            "agent_id": agent_id,
            "skill_name": verify_skill,
            "order": 2,
        },
    ]
    if terminal:
        stages.append(_DONE_STAGE)
    return stages


@pytest.fixture(name="db")
def db_fixture(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skillpins.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db, *, live_version: int, snapshots: dict[int, list[dict]], pin: int) -> str:
    """A template at `live_version`, its version snapshots, and one pinned instance."""
    with Session(db) as session:
        template = WorkflowTemplate(
            id=str(uuid4()),
            slug="tdd",
            name="tdd",
            stages_json=json.dumps(snapshots[live_version]),
            transitions_json="[]",
            version=live_version,
        )
        session.add(template)
        workspace = Workspace(id=str(uuid4()), slug="skill-ws", name="Skill WS", repo_path=".")
        session.add(workspace)
        session.commit()
        for version, stages in snapshots.items():
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
        ticket = Ticket(
            id=str(uuid4()),
            external_id="skill-pin",
            workspace_id=workspace.id,
            title="skill-pin",
            work_item_type=WorkItemType.TASK,
        )
        session.add(ticket)
        session.commit()
        ticket_id, template_id = ticket.id, template.id
    # Raw SQL, the way history wrote these rows: the terminal-stage pin guard
    # postdates them, and one case here pins to a version it would refuse.
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_instances "
                "(id, ticket_id, template_id, template_version, current_stage_key, stages_json, "
                "created_at, updated_at) "
                "VALUES (:id, :tid, :tpl, :v, 'verify', '[]', :now, :now)"
            ),
            {
                "id": str(uuid4()),
                "tid": ticket_id,
                "tpl": template_id,
                "v": pin,
                "now": datetime.now(timezone.utc),
            },
        )
    return ticket_id


def _add_skill(db, slug: str) -> None:
    with Session(db) as session:
        session.add(
            Skill(
                id=str(uuid4()),
                slug=slug,
                name=slug,
                description="",
                body="body",
                required_capabilities_json="[]",
                version=1,
                built_in=False,
            )
        )
        session.commit()


def _pin(db) -> int | None:
    with Session(db) as session:
        return session.exec(select(WorkflowInstance)).one().template_version


def _migrate(db) -> None:
    with db.begin() as conn:
        m_repin_unregistered_skill_instances(conn)


def test_pin_naming_an_unregistered_skill_is_repinned(db):
    """`verify` has never been a skill, so the pinned verify stage dies at dispatch."""
    _seed(
        db,
        live_version=3,
        snapshots={1: _stages("verify"), 2: _stages(""), 3: _stages("")},
        pin=1,
    )

    _migrate(db)

    assert _pin(db) == 2, "the lowest successor that only cleared the phantom name"


def test_repin_composes_with_the_terminal_stage_rule(db):
    """An instance stranded on both defects at once: no exit *and* a phantom skill."""
    _seed(
        db,
        live_version=2,
        snapshots={1: _stages("verify", terminal=False), 2: _stages("")},
        pin=1,
    )

    _migrate(db)

    assert _pin(db) == 2


def test_successor_that_changes_anything_else_is_refused(db):
    """Version 2 also swapped the agent. Moving there changes the workflow the
    ticket is running, which is the whole point of a pin."""
    _seed(
        db,
        live_version=2,
        snapshots={1: _stages("verify"), 2: _stages("", agent_id="other_verifier")},
        pin=1,
    )

    _migrate(db)

    assert _pin(db) == 1


def test_successor_naming_another_unregistered_skill_is_refused(db):
    """Clearing one phantom for another resolves nothing."""
    _seed(
        db,
        live_version=2,
        snapshots={1: _stages("verify"), 2: _stages("static_qa")},
        pin=1,
    )

    _migrate(db)

    assert _pin(db) == 1


def test_correctly_pinned_instance_is_untouched(db):
    _seed(db, live_version=2, snapshots={1: _stages(""), 2: _stages("")}, pin=1)

    _migrate(db)

    assert _pin(db) == 1


def test_registered_skill_is_resolved_against_the_registry_not_a_name_list(db):
    """A skill that exists only as a `skills` row — no seed directory, and no
    entry in 0068's frozen phantom list — is registered, so nothing is repinned."""
    _add_skill(db, "house-style")
    _seed(
        db,
        live_version=2,
        snapshots={1: _stages("house-style"), 2: _stages("")},
        pin=1,
    )

    _migrate(db)

    assert _pin(db) == 1


def test_the_same_pin_moves_when_the_registry_does_not_know_the_skill(db):
    """The control for the test above: identical snapshots, no `skills` row, and
    now the pin does move — so that test is measuring the registry, not inertia."""
    _seed(
        db,
        live_version=2,
        snapshots={1: _stages("house-style"), 2: _stages("")},
        pin=1,
    )

    _migrate(db)

    assert _pin(db) == 2


def test_migration_is_idempotent(db):
    _seed(
        db,
        live_version=3,
        snapshots={1: _stages("verify"), 2: _stages(""), 3: _stages("")},
        pin=1,
    )

    _migrate(db)
    once = _pin(db)
    _migrate(db)

    assert _pin(db) == once == 2


def test_migration_does_not_rewrite_version_snapshots(db):
    """The remedy is a repin, not a backfill: applied snapshots stay immutable."""
    _seed(
        db,
        live_version=2,
        snapshots={1: _stages("verify"), 2: _stages("")},
        pin=1,
    )
    with Session(db) as session:
        before = {v.version: v.snapshot_json for v in session.exec(select(WorkflowTemplateVersion))}

    _migrate(db)

    with Session(db) as session:
        after = {v.version: v.snapshot_json for v in session.exec(select(WorkflowTemplateVersion))}
    assert after == before
