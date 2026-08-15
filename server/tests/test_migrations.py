from loregarden.db.migrations import MIGRATIONS, apply_migrations
from loregarden.models.domain import BoundaryVerdict
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _index_columns(engine, table: str) -> set[tuple[str, ...]]:
    with engine.connect() as conn:
        indexes = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        return {
            tuple(column[2] for column in conn.execute(text(f"PRAGMA index_info({index[1]})")))
            for index in indexes
        }


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
    assert "scope_reroute_agent" in cols


def test_skill_tables_exist_in_fresh_metadata_and_migration_seeds(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    SQLModel.metadata.create_all(engine)

    apply_migrations(engine)

    with engine.connect() as conn:
        skill_cols = _columns(engine, "skills")
        version_cols = _columns(engine, "skill_versions")
        slugs = {
            row[0]
            for row in conn.execute(text("SELECT slug FROM skills WHERE built_in=1")).fetchall()
        }
        plan = conn.execute(
            text(
                "SELECT id, body, required_capabilities_json, pack_id, pack_commit, "
                "upstream_name FROM skills WHERE slug='plan'"
            )
        ).fetchone()
        plan_versions = conn.execute(
            text("SELECT created_by, change_note FROM skill_versions WHERE skill_id=:sid"),
            {"sid": plan[0]},
        ).fetchall()

    assert {
        "slug",
        "name",
        "description",
        "body",
        "required_capabilities_json",
        "pack_id",
        "pack_commit",
        "upstream_name",
        "version",
        "built_in",
        "created_at",
        "updated_at",
    } <= skill_cols
    assert {"skill_id", "version", "snapshot_json", "created_by", "change_note", "created_at"} <= (
        version_cols
    )
    assert {
        "autopilot",
        "plan",
        "plan-risk",
        "plan-seams",
        "plan-simplest",
        "plan-synthesis",
        "refactor",
    } <= slugs
    assert not plan[1].startswith("---")
    assert plan[2] == "[]"
    assert plan[3] is None
    assert plan[4] is None
    assert plan[5] is None
    assert plan_versions == [("migration", "Seeded built-in skill from agent_context/skills")]


def test_skill_migration_preserves_existing_rows_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'existing-skills.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE skills (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    required_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    pack_id TEXT,
                    pack_commit TEXT,
                    upstream_name TEXT,
                    version INTEGER NOT NULL DEFAULT 7,
                    built_in INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO skills "
                "(id, slug, name, description, body, required_capabilities_json, version, "
                "built_in, created_at, updated_at) "
                "VALUES ('existing-plan', 'plan', 'Custom Plan', 'custom', 'custom body', "
                "'[]', 7, 0, 'then', 'then')"
            )
        )

    apply_migrations(engine)
    with engine.connect() as conn:
        first = conn.execute(
            text("SELECT name, body, version, built_in FROM skills WHERE slug='plan'")
        ).fetchone()
        first_count = conn.execute(text("SELECT COUNT(*) FROM skill_versions")).scalar()

    assert apply_migrations(engine) == []
    with engine.connect() as conn:
        second = conn.execute(
            text("SELECT name, body, version, built_in FROM skills WHERE slug='plan'")
        ).fetchone()
        second_count = conn.execute(text("SELECT COUNT(*) FROM skill_versions")).scalar()

    assert first == ("Custom Plan", "custom body", 7, 0)
    assert second == first
    assert second_count == first_count


def test_skill_versions_reject_duplicate_skill_version(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dup-skills.db'}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)

    with engine.begin() as conn:
        skill_id = conn.execute(text("SELECT id FROM skills WHERE slug='plan'")).scalar()
        row = conn.execute(
            text("SELECT snapshot_json FROM skill_versions WHERE skill_id=:sid AND version=1"),
            {"sid": skill_id},
        ).fetchone()
        import pytest
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO skill_versions "
                    "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
                    "VALUES ('duplicate', :sid, 1, :snap, 'test', '', 'now')"
                ),
                {"sid": skill_id, "snap": row[0]},
            )


def test_stage_fanout_tables_exist_in_fresh_metadata_and_migration_history(tmp_path):
    """R3.AC1/R3.AC6/R5.AC8: fresh metadata includes fan-out tables and records migration 0070."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fanout-fresh.db'}")
    SQLModel.metadata.create_all(engine)

    assert _columns(engine, "stage_fanout_groups") >= {
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
    assert _columns(engine, "stage_fanout_attempts") >= {
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
    assert "0070_stage_fanout_groups" in [migration_id for migration_id, _ in MIGRATIONS]

    apply_migrations(engine)
    with engine.connect() as conn:
        recorded = {row[0] for row in conn.execute(text("SELECT id FROM schema_migrations"))}
    assert "0070_stage_fanout_groups" in recorded


def test_stage_fanout_migration_creates_tables_indexes_and_is_idempotent(tmp_path):
    """R3.AC2/R3.AC3/R5.AC8: older databases get both fan-out tables and indexes once."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fanout-old.db'}")

    first = apply_migrations(engine)
    second = apply_migrations(engine)

    assert "0070_stage_fanout_groups" in first
    assert second == []
    assert _columns(engine, "stage_fanout_groups") >= {
        "workspace_id",
        "ticket_id",
        "orchestration_run_id",
        "stage_key",
        "attempt_count",
        "status",
        "outcome",
        "winner_attempt_id",
    }
    assert _columns(engine, "stage_fanout_attempts") >= {
        "group_id",
        "attempt_index",
        "agent_run_id",
        "worktree_id",
        "branch",
        "status",
    }
    assert {
        ("workspace_id",),
        ("ticket_id", "stage_key"),
        ("orchestration_run_id",),
        ("status",),
        ("winner_attempt_id",),
    } <= _index_columns(engine, "stage_fanout_groups")
    assert {
        ("group_id",),
        ("agent_run_id",),
        ("worktree_id",),
        ("status",),
        ("group_id", "attempt_index"),
    } <= _index_columns(engine, "stage_fanout_attempts")


def test_stage_fanout_migration_repairs_partially_created_schema(tmp_path):
    """R3.AC4/R3.AC5/R5.AC8: table and index guards are independent."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fanout-partial.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE stage_fanout_groups (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    orchestration_run_id TEXT,
                    stage_key TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    pre_fanout_workflow_stage_key TEXT NOT NULL DEFAULT '',
                    pre_fanout_workflow_stage_status TEXT NOT NULL DEFAULT 'pending',
                    pre_fanout_stage_map_json TEXT NOT NULL DEFAULT '[]',
                    pre_fanout_next_agent TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    outcome TEXT NOT NULL DEFAULT 'pending',
                    winner_attempt_id TEXT,
                    declined_reason TEXT NOT NULL DEFAULT '',
                    failure_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    settled_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_stage_fanout_groups_workspace_id "
                "ON stage_fanout_groups (workspace_id)"
            )
        )

    apply_migrations(engine)

    assert _columns(engine, "stage_fanout_attempts") >= {
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
    assert {
        ("workspace_id",),
        ("ticket_id", "stage_key"),
        ("orchestration_run_id",),
        ("status",),
        ("winner_attempt_id",),
    } <= _index_columns(engine, "stage_fanout_groups")
    assert {
        ("group_id",),
        ("agent_run_id",),
        ("worktree_id",),
        ("status",),
        ("group_id", "attempt_index"),
    } <= _index_columns(engine, "stage_fanout_attempts")


def test_stage_fanout_migration_repairs_missing_indexes_when_tables_exist(tmp_path):
    """R3.AC5/R5.AC8: existing tables must not make the migration skip index repair."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fanout-missing-indexes.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE stage_fanout_groups (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    orchestration_run_id TEXT,
                    stage_key TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    pre_fanout_workflow_stage_key TEXT NOT NULL DEFAULT '',
                    pre_fanout_workflow_stage_status TEXT NOT NULL DEFAULT 'pending',
                    pre_fanout_stage_map_json TEXT NOT NULL DEFAULT '[]',
                    pre_fanout_next_agent TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    outcome TEXT NOT NULL DEFAULT 'pending',
                    winner_attempt_id TEXT,
                    declined_reason TEXT NOT NULL DEFAULT '',
                    failure_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    settled_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE stage_fanout_attempts (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    attempt_index INTEGER NOT NULL,
                    attempt_name TEXT NOT NULL DEFAULT '',
                    agent_run_id TEXT,
                    worktree_id TEXT,
                    branch TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned',
                    failure_details TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )

    assert _index_columns(engine, "stage_fanout_groups") == {("id",)}
    assert _index_columns(engine, "stage_fanout_attempts") == {("id",)}

    apply_migrations(engine)

    assert {
        ("workspace_id",),
        ("ticket_id", "stage_key"),
        ("orchestration_run_id",),
        ("status",),
        ("winner_attempt_id",),
    } <= _index_columns(engine, "stage_fanout_groups")
    assert {
        ("group_id",),
        ("agent_run_id",),
        ("worktree_id",),
        ("status",),
        ("group_id", "attempt_index"),
    } <= _index_columns(engine, "stage_fanout_attempts")


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
    from loregarden.models.domain import Ticket, Workspace
    from loregarden.models.domain.enums import StageStatus, TicketState
    from sqlmodel import Session, select

    engine = create_engine(f"sqlite:///{tmp_path / 'enums.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Workspace(id="ws1", slug="ws1", name="WS1"))
        session.commit()
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
    from loregarden.models.domain import Ticket, Workspace
    from loregarden.models.domain.enums import StageStatus, TicketState
    from sqlmodel import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'enums-idem.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Workspace(id="ws1", slug="ws1", name="WS1"))
        session.commit()
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
    from loregarden.models.domain import AgentRun, Approval, DomainEvent, Ticket, Workspace
    from loregarden.models.domain.enums import ApprovalStatus, EventType, RunStatus
    from sqlmodel import Session, select

    engine = create_engine(f"sqlite:///{tmp_path / 'enums43.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Workspace(id="ws1", slug="ws1", name="WS1"))
        session.commit()
        session.add(Ticket(id="t1", external_id="ext-1", workspace_id="ws1", title="Row"))
        session.commit()
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

    from loregarden.models.domain import (
        Ticket,
        WorkflowInstance,
        WorkflowTemplate,
        Workspace,
    )
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
        # Both rows name a real ticket: workflow_instances references them.
        session.commit()
        workspace = Workspace(slug="wf", name="wf")
        session.add(workspace)
        session.commit()
        early_ticket = Ticket(
            id="t-early", external_id="t-early", workspace_id=workspace.id, title="early"
        )
        late_ticket = Ticket(
            id="t-late", external_id="t-late", workspace_id=workspace.id, title="late"
        )
        session.add_all([early_ticket, late_ticket])
        session.commit()
        session.add(
            WorkflowInstance(
                id="wi-early",
                ticket_id=early_ticket.id,
                template_id="tpl-v3",
                current_stage_key="implement",
                stages_json=json.dumps([{"key": "implement", "status": "running"}]),
            )
        )
        session.add(
            WorkflowInstance(
                id="wi-late",
                ticket_id=late_ticket.id,
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

    from loregarden.db.migration_runner import warn_if_database_is_ahead

    engine = create_engine(f"sqlite:///{tmp_path / 'ahead.db'}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations (id) VALUES ('9999_from_the_future')"))

    with engine.connect() as conn:
        applied = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}

    with caplog.at_level(logging.ERROR):
        unknown = warn_if_database_is_ahead(applied, MIGRATIONS)

    assert unknown == ["9999_from_the_future"]
    assert "9999_from_the_future" in caplog.text


def test_a_current_database_is_not_flagged_as_ahead(tmp_path):
    from loregarden.db.migration_runner import warn_if_database_is_ahead

    engine = create_engine(f"sqlite:///{tmp_path / 'current.db'}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    with engine.connect() as conn:
        applied = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}

    assert warn_if_database_is_ahead(applied, MIGRATIONS) == []


def test_workspace_scoped_runs_and_approvals_relax_ticket_id(tmp_path):
    """Home chat needs runs and approvals that are not hung off a ticket.

    SQLite cannot ALTER COLUMN, so the migration rebuilds each table. The
    rebuild must keep every prior column and index, and leave existing rows
    readable with their ticket_id still set.
    """
    from loregarden.db.migration_utils import column_is_nullable

    engine = create_engine(f"sqlite:///{tmp_path / 'relax.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE workspaces (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE tickets (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE agent_runs (
                    id TEXT PRIMARY KEY,
                    run_code TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT '',
                    skill_name TEXT NOT NULL DEFAULT '',
                    stage_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    command TEXT NOT NULL DEFAULT '',
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(ticket_id) REFERENCES tickets (id),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_agent_runs_ticket_id ON agent_runs (ticket_id)"))
        conn.execute(
            text(
                """
                CREATE TABLE approvals (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    level TEXT NOT NULL DEFAULT '',
                    stage_key TEXT NOT NULL DEFAULT '',
                    impact TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(ticket_id) REFERENCES tickets (id),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_approvals_ticket_id ON approvals (ticket_id)"))
        conn.execute(text("INSERT INTO workspaces (id, slug) VALUES ('ws1', 'demo')"))
        conn.execute(
            text("INSERT INTO tickets (id, workspace_id, title) VALUES ('t1', 'ws1', 'demo')")
        )
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, run_code, ticket_id, workspace_id) "
                "VALUES ('r1', 'run_old', 't1', 'ws1')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO approvals (id, ticket_id, workspace_id, title) "
                "VALUES ('a1', 't1', 'ws1', 'Allow Bash?')"
            )
        )

    apply_migrations(engine)

    with engine.connect() as conn:
        assert column_is_nullable(conn, "agent_runs", "ticket_id")
        assert column_is_nullable(conn, "approvals", "ticket_id")
        run_ticket = conn.execute(
            text("SELECT ticket_id FROM agent_runs WHERE id='r1'")
        ).scalar_one()
        approval_ticket = conn.execute(
            text("SELECT ticket_id FROM approvals WHERE id='a1'")
        ).scalar_one()
        assert run_ticket == "t1"
        assert approval_ticket == "t1"
        # Indexes survive the rebuild.
        index_sql = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name IN ('ix_agent_runs_ticket_id', 'ix_approvals_ticket_id')"
                )
            ).fetchall()
        ]
        assert set(index_sql) == {"ix_agent_runs_ticket_id", "ix_approvals_ticket_id"}
        # And a ticket-less row is now legal.
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, run_code, ticket_id, workspace_id) "
                "VALUES ('r2', 'run_home', NULL, 'ws1')"
            )
        )
        conn.commit()


def test_branch_triage_messages_gain_agent_run_link(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'branch-run.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE branch_triage_messages (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'complete',
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )

    apply_migrations(engine)

    assert "run_id" in _columns(engine, "branch_triage_messages")
    with engine.connect() as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='branch_triage_messages'"
                )
            ).fetchall()
        }
    assert "ix_branch_triage_messages_run_id" in indexes


def test_agent_runs_gain_the_git_boundary_columns_with_safe_defaults(tmp_path):
    """0078 adds the boundary a run started from.

    Existing rows predate any boundary and must read back as unrecorded rather
    than as a mismatch, so the defaults are empty strings and an empty JSON
    array — never NULL, which every reader here would have to special-case.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'boundary.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agent_runs ("
                "id TEXT PRIMARY KEY, run_code TEXT NOT NULL, workspace_id TEXT NOT NULL, "
                "agent_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, run_code, workspace_id, agent_id) "
                "VALUES ('r1', 'LG-1-implement', 'ws1', 'backend_implementer')"
            )
        )

    apply_migrations(engine)

    columns = _columns(engine, "agent_runs")
    assert {
        "start_repo_path",
        "start_branch",
        "start_head_sha",
        "start_dirty_paths_json",
    } <= columns

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT start_repo_path, start_branch, start_head_sha, start_dirty_paths_json "
                "FROM agent_runs WHERE id = 'r1'"
            )
        ).one()
    assert row == ("", "", "", "[]")


def test_agent_runs_gain_the_boundary_verdict_defaulting_to_unknown(tmp_path):
    """0079 records how each run's tree compared to its predecessor's.

    Rows that predate the check compared nothing, which is exactly what UNKNOWN
    means — so that is the default rather than an empty string, which would give
    every reader a second not-checked case to handle.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'verdict.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agent_runs ("
                "id TEXT PRIMARY KEY, run_code TEXT NOT NULL, workspace_id TEXT NOT NULL, "
                "agent_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, run_code, workspace_id, agent_id) "
                "VALUES ('r1', 'LG-1-implement', 'ws1', 'backend_implementer')"
            )
        )

    apply_migrations(engine)

    assert "start_boundary_verdict" in _columns(engine, "agent_runs")
    with engine.connect() as conn:
        verdict = conn.execute(
            text("SELECT start_boundary_verdict FROM agent_runs WHERE id = 'r1'")
        ).scalar_one()
    assert verdict == BoundaryVerdict.UNKNOWN.value
