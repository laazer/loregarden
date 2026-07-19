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
    # Untouched stages stay exactly as they were.
    assert stages["plan"]["stage_type"] == "agent"
    assert version == 2

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
