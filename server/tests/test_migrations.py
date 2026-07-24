from loregarden.db.migrations import MIGRATIONS, apply_migrations
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def test_fresh_db_records_all_migrations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    # A fully-current schema created by SQLModel — migrations should still be
    # recorded (their guarded ALTERs are no-ops) so history is complete.
    SQLModel.metadata.create_all(engine)

    applied = apply_migrations(engine)
    assert applied == [mid for mid, _ in MIGRATIONS]

    with engine.connect() as conn:
        recorded = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}
    assert recorded == {mid for mid, _ in MIGRATIONS}


def test_migrations_are_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    SQLModel.metadata.create_all(engine)

    first = apply_migrations(engine)
    second = apply_migrations(engine)
    assert first  # ran the first time
    assert second == []  # nothing pending the second time


def test_old_schema_gets_upgraded(tmp_path):
    """A pre-migration database missing new columns is brought up to date."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '')")
        )

    assert "work_item_type" not in _columns(engine, "tickets")
    apply_migrations(engine)

    cols = _columns(engine, "tickets")
    assert "work_item_type" in cols
    assert "parent_ticket_id" in cols
    assert "permission_allowlist_json" in cols


def test_backfill_runs_against_a_populated_db(tmp_path):
    """Migrations must survive a database that actually has rows.

    The other tests here apply migrations to an empty schema, so the
    definition-versioning backfill loop never executed and its INSERTs were
    never checked against the real table constraints. On a populated database
    it failed on NOT NULL columns, taking the whole app down: migrations run in
    the lifespan hook, so the server bound its port and then served nothing.

    Note SQLModel.create_all wins the race with the migration's CREATE TABLE, and
    a Python field default renders as NOT NULL with no DDL default — so every
    column an INSERT omits must be supplied explicitly.
    """
    from datetime import datetime, timezone

    from loregarden.models.domain import StudioAgent, WorkflowTemplate
    from sqlmodel import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'populated.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            StudioAgent(
                id="agent-1",
                slug="populated-agent",
                name="Populated Agent",
                role_body="You do a thing.",
            )
        )
        session.add(
            WorkflowTemplate(
                id="tpl-1",
                slug="populated-template",
                name="Populated Template",
                stages_json="[]",
                transitions_json="[]",
                source_path="studio:populated-template",
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        agent_versions = conn.execute(
            text("SELECT created_by, change_note, created_at FROM studio_agent_versions")
        ).fetchall()
        tpl_versions = conn.execute(
            text("SELECT created_by, change_note, created_at FROM workflow_template_versions")
        ).fetchall()

    assert len(agent_versions) == 1
    assert len(tpl_versions) == 1
    for created_by, change_note, created_at in agent_versions + tpl_versions:
        assert created_by == "migration"
        assert change_note == ""
        assert created_at is not None
        # Round-trips as a real timestamp rather than an empty string.
        datetime.fromisoformat(str(created_at)).astimezone(timezone.utc)


def test_queued_runs_gets_created_at_and_backfills(tmp_path):
    """A pre-0039 queued_runs table lacked created_at, breaking every SELECT.

    The model added the column but no migration did, so
    ``SELECT ... queued_runs.created_at`` raised OperationalError. 0039 adds it
    and backfills existing rows so they read back as real timestamps.
    """
    from datetime import datetime, timezone

    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE queued_runs ("
                "id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
                "ticket_id TEXT NOT NULL, run_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, "
                "status TEXT NOT NULL DEFAULT 'queued')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO queued_runs (id, workspace_id, ticket_id, run_id) "
                "VALUES ('q1', 'ws1', 't1', 'r1')"
            )
        )

    assert "created_at" not in _columns(engine, "queued_runs")
    apply_migrations(engine)
    assert "created_at" in _columns(engine, "queued_runs")

    with engine.connect() as conn:
        created_at = conn.execute(
            text("SELECT created_at FROM queued_runs WHERE id = 'q1'")
        ).scalar_one()
    assert created_at is not None
    datetime.fromisoformat(str(created_at)).astimezone(timezone.utc)


def test_ticket_enum_columns_move_from_names_to_values(tmp_path):
    """0042 rewrites tickets.state/workflow_stage_status from names to values.

    Those two columns stored the enum name (BLOCKED) while every neighbouring enum
    column stored the value (blocked), so an out-of-band write of the lowercase form
    produced a row the ORM could not load — and one such row raised LookupError on
    every SELECT over tickets, not just its own.
    """
    from loregarden.models.domain import Ticket
    from loregarden.models.domain.enums import StageStatus, TicketState
    from sqlmodel import Session, select

    engine = create_engine(f"sqlite:///{tmp_path / 'enums.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Ticket(id="t1", external_id="ext-1", workspace_id="ws1", title="Legacy row"))
        session.commit()
    # Rewind that row to how the old model persisted it: enum names, not values.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tickets SET state = 'BLOCKED', workflow_stage_status = 'RUNNING' "
                "WHERE id = 't1'"
            )
        )

    apply_migrations(engine)

    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT state, workflow_stage_status FROM tickets WHERE id = 't1'")
        ).one()
    assert stored == ("blocked", "running")

    # The point of the rewrite: the row loads through the ORM afterwards.
    with Session(engine) as session:
        ticket = session.exec(select(Ticket).where(Ticket.id == "t1")).one()
    assert ticket.state is TicketState.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.RUNNING


def test_ticket_enum_migration_leaves_value_form_rows_alone(tmp_path):
    """Re-running against an already-migrated row is a no-op, not a double rewrite."""
    from loregarden.models.domain import Ticket
    from loregarden.models.domain.enums import StageStatus, TicketState
    from sqlmodel import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'enums-idem.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            Ticket(
                id="t2",
                external_id="ext-2",
                workspace_id="ws1",
                title="Current row",
                state=TicketState.IN_PROGRESS,
                workflow_stage_status=StageStatus.PENDING,
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT state, workflow_stage_status FROM tickets WHERE id = 't2'")
        ).one()
    assert stored == ("in_progress", "pending")


def test_run_approval_event_enums_move_from_names_to_values(tmp_path):
    """0043 converts the last three name-form columns, across three tables."""
    from loregarden.models.domain import AgentRun, Approval, DomainEvent
    from loregarden.models.domain.enums import ApprovalStatus, EventType, RunStatus
    from sqlmodel import Session, select

    engine = create_engine(f"sqlite:///{tmp_path / 'enums43.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            AgentRun(id="r1", run_code="run_x", ticket_id="t1", workspace_id="ws1", agent_id="a1")
        )
        session.add(Approval(id="a1", ticket_id="t1", workspace_id="ws1", title="Gate"))
        session.add(DomainEvent(id="e1", type=EventType.AGENT_RUN_COMPLETED))
        session.commit()
    # Rewind all three to the names the old models persisted.
    with engine.begin() as conn:
        conn.execute(text("UPDATE agent_runs SET status = 'FAILED' WHERE id = 'r1'"))
        conn.execute(text("UPDATE approvals SET status = 'APPROVED' WHERE id = 'a1'"))
        conn.execute(text("UPDATE domain_events SET type = 'AGENT_RUN_COMPLETED' WHERE id = 'e1'"))

    apply_migrations(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM agent_runs WHERE id='r1'")).scalar_one() == (
            "failed"
        )
        assert conn.execute(text("SELECT status FROM approvals WHERE id='a1'")).scalar_one() == (
            "approved"
        )
        # EventType values are PascalCase, so this one is a rename, not a case-fold.
        assert conn.execute(text("SELECT type FROM domain_events WHERE id='e1'")).scalar_one() == (
            "AgentRunCompleted"
        )

    with Session(engine) as session:
        assert session.exec(select(AgentRun)).one().status is RunStatus.FAILED
        assert session.exec(select(Approval)).one().status is ApprovalStatus.APPROVED
        assert session.exec(select(DomainEvent)).one().type is EventType.AGENT_RUN_COMPLETED


def test_every_enum_column_stores_values(tmp_path):
    """The invariant the two migrations exist to establish.

    A mixed schema is the actual defect: nothing on the row tells a reader whether a
    column holds ``blocked`` or ``BLOCKED``, so a hand-written value is a coin flip.
    This fails the moment a new model reintroduces a name-form column.
    """
    from sqlalchemy import Enum as SAEnum

    offenders = [
        f"{table.name}.{col.name}"
        for table in SQLModel.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, SAEnum)
        and col.type.enum_class
        and list(col.type.enums) != [m.value for m in col.type.enum_class]
    ]
    assert offenders == []


def test_rigor_triage_reshapes_the_v3_template(tmp_path):
    """0023 scales rigor by change risk on a populated template.

    Seeded rather than run against an empty schema: the migration only acts when
    the template row exists, so an empty database would exercise nothing.
    """
    import json

    from loregarden.models.domain import WorkflowTemplate
    from sqlmodel import Session

    linear = [
        {
            "key": "triage",
            "name": "Triage",
            "agent_id": "ticket_scoper",
            "order": 1,
            "stage_type": "agent",
            "classify_routes": [],
        },
        {"key": "plan", "name": "Plan", "agent_id": "planner", "order": 2, "stage_type": "agent"},
        {"key": "spec", "name": "Spec", "agent_id": "spec", "order": 3, "stage_type": "agent"},
        {
            "key": "test-design",
            "name": "TD",
            "agent_id": "test_designer",
            "order": 4,
            "stage_type": "agent",
        },
    ]
    engine = create_engine(f"sqlite:///{tmp_path / 'rigor.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            WorkflowTemplate(
                id="tpl-v3",
                slug="studio-loregarden-tdd-v3",
                name="TDD V3",
                stages_json=json.dumps(linear),
                transitions_json="[]",
                source_path="studio:studio-loregarden-tdd-v3",
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        version, stages_json = conn.execute(
            text("SELECT version, stages_json FROM workflow_templates WHERE id='tpl-v3'")
        ).fetchone()
    stages = {s["key"]: s for s in json.loads(stages_json)}

    assert stages["triage"]["stage_type"] == "classify"
    routes = stages["triage"]["classify_routes"]
    light = next(r for r in routes if not r["default"])
    heavy = next(r for r in routes if r["default"])
    # Light work branches past planning; everything else keeps the full pipeline.
    assert light["to_stage"] == "test-design"
    assert "typo" in light["specialties"]
    assert heavy["to_stage"] == ""
    # The routing agent is preserved, not replaced by the reshape.
    assert light["agent_id"] == heavy["agent_id"] == "ticket_scoper"
    assert stages["spec"]["skip_when"] == "has_acceptance_criteria"
    # A stage outside the reshape is left alone. Checked on test-design rather
    # than plan: this runs the whole migration list, and later migrations do
    # legitimately reshape plan.
    assert stages["test-design"]["stage_type"] == "agent"
    # Recorded as a new version, so the reshape is auditable. Not pinned to an
    # exact number: this seeded template is fair game for later migrations too,
    # and every one of them would otherwise have to edit this line.
    assert version > 1

    # Re-running must not stack a second set of routes onto the template.
    assert apply_migrations(engine) == []
    with engine.connect() as conn:
        again = json.loads(
            conn.execute(
                text("SELECT stages_json FROM workflow_templates WHERE id='tpl-v3'")
            ).scalar()
        )
    assert len({s["key"]: s for s in again}["triage"]["classify_routes"]) == 2


def test_verify_stage_is_wired_between_implement_and_review(tmp_path):
    """0026 inserts verify without stranding or rewinding live tickets."""
    import json

    from loregarden.models.domain import WorkflowInstance, WorkflowTemplate
    from sqlmodel import Session

    stages = [
        {
            "key": "implement",
            "name": "Impl",
            "agent_id": "backend",
            "order": 7,
            "stage_type": "agent",
        },
        {
            "key": "review",
            "name": "Review",
            "agent_id": "architecture_reviewer",
            "order": 8,
            "stage_type": "agent",
        },
        {"key": "gate", "name": "Gate", "agent_id": "gatekeeper", "order": 9, "stage_type": "gate"},
    ]
    transitions = [
        {"from": "implement", "to": "review"},
        {"from": "review", "to": "gate", "when": "pass"},
    ]
    engine = create_engine(f"sqlite:///{tmp_path / 'verify.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            WorkflowTemplate(
                id="tpl-v3",
                slug="studio-loregarden-tdd-v3",
                name="V3",
                stages_json=json.dumps(stages),
                transitions_json=json.dumps(transitions),
                source_path="studio:studio-loregarden-tdd-v3",
            )
        )
        # One ticket still upstream, one already past the insertion point.
        session.add(
            WorkflowInstance(
                id="wi-early",
                ticket_id="t-early",
                template_id="tpl-v3",
                current_stage_key="implement",
                stages_json=json.dumps([{"key": "implement", "status": "running"}]),
            )
        )
        session.add(
            WorkflowInstance(
                id="wi-late",
                ticket_id="t-late",
                template_id="tpl-v3",
                current_stage_key="gate",
                stages_json=json.dumps([{"key": "implement", "status": "pending"}]),
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        stages_json, transitions_json = conn.execute(
            text("SELECT stages_json, transitions_json FROM workflow_templates WHERE id='tpl-v3'")
        ).fetchone()
        by_key = {s["key"]: s for s in json.loads(stages_json)}
        edges = {
            (t.get("from"), t.get("to"), t.get("when", "")) for t in json.loads(transitions_json)
        }

    # Sits between the claim and the review of it, and downstream stages shift.
    assert by_key["verify"]["order"] == 8
    assert by_key["verify"]["stage_type"] == "verify"
    assert by_key["review"]["order"] == 9
    assert by_key["gate"]["order"] == 10
    # implement no longer advances straight to review; refusal routes back to it.
    assert ("implement", "verify", "") in edges
    assert ("verify", "review", "pass") in edges
    assert ("verify", "implement", "reject") in edges
    assert ("implement", "review", "") not in edges

    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT id, stages_json FROM workflow_instances")).fetchall())
    early = {e["key"]: e["status"] for e in json.loads(rows["wi-early"])}
    late = {e["key"]: e["status"] for e in json.loads(rows["wi-late"])}
    # Upstream ticket will run it; one already past must not be pulled backwards,
    # even though its implement stage was never marked done.
    assert early["verify"] == "pending"
    assert late["verify"] == "wont_do"

    assert apply_migrations(engine) == []


def test_review_becomes_multi_angle(tmp_path):
    """0027 replaces the single reviewer with independent concurrent lanes."""
    import json

    from loregarden.models.domain import WorkflowTemplate
    from sqlmodel import Session

    stages = [
        {
            "key": "implement",
            "name": "Impl",
            "agent_id": "backend",
            "order": 7,
            "stage_type": "agent",
        },
        {
            "key": "review",
            "name": "Code Review",
            "agent_id": "architecture_reviewer",
            "order": 8,
            "stage_type": "classify",
            "classify_routes": [
                {
                    "languages": [],
                    "specialties": [],
                    "agent_id": "architecture_reviewer",
                    "skill_name": "",
                    "default": True,
                }
            ],
        },
    ]
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            WorkflowTemplate(
                id="tpl-v3",
                slug="studio-loregarden-tdd-v3",
                name="V3",
                stages_json=json.dumps(stages),
                transitions_json="[]",
                source_path="studio:studio-loregarden-tdd-v3",
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        stages_json = conn.execute(
            text("SELECT stages_json FROM workflow_templates WHERE id='tpl-v3'")
        ).scalar()
    review = {s["key"]: s for s in json.loads(stages_json)}["review"]

    assert review["stage_type"] == "parallel"
    lanes = [a["agent_id"] for a in review["parallel_agents"]]
    # Distinct lenses: structure, correctness, and exploitability.
    assert lanes == ["architecture_reviewer", "static_qa", "security_reviewer"]
    # The old single route would be a second, contradictory answer to "who reviews".
    assert review["classify_routes"] == []

    assert apply_migrations(engine) == []


def test_security_reviewer_is_registered_with_a_role():
    from loregarden.agents.registry import get_agent

    agent = get_agent("security_reviewer")
    assert agent is not None
    # A lane with no role body would run an agent with no instructions.
    assert agent["role_body"].strip()


def test_verify_must_record_a_verdict(tmp_path):
    """0028 makes verify produce its verdict rather than assert one."""
    import json

    from loregarden.models.domain import StudioAgent, WorkflowTemplate
    from sqlmodel import Session

    stages = [
        {
            "key": "implement",
            "name": "Impl",
            "agent_id": "backend",
            "order": 7,
            "stage_type": "agent",
        },
        {
            "key": "verify",
            "name": "Verify",
            "agent_id": "verifier",
            "order": 8,
            "stage_type": "verify",
        },
    ]
    engine = create_engine(f"sqlite:///{tmp_path / 'ev.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            WorkflowTemplate(
                id="tpl-v3",
                slug="studio-loregarden-tdd-v3",
                name="V3",
                stages_json=json.dumps(stages),
                transitions_json="[]",
                source_path="studio:studio-loregarden-tdd-v3",
            )
        )
        session.add(
            StudioAgent(
                id="a1",
                slug="verifier",
                name="Verifier",
                role_body="check it",
                mcp_tools_json=json.dumps(["loregarden_get_ticket"]),
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        stages_json = conn.execute(
            text("SELECT stages_json FROM workflow_templates WHERE id='tpl-v3'")
        ).scalar()
        tools = json.loads(
            conn.execute(text("SELECT mcp_tools_json FROM studio_agents WHERE id='a1'")).scalar()
        )
    by_key = {s["key"]: s for s in json.loads(stages_json)}

    assert by_key["verify"]["required_evidence"] == ["verify_verdict"]
    # A stage required to record evidence without the tool to record it would be
    # blocked with no way to comply. Grants are stored per row, not read from
    # the defaults, so existing agents need the backfill.
    assert "loregarden_attach_evidence" in tools
    # 0029 then adds the other half; see test_implement_must_show_the_change_working.
    assert by_key["implement"].get("required_evidence") == ["real_surface"]

    assert apply_migrations(engine) == []


def test_evidence_tool_is_granted_to_new_agents_by_default():
    from loregarden.services.studio_service import default_mcp_tools

    assert "loregarden_attach_evidence" in default_mcp_tools()


def test_implement_must_show_the_change_working(tmp_path):
    """0029 requires a real-surface capture, the claim tests never made."""
    import json

    from loregarden.models.domain import WorkflowTemplate
    from sqlmodel import Session

    stages = [
        {
            "key": "implement",
            "name": "Impl",
            "agent_id": "backend",
            "order": 7,
            "stage_type": "agent",
        },
        {
            "key": "verify",
            "name": "Verify",
            "agent_id": "verifier",
            "order": 8,
            "stage_type": "verify",
        },
    ]
    engine = create_engine(f"sqlite:///{tmp_path / 'rs.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            WorkflowTemplate(
                id="tpl-v3",
                slug="studio-loregarden-tdd-v3",
                name="V3",
                stages_json=json.dumps(stages),
                transitions_json="[]",
                source_path="studio:studio-loregarden-tdd-v3",
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        stages_json = conn.execute(
            text("SELECT stages_json FROM workflow_templates WHERE id='tpl-v3'")
        ).scalar()
    by_key = {s["key"]: s for s in json.loads(stages_json)}

    # The floor and the ceiling, on the stages that owe each.
    assert by_key["implement"]["required_evidence"] == ["real_surface"]
    assert by_key["verify"]["required_evidence"] == ["verify_verdict"]

    assert apply_migrations(engine) == []


def test_implementer_roles_explain_how_to_show_it_working():
    """A stage required to produce evidence with a role that never mentions it
    blocks an agent that was never told."""
    from loregarden.config import settings

    for role in (
        "agents/5_backend_implementer/backend_implementer_v1.md",
        "agents/6_frontend_implementer/frontend_implementer_v1.md",
    ):
        body = (settings.agent_context_dir / role).read_text(encoding="utf-8")
        assert "loregarden_attach_evidence" in body
        assert "real_surface" in body


def test_a_database_migrated_by_newer_code_is_called_out(tmp_path, caplog):
    """Reverting past a value-rewriting migration is otherwise a mystery LookupError."""
    import logging

    from loregarden.db.migrations import _warn_if_database_is_ahead

    engine = create_engine(f"sqlite:///{tmp_path / 'ahead.db'}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations (id) VALUES ('9999_from_the_future')"))

    with engine.connect() as conn:
        applied = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}

    with caplog.at_level(logging.ERROR):
        unknown = _warn_if_database_is_ahead(applied)

    assert unknown == ["9999_from_the_future"]
    assert "9999_from_the_future" in caplog.text


def test_a_current_database_is_not_flagged_as_ahead(tmp_path):
    from loregarden.db.migrations import _warn_if_database_is_ahead

    engine = create_engine(f"sqlite:///{tmp_path / 'current.db'}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    with engine.connect() as conn:
        applied = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}

    assert _warn_if_database_is_ahead(applied) == []
