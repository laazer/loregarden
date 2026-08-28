import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    MemoryBriefingAssembly,
    StudioAgent,
    StudioAgentVersion,
    Ticket,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.skills.registry import SKILL_PROMPT_CAP, get_skill, list_skills
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _indexes(engine, table: str) -> list[dict]:
    with engine.connect() as conn:
        indexes = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        result = []
        for index in indexes:
            columns = conn.execute(text(f"PRAGMA index_info({index[1]})")).fetchall()
            result.append(
                {
                    "name": index[1],
                    "unique": bool(index[2]),
                    "columns": [column[2] for column in columns],
                }
            )
    return result


def _write_seed(agent_context: Path, slug: str, markdown: str) -> None:
    skill_dir = agent_context / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(markdown, encoding="utf-8")


def _create_skill(client, slug: str, markdown: str):
    return client.post(
        "/api/studio/skills",
        json={
            "slug": slug,
            "markdown": markdown,
            "created_by": "pytest",
            "change_note": "create skill",
        },
    )


def _record_migrations_through_0068(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
        )
        for migration_id in SHIPPED_MIGRATION_IDS:
            conn.execute(
                text("INSERT INTO schema_migrations (id) VALUES (:id)"),
                {"id": migration_id},
            )
            if migration_id == "0068_clear_phantom_skill_names":
                break


def test_fresh_sqlmodel_metadata_contains_skill_tables_and_constraints(tmp_path):
    """R1.AC1/R1.AC4: metadata-created DBs include skill tables and version uniqueness."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh-skills.db'}")
    SQLModel.metadata.create_all(engine)

    assert _columns(engine, "skills") >= {
        "id",
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
    }
    assert _columns(engine, "skill_versions") >= {
        "id",
        "skill_id",
        "version",
        "snapshot_json",
        "created_by",
        "change_note",
        "created_at",
    }
    assert any(
        index["unique"] and index["columns"] == ["skill_id", "version"]
        for index in _indexes(engine, "skill_versions")
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO skills "
                "(id, slug, name, description, body, required_capabilities_json, version, "
                "built_in, created_at, updated_at) "
                "VALUES ('skill-1', 'duplicate-check', 'Duplicate Check', '', 'body', '[]', "
                "1, 0, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO skill_versions "
                "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
                "VALUES ('sv-1', 'skill-1', 1, '{}', 'pytest', 'first', "
                "'2026-01-01T00:00:00+00:00')"
            )
        )
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO skill_versions "
                    "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
                    "VALUES ('sv-2', 'skill-1', 1, '{}', 'pytest', 'duplicate', "
                    "'2026-01-01T00:00:00+00:00')"
                )
            )


def test_skill_migration_is_registered_after_current_latest_migration():
    """R1: the skill migration is appended after the shipped 0068 migration."""
    from loregarden.db.migrations import MIGRATIONS

    migration_ids = [migration_id for migration_id, _ in MIGRATIONS]
    latest_shipped_index = migration_ids.index("0068_clear_phantom_skill_names")
    assert len(migration_ids) > latest_shipped_index + 1
    skill_migration_id = migration_ids[latest_shipped_index + 1]
    assert skill_migration_id.startswith("0069_")
    assert "skill" in skill_migration_id


def test_skill_migration_preserves_existing_non_skill_data(tmp_path, monkeypatch):
    """R1.AC2: operator DBs gain skill tables without rewriting existing data."""
    agent_context = tmp_path / "agent_context"
    _write_seed(agent_context, "seeded", "---\nname: Seeded\n---\nbody")
    monkeypatch.setattr("loregarden.config.settings.agent_context_dir", agent_context)

    engine = create_engine(f"sqlite:///{tmp_path / 'operator.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS skill_versions"))
        conn.execute(text("DROP TABLE IF EXISTS skills"))
    _record_migrations_through_0068(engine)

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        workspace = Workspace(id="ws-1", slug="loregarden", name="Loregarden", repo_path=".")
        template = WorkflowTemplate(
            id="tpl-1",
            slug="operator-template",
            name="Operator Template",
            description="leave template",
            stages_json='[{"key":"verify"}]',
            transitions_json='[{"from":"verify","to":"done"}]',
            source_path="studio:operator-template",
            version=4,
            built_in=False,
        )
        agent = StudioAgent(
            id="agent-1",
            slug="operator-agent",
            name="Operator Agent",
            description="leave agent",
            role_body="unchanged role",
            version=5,
            built_in=False,
        )
        agent_version = StudioAgentVersion(
            id="agent-version-5",
            agent_id=agent.id,
            version=5,
            snapshot_json='{"role_body":"unchanged role"}',
            created_by="operator",
            change_note="existing snapshot",
            created_at=now,
        )
        ticket = Ticket(
            id="ticket-1",
            external_id="operator-ticket",
            workspace_id=workspace.id,
            title="Operator Ticket",
            description="leave ticket",
            acceptance_criteria_json='["unchanged"]',
        )
        run = AgentRun(
            id="run-1",
            run_code="run_operator",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="operator-agent",
            stage_key="verify",
            stdout="keep stdout",
        )
        artifact = Artifact(
            id="artifact-1",
            ticket_id=ticket.id,
            run_id=run.id,
            kind="log",
            title="Keep Artifact",
            content_json='{"keep": true}',
        )
        # Committed in dependency order: one flush orders by mapper relationship,
        # and these are joined by bare foreign key columns.
        session.add_all([workspace, template, agent])
        session.commit()
        session.add_all([agent_version, ticket])
        session.commit()
        session.add(run)
        session.commit()
        session.add(artifact)
        session.commit()

    applied = apply_migrations(engine)

    assert applied
    assert all(migration_id > "0068_clear_phantom_skill_names" for migration_id in applied)
    assert _columns(engine, "skills")
    assert _columns(engine, "skill_versions")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name, version FROM studio_agents")).one() == (
            "Operator Agent",
            5,
        )
        assert conn.execute(
            text("SELECT created_by, change_note FROM studio_agent_versions")
        ).one() == ("operator", "existing snapshot")
        assert conn.execute(
            text("SELECT name, version, built_in FROM workflow_templates")
        ).one() == ("Operator Template", 4, 0)
        assert conn.execute(text("SELECT title, description FROM tickets")).one() == (
            "Operator Ticket",
            "leave ticket",
        )
        assert conn.execute(text("SELECT run_code, stdout FROM agent_runs")).one() == (
            "run_operator",
            "keep stdout",
        )
        assert conn.execute(text("SELECT title, content_json FROM artifacts")).one() == (
            "Keep Artifact",
            '{"keep": true}',
        )


def test_skill_migration_seeds_builtins_once_and_preserves_existing_rows(tmp_path, monkeypatch):
    """R1/R2/R3/R6: migration seeds parsed, full, nullable-provenance built-ins once."""
    agent_context = tmp_path / "agent_context"
    long_body = "A" * (SKILL_PROMPT_CAP + 75)
    _write_seed(
        agent_context,
        "plan",
        "---\nname: Seed Plan\ndescription: Plans carefully\n---\n\n" + long_body,
    )
    _write_seed(agent_context, "longseed", "---\nname: Long Seed\n---\n" + long_body)
    _write_seed(agent_context, "refactor", "# Refactor\n\nBody")
    _write_seed(agent_context, "empty", "  \n\t")
    monkeypatch.setattr("loregarden.config.settings.agent_context_dir", agent_context)

    engine = create_engine(f"sqlite:///{tmp_path / 'seed-skills.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO skills "
                "(id, slug, name, description, body, required_capabilities_json, version, "
                "built_in, created_at, updated_at) "
                "VALUES ('existing-plan', 'plan', 'Operator Plan', 'keep me', "
                "'operator body', '[\"terminal\"]', 7, 0, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO skill_versions "
                "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
                "VALUES ('existing-version', 'existing-plan', 7, '{\"body\":\"operator body\"}', "
                "'operator', 'existing history', '2026-01-01T00:00:00+00:00')"
            )
        )

    apply_migrations(engine)
    apply_migrations(engine)

    with engine.connect() as conn:
        plan = conn.execute(
            text(
                "SELECT name, description, body, version, built_in, required_capabilities_json, "
                "pack_id, pack_commit, upstream_name FROM skills WHERE slug = 'plan'"
            )
        ).one()
        refactor = conn.execute(
            text(
                "SELECT id, name, description, body, version, built_in, "
                "required_capabilities_json, pack_id, pack_commit, upstream_name "
                "FROM skills WHERE slug = 'refactor'"
            )
        ).one()
        refactor_versions = conn.execute(
            text(
                "SELECT version, snapshot_json, created_by, change_note FROM skill_versions "
                "WHERE skill_id = :skill_id"
            ),
            {"skill_id": refactor.id},
        ).fetchall()
        seed_slugs = {row[0] for row in conn.execute(text("SELECT slug FROM skills")).fetchall()}
        longseed_body = conn.execute(
            text("SELECT body FROM skills WHERE slug = 'longseed'")
        ).scalar_one()

    assert plan.name == "Operator Plan"
    assert plan.description == "keep me"
    assert plan.body == "operator body"
    assert plan.version == 7
    assert plan.built_in == 0
    assert plan.required_capabilities_json == '["terminal"]'
    assert plan.pack_id is None
    assert plan.pack_commit is None
    assert plan.upstream_name is None

    assert refactor.name == "refactor"
    assert refactor.description == ""
    assert refactor.body == "# Refactor\n\nBody"
    assert refactor.version == 1
    assert refactor.built_in == 1
    assert refactor.required_capabilities_json == "[]"
    assert refactor.pack_id is None
    assert refactor.pack_commit is None
    assert refactor.upstream_name is None
    assert len(refactor_versions) == 1
    assert refactor_versions[0].version == 1
    assert "migration" in refactor_versions[0].created_by
    assert "seed" in refactor_versions[0].change_note.lower()
    assert json.loads(refactor_versions[0].snapshot_json)["body"] == "# Refactor\n\nBody"
    assert longseed_body == long_body
    assert len(longseed_body) > SKILL_PROMPT_CAP
    assert "empty" not in seed_slugs


def test_parse_skill_markdown_frontmatter_contract():
    """R3: only leading YAML frontmatter is metadata; body preservation is exact."""
    from loregarden.services.skill_service import parse_skill_markdown

    parsed = parse_skill_markdown(
        "sluggy",
        "---\nname: Friendly Name\ndescription: Human summary\n---\n\n# Body\n\nKeep me\n",
    )
    assert parsed.name == "Friendly Name"
    assert parsed.description == "Human summary"
    assert parsed.body == "\n# Body\n\nKeep me\n"

    nonleading = parse_skill_markdown("plain", "\n---\nname: Not metadata\n---\nBody")
    assert nonleading.name == "plain"
    assert nonleading.description == ""
    assert nonleading.body == "\n---\nname: Not metadata\n---\nBody"

    malformed = parse_skill_markdown("bad", "---\nname: [unterminated\n---\nBody")
    assert malformed.name == "bad"
    assert malformed.description == ""
    assert malformed.body == "Body"


def test_parse_skill_markdown_preserves_frontmatter_boundary_edge_cases():
    """R3: parser must not strip near-frontmatter or alter post-fence bytes."""
    from loregarden.services.skill_service import parse_skill_markdown

    crlf = parse_skill_markdown(
        "crlf",
        "---\r\nname: CRLF Skill\r\ndescription: Windows newlines\r\n---\r\n\r\nBody\r\n",
    )
    assert crlf.name == "CRLF Skill"
    assert crlf.description == "Windows newlines"
    assert crlf.body == "\r\nBody\r\n"

    not_a_mapping = parse_skill_markdown("list-meta", "---\n- nope\n---\n# Body")
    assert not_a_mapping.name == "list-meta"
    assert not_a_mapping.description == ""
    assert not_a_mapping.body == "# Body"

    leading_space = parse_skill_markdown("spacey", " ---\nname: Not metadata\n---\n# Body")
    assert leading_space.name == "spacey"
    assert leading_space.description == ""
    assert leading_space.body == " ---\nname: Not metadata\n---\n# Body"


def test_registry_is_database_backed_fresh_and_lists_sorted(client, db_session):
    """R4/R6: lookup reads live DB rows, ignores filesystem overlays, and returns full body."""
    long_body = "live body " + ("x" * (SKILL_PROMPT_CAP + 20))
    response = _create_skill(client, "fresh-registry", long_body)
    assert response.status_code == 200, response.text

    assert get_skill("fresh-registry") == long_body
    assert len(get_skill("fresh-registry") or "") == len(long_body)

    db_session.execute(
        text("UPDATE skills SET body = 'updated in db' WHERE slug = 'fresh-registry'")
    )
    db_session.commit()

    assert get_skill("fresh-registry") == "updated in db"
    assert list_skills() == sorted(list_skills())


def test_studio_skill_api_versions_and_restore_are_append_only(client, db_session):
    """R5: create, update, and restore each write one snapshot and preserve identity."""
    created = _create_skill(
        client,
        "versioned-skill",
        "---\nname: Versioned Skill\ndescription: first\n---\n\nv1 body",
    )
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1
    assert created.json()["name"] == "Versioned Skill"
    assert created.json()["description"] == "first"
    assert created.json()["body"] == "\nv1 body"

    updated = client.patch(
        "/api/studio/skills/versioned-skill",
        json={
            "markdown": "---\nname: Versioned Skill\ndescription: second\n---\n\nv2 body",
            "created_by": "pytest",
            "change_note": "update skill",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["body"] == "\nv2 body"

    versions = client.get("/api/studio/skills/versioned-skill/versions")
    assert versions.status_code == 200
    assert [row["version"] for row in versions.json()] == [2, 1]
    assert all(row["created_by"] == "pytest" for row in versions.json())

    v1 = client.get("/api/studio/skills/versioned-skill/versions/1")
    assert v1.status_code == 200
    assert v1.json()["body"] == "\nv1 body"

    restored = client.post(
        "/api/studio/skills/versioned-skill/versions/1/restore",
        json={"created_by": "pytest", "change_note": "restore v1"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    assert restored.json()["slug"] == "versioned-skill"
    assert restored.json()["built_in"] is False
    assert restored.json()["body"] == "\nv1 body"

    rows = db_session.execute(
        text(
            "SELECT version, snapshot_json, created_by, change_note FROM skill_versions "
            "WHERE skill_id = (SELECT id FROM skills WHERE slug = 'versioned-skill') "
            "ORDER BY version"
        )
    ).fetchall()
    assert [row.version for row in rows] == [1, 2, 3]
    assert [row.created_by for row in rows] == ["pytest", "pytest", "pytest"]
    assert json.loads(rows[2].snapshot_json)["body"] == "\nv1 body"


def test_skill_version_snapshots_are_complete_enough_to_restore(client, db_session):
    """R5/R6: snapshots carry all mutable head fields, not just prompt body text."""
    created = _create_skill(
        client,
        "snapshot-completeness",
        "---\nname: Snapshot Complete\ndescription: has metadata\n---\n\nsnapshot body",
    )
    assert created.status_code == 200, created.text

    row = db_session.execute(
        text(
            "SELECT snapshot_json FROM skill_versions "
            "WHERE skill_id = (SELECT id FROM skills WHERE slug = 'snapshot-completeness') "
            "AND version = 1"
        )
    ).one()
    snapshot = json.loads(row.snapshot_json)

    assert snapshot["slug"] == "snapshot-completeness"
    assert snapshot["name"] == "Snapshot Complete"
    assert snapshot["description"] == "has metadata"
    assert snapshot["body"] == "\nsnapshot body"
    assert snapshot["required_capabilities_json"] == "[]"
    assert snapshot["pack_id"] is None
    assert snapshot["pack_commit"] is None
    assert snapshot["upstream_name"] is None
    assert snapshot["version"] == 1


def test_skill_mutation_requires_audit_fields_without_partial_versions(client, db_session):
    """R5: failed update/restore validation must not bump head or append history."""
    created = _create_skill(client, "audit-required", "v1 body")
    assert created.status_code == 200, created.text

    missing_update_audit = client.patch(
        "/api/studio/skills/audit-required",
        json={"markdown": "v2 body", "created_by": "pytest"},
    )
    assert missing_update_audit.status_code == 422

    missing_restore_audit = client.post(
        "/api/studio/skills/audit-required/versions/1/restore",
        json={"created_by": "pytest"},
    )
    assert missing_restore_audit.status_code == 422

    head = db_session.execute(
        text("SELECT version, body FROM skills WHERE slug = 'audit-required'")
    ).one()
    versions = db_session.execute(
        text(
            "SELECT version, created_by, change_note FROM skill_versions "
            "WHERE skill_id = (SELECT id FROM skills WHERE slug = 'audit-required')"
        )
    ).fetchall()
    assert head == (1, "v1 body")
    assert [(row.version, row.created_by, row.change_note) for row in versions] == [
        (1, "pytest", "create skill")
    ]


@pytest.mark.parametrize("slug", ["", "a/b", "a\\b", ".", "..", "../skill", "skill/.."])
def test_studio_skill_slug_validation_rejects_path_like_values(client, slug):
    response = _create_skill(client, slug, "body")
    assert response.status_code == 400


def test_skill_version_http_errors_mirror_agent_versions(client):
    assert client.get("/api/studio/skills/missing/versions").status_code == 404
    assert client.get("/api/studio/skills/missing/versions/1").status_code == 404
    assert (
        client.post(
            "/api/studio/skills/missing/versions/1/restore",
            json={"created_by": "pytest", "change_note": "restore"},
        ).status_code
        == 404
    )


def test_prompt_truncates_skill_at_render_time_with_explicit_notice(client, db_session):
    """R6: full storage survives while rendered prompt reports the skill cap."""
    body = ("L" * SKILL_PROMPT_CAP) + "TAIL-OUTSIDE-RENDERED-SKILL"
    create = _create_skill(client, "long-render-skill", body)
    assert create.status_code == 200, create.text
    assert get_skill("long-render-skill") == body

    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = Ticket(
        title="Render long skill",
        description="",
        external_id="render-long-skill",
        workspace_id=workspace.id,
        acceptance_criteria_json="[]",
    )
    run = AgentRun(
        run_code="run_long_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="verify",
        skill_name="long-render-skill",
    )
    # The ticket commits first: one flush can emit the run that names it first.
    db_session.add(ticket)
    db_session.commit()
    db_session.add(run)
    db_session.commit()

    prompt = CliAgentExecutor(db_session)._build_prompt(
        ticket,
        run,
        {"role_body": "role"},
        resolve_agent_context_dir(workspace),
        workspace,
        WorkflowStageDef(
            key="verify",
            name="Verify",
            order=1,
            stage_type="agent",
            agent_id="backend_implementer",
            skill_name="long-render-skill",
        ),
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )

    assert "long-render-skill" in prompt
    assert "truncat" in prompt.lower()
    assert f"original body length: {len(body)}" in prompt
    assert f"rendered body length: {SKILL_PROMPT_CAP}" in prompt
    assert body[:SKILL_PROMPT_CAP] in prompt
    assert body[SKILL_PROMPT_CAP:] not in prompt
