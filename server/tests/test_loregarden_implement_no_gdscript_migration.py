"""loregarden's implement stage no longer offers the GDScript reviewer."""

from __future__ import annotations

import json

from loregarden.db.versions.loregarden_implement_no_gdscript import (
    AGENT_ID,
    TEMPLATE_SLUG,
    m_loregarden_implement_no_gdscript,
)
from sqlalchemy import text

ROUTES = [
    {"languages": ["gdscript"], "specialties": ["gameplay"], "agent_id": AGENT_ID},
    {"specialties": ["frontend"], "agent_id": "frontend_implementer"},
    {"specialties": ["refactor"], "agent_id": "backend_implementer", "skill_name": "refactor"},
    {"specialties": ["backend"], "agent_id": "backend_implementer", "default": True},
]


def _insert(isolated_db, slug: str = TEMPLATE_SLUG) -> None:
    stages = [
        {"key": "review", "name": "Review", "stage_type": "parallel", "agent_id": ""},
        {"key": "implement", "name": "Implement", "stage_type": "classify",
         "agent_id": "backend_implementer", "classify_routes": ROUTES},
    ]  # fmt: skip
    with isolated_db.begin() as conn:
        conn.execute(text("DELETE FROM workflow_templates WHERE slug=:slug"), {"slug": slug})
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, "
                "source_path, version, built_in, created_at) "
                "VALUES ('tpl', :slug, 'L', '', :stages, '[]', 'test', 18, 0, '2026-01-01')"
            ),
            {"slug": slug, "stages": json.dumps(stages)},
        )


def _template(isolated_db) -> tuple[int, list[dict], list[str]]:
    with isolated_db.connect() as conn:
        version, raw = conn.execute(
            text("SELECT version, stages_json FROM workflow_templates WHERE id='tpl'")
        ).one()
        notes = conn.execute(
            text(
                "SELECT change_note FROM workflow_template_versions "
                "WHERE template_id='tpl' ORDER BY version"
            )
        ).scalars()
        notes = list(notes)
    implement = next(stage for stage in json.loads(raw) if stage["key"] == "implement")
    return version, implement["classify_routes"], notes


def test_the_route_is_removed_once_and_snapshotted(isolated_db):
    _insert(isolated_db)
    with isolated_db.begin() as conn:
        m_loregarden_implement_no_gdscript(conn)
        m_loregarden_implement_no_gdscript(conn)

    version, routes, notes = _template(isolated_db)
    assert version == 19
    assert [(r["agent_id"], r.get("skill_name", "")) for r in routes] == [
        ("frontend_implementer", ""),
        ("backend_implementer", "refactor"),
        ("backend_implementer", ""),
    ]
    assert len(notes) == 1


def test_another_workflow_keeps_its_gdscript_route(isolated_db):
    _insert(isolated_db, slug="blobert-tdd-copy")
    with isolated_db.begin() as conn:
        m_loregarden_implement_no_gdscript(conn)

    version, routes, notes = _template(isolated_db)
    assert version == 18
    assert routes[0]["agent_id"] == AGENT_ID
    assert notes == []
