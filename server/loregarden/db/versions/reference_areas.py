"""Project areas for the reference library (`models.domain.survey_area_tables`).

Creates `survey_areas` and `survey_source_areas`, and seeds each known
workspace's areas once, from its layout as of 2026-11-10: loremaker's Django
apps by their code names, blobert's asset pipeline and game, lore-eden's two
packages, loregarden's own subsystems. A workspace that does not exist here is
skipped; a slug already present is left as the operator edited it. The seed is
frozen in this module on purpose: changing it later must not change what this
migration did.
"""

from __future__ import annotations

import json
from uuid import uuid4

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from loregarden.models.domain.enums import utcnow
from sqlalchemy import Connection, text

#: workspace slug -> (slug, name, description, keywords)
_SEED: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
    "loremaker": (
        ("stratos", "Stratos", "Structured data models and projects, built on the XEditor framework.",
         ("data model", "schema", "structured data", "project management")),
        ("xeditor", "XEditor", "The visual editor framework: canvas, flow and form editing that Stratos extends.",
         ("editor", "canvas", "diagram", "flow", "node graph", "wysiwyg", "codemirror")),
        ("dexter", "Dexter", "AI integration: model calls, agents and AI-assisted features.",
         ("llm", "ai", "agent", "embedding", "rag", "prompt", "model")),
        ("atlas", "Atlas", "Project registry: discovery, composition, connections and ownership across projects.",
         ("registry", "catalog", "discovery", "service map", "ownership", "composition")),
        ("terminus", "Terminus", "Workflow execution engine: canonical workflows, integration connectors, credential vault.",
         ("workflow engine", "execution", "integration", "connector", "credentials", "automation")),
        ("ops", "Operations", "How loremaker is built, deployed and watched: docker, CI, logs, metrics, uptime.",
         ("observability", "monitoring", "logging", "tracing", "metrics", "ci", "cd", "deploy", "docker",
          "uptime", "self-hosted", "status page")),
    ),
    "blobert": (
        ("enemy-generator", "Enemy generator", "Procedural Blender pipeline producing animated enemy GLBs, materials and combat data.",
         ("blender", "procedural", "3d model", "mesh", "animation", "rigging", "material", "glb", "texture")),
        ("asset-web", "Asset web tool", "The web frontend and backend for browsing and generating game assets.",
         ("asset browser", "asset pipeline", "web ui", "preview", "content tool")),
        ("gameplay", "Gameplay", "The Godot game: 3D platformer movement, mutation, enemies and game feel.",
         ("godot", "gdscript", "platformer", "physics", "game feel", "level design", "game")),
        ("dev-tooling", "Game dev tooling", "Build, test and CI tooling around the Godot project and asset exports.",
         ("ci", "headless test", "build", "export", "automation", "mcp")),
    ),
    "lore-eden": (
        ("harness", "Python harness", "lore_eden: shared agent runner, ledger, workflow, store, cache and MCP plumbing.",
         ("agent runner", "ledger", "workflow", "mcp", "cache", "store", "library")),
        ("ui-kit", "UI kit", "The shared TypeScript UI kit: components, theme tokens, layout, panes and sockets.",
         ("components", "design system", "theme", "tokens", "react", "ui kit", "layout")),
        ("gates", "Shared gates", "Gate scripts and policy shared across workspaces.",
         ("lint", "gate", "policy", "pre-commit", "ci check")),
    ),
    "loregarden": (
        ("orchestration", "Orchestration", "Ticket pipelines, stage dispatch, agent runs and recovery.",
         ("orchestration", "agents", "pipeline", "scheduler", "dispatch", "workflow")),
        ("memory", "Memory layer", "What agents learn: learnings, memory graph, retrieval and the reference library.",
         ("memory", "knowledge graph", "retrieval", "rag", "embedding")),
        ("operator-ui", "Operator UI", "The React operator surfaces and Hive.",
         ("ui", "ux", "dashboard", "react", "design system")),
        ("gates-ci", "Gates and CI", "Lefthook gates, the merge queue, CI and verification.",
         ("ci", "gate", "merge queue", "test", "verification", "git forge", "code review")),
        ("capacity-ops", "Capacity and ops", "The capacity ledger, docker pools, instances, health and monitoring.",
         ("observability", "monitoring", "logging", "metrics", "tracing", "uptime", "docker", "self-hosted")),
    ),
}  # fmt: skip


@migration("20261110_reference_areas", after="20261110_repair_clears_gate_failures")
def m_reference_areas(conn: Connection) -> None:
    if not table_exists(conn, "survey_areas"):
        conn.execute(
            text(
                "CREATE TABLE survey_areas ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "workspace_id VARCHAR NOT NULL REFERENCES workspaces(id), "
                "slug VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, "
                "description VARCHAR NOT NULL DEFAULT '', "
                "keywords_json VARCHAR NOT NULL DEFAULT '[]', "
                "home_ticket_id VARCHAR REFERENCES tickets(id), "
                "archived_at DATETIME, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "CONSTRAINT uq_survey_area_slug UNIQUE (workspace_id, slug))"
            )
        )
    if not index_exists(conn, "ix_survey_areas_workspace_id"):
        conn.execute(
            text("CREATE INDEX ix_survey_areas_workspace_id ON survey_areas (workspace_id)")
        )
    if not table_exists(conn, "survey_source_areas"):
        conn.execute(
            text(
                "CREATE TABLE survey_source_areas ("
                "source_id VARCHAR NOT NULL REFERENCES survey_sources(id), "
                "area_id VARCHAR NOT NULL REFERENCES survey_areas(id), "
                "reason VARCHAR NOT NULL DEFAULT '', "
                "added_at DATETIME NOT NULL, "
                "PRIMARY KEY (source_id, area_id))"
            )
        )
    if not index_exists(conn, "ix_survey_source_areas_area_id"):
        conn.execute(
            text("CREATE INDEX ix_survey_source_areas_area_id ON survey_source_areas (area_id)")
        )
    _seed(conn)


def _seed(conn: Connection) -> None:
    # A partial schema (a migration test's) has no workspaces to seed for.
    if not table_exists(conn, "workspaces"):
        return
    now = utcnow()
    for workspace_slug, areas in _SEED.items():
        workspace_id = conn.execute(
            text("SELECT id FROM workspaces WHERE slug = :slug"), {"slug": workspace_slug}
        ).scalar()
        if workspace_id is None:
            continue
        for slug, name, description, keywords in areas:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO survey_areas (id, workspace_id, slug, name, description, "
                    "keywords_json, created_at, updated_at) VALUES (:id, :ws, :slug, :name, :desc, "
                    ":kw, :now, :now)"
                ),
                {
                    "id": str(uuid4()), "ws": workspace_id, "slug": slug, "name": name,
                    "desc": description, "kw": json.dumps(list(keywords)), "now": now,
                },
            )  # fmt: skip
