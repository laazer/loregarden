"""Migration 0114 renames the stage-key forks everywhere they are stored.

The interesting part of `lg-workflow-integrity-660` is not the rename, it is the
surface count. Its AC2 names four places a stage key lives; measuring the live
database found nine that must move and two more that must NOT, because they share
the spellings while belonging to other vocabularies. A rename that misses one
strands a cursor on a key nothing declares any more.

The most valuable guard here is therefore not "did these five strings change" but
"is any stage-key column unaccounted for", which is the defect class this ticket
is an instance of.
"""

from __future__ import annotations

import json

import pytest
from loregarden.db.migrations_stage_keys import (
    CANONICAL_STAGE_RENAMES,
    STAGE_KEY_COLUMNS,
    m_canonical_stage_keys,
)
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

#: Columns whose name mentions a stage but which hold something else entirely.
#: Renaming any of these would corrupt a different vocabulary.
NOT_A_STAGE_KEY: frozenset[tuple[str, str]] = frozenset(
    {
        ("queued_runs", "max_stages"),  # a count of stages, not a key
        ("tickets", "workflow_stage_status"),  # a StageStatus
        ("stage_fanout_groups", "pre_fanout_workflow_stage_status"),  # a StageStatus
    }
)

#: Columns holding stage keys inside JSON, rewritten by the walkers rather than
#: by the bare-column loop.
JSON_CARRIERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("workflow_templates", "stages_json"),
        ("studio_workflows", "stages_json"),
        ("workflow_instances", "stages_json"),
        ("stage_fanout_groups", "pre_fanout_stage_map_json"),
    }
)


@pytest.fixture(name="forked_db")
def forked_db_fixture(tmp_path):
    """A database carrying the old spellings on every surface the migration moves.

    Built with raw DDL rather than the models: this exercises the migration's own
    SQL, and a fixture that had to satisfy every foreign key would be a fixture
    whose failures `xfail`-style silence could hide.
    """
    path = tmp_path / "forked.db"
    engine = create_engine(f"sqlite:///{path}")
    stage_map = json.dumps([{"key": "implementation", "status": "pending"}])
    template_stages = json.dumps(
        [
            {"key": "planning", "classify_routes": [{"to_stage": "test_design"}]},
            {"key": "implementation"},
        ]
    )
    transitions = json.dumps([{"from": "planning", "to": "implementation", "when": "pass"}])
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id TEXT, workflow_stage_key TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE workflow_instances "
                "(id TEXT, current_stage_key TEXT, stages_json TEXT)"
            )
        )
        conn.execute(text("CREATE TABLE agent_runs (id TEXT, stage_key TEXT, skill_name TEXT)"))
        conn.execute(text("CREATE TABLE approvals (id TEXT, stage_key TEXT)"))
        conn.execute(text("CREATE TABLE artifacts (id TEXT, kind TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE workflow_templates (id TEXT, stages_json TEXT, transitions_json TEXT)"
            )
        )
        conn.execute(
            text("CREATE TABLE studio_workflows (id TEXT, stages_json TEXT, transitions_json TEXT)")
        )
        conn.execute(text("CREATE TABLE workflow_template_versions (id TEXT, snapshot_json TEXT)"))
        conn.execute(text("INSERT INTO tickets VALUES ('t1', 'implementation')"))
        conn.execute(
            text("INSERT INTO workflow_instances VALUES ('i1', 'implementation', :m)"),
            {"m": stage_map},
        )
        # `skill_name` carries the same spelling and is a different vocabulary.
        conn.execute(text("INSERT INTO agent_runs VALUES ('r1', 'test_design', 'test_design')"))
        conn.execute(text("INSERT INTO approvals VALUES ('a1', 'planning')"))
        # An agent-chosen artifact kind, which 613 deliberately leaves open.
        conn.execute(text("INSERT INTO artifacts VALUES ('f1', 'specification')"))
        conn.execute(
            text("INSERT INTO workflow_templates VALUES ('w1', :s, :t)"),
            {"s": template_stages, "t": transitions},
        )
        conn.execute(
            text("INSERT INTO studio_workflows VALUES ('d1', :s, :t)"),
            {"s": template_stages, "t": transitions},
        )
        conn.execute(
            text("INSERT INTO workflow_template_versions VALUES ('v1', :snap)"),
            {"snap": json.dumps({"stages_json": template_stages, "transitions_json": transitions})},
        )
    with engine.begin() as conn:
        m_canonical_stage_keys(conn)
    return engine


def _one(engine, sql: str):
    with engine.begin() as conn:
        return conn.execute(text(sql)).scalar()


def test_bare_stage_key_columns_are_renamed(forked_db):
    assert _one(forked_db, "SELECT workflow_stage_key FROM tickets") == "implement"
    assert _one(forked_db, "SELECT current_stage_key FROM workflow_instances") == "implement"
    assert _one(forked_db, "SELECT stage_key FROM agent_runs") == "test-design"
    assert _one(forked_db, "SELECT stage_key FROM approvals") == "plan"


def test_stage_keys_inside_json_are_renamed(forked_db):
    stages = json.loads(_one(forked_db, "SELECT stages_json FROM workflow_instances"))
    assert [s["key"] for s in stages] == ["implement"]

    template = json.loads(_one(forked_db, "SELECT stages_json FROM workflow_templates"))
    assert [s["key"] for s in template] == ["plan", "implement"]
    # A classify route names a stage too.
    assert template[0]["classify_routes"][0]["to_stage"] == "test-design"

    transitions = json.loads(_one(forked_db, "SELECT transitions_json FROM workflow_templates"))
    assert (transitions[0]["from"], transitions[0]["to"]) == ("plan", "implement")


def test_the_studio_draft_is_renamed_too(forked_db):
    """A draft left on the old spellings republishes the forks straight back over
    the renamed template — the drift lg-workflow-integrity-561 fixed."""
    stages = json.loads(_one(forked_db, "SELECT stages_json FROM studio_workflows"))
    assert [s["key"] for s in stages] == ["plan", "implement"]


def test_version_snapshots_are_renamed(forked_db):
    """100 live instances pin a version whose snapshot carried the old spellings.
    A pinned instance resolves its stages through the snapshot, not the template,
    so skipping this strands exactly those cursors."""
    snapshot = json.loads(_one(forked_db, "SELECT snapshot_json FROM workflow_template_versions"))
    assert [s["key"] for s in json.loads(snapshot["stages_json"])] == ["plan", "implement"]
    assert json.loads(snapshot["transitions_json"])[0]["from"] == "plan"


def test_other_vocabularies_keep_their_spelling(forked_db):
    """`artifacts.kind` and `agent_runs.skill_name` share these spellings and mean
    something else — an agent-chosen artifact kind and a skill. This migration has
    no authority over either."""
    assert _one(forked_db, "SELECT kind FROM artifacts") == "specification"
    assert _one(forked_db, "SELECT skill_name FROM agent_runs") == "test_design"


def test_every_stage_key_column_is_accounted_for():
    """The failure this ticket is an instance of: a surface nobody listed.

    AC2 named four; nine needed moving. So rather than trusting a hand-written
    list, walk the schema and require every stage-ish column to be classified —
    migrated, JSON-carried, or explicitly not a stage key. A new table with a
    `stage_key` fails here instead of silently keeping a fork alive.
    """
    unclassified = []
    for table_name, table in SQLModel.metadata.tables.items():
        for column in table.columns:
            if "stage" not in column.name.lower():
                continue
            pair = (table_name, column.name)
            if pair in set(STAGE_KEY_COLUMNS) | JSON_CARRIERS | NOT_A_STAGE_KEY:
                continue
            unclassified.append(pair)

    assert not unclassified, (
        "These columns mention a stage but are in none of the migration's lists:\n  "
        + "\n  ".join(f"{t}.{c}" for t, c in unclassified)
        + "\n\nAdd each to STAGE_KEY_COLUMNS (a bare key), JSON_CARRIERS (a key "
        "inside JSON), or NOT_A_STAGE_KEY (it means something else)."
    )


def test_the_specialist_stages_are_not_folded_in():
    """AC3. `backend-impl` and `frontend-impl` make the agent choice the generic
    stage defers; folding them into `implement` would erase that distinction."""
    assert "backend-impl" not in CANONICAL_STAGE_RENAMES
    assert "frontend-impl" not in CANONICAL_STAGE_RENAMES
    assert set(CANONICAL_STAGE_RENAMES.values()) == {
        "plan",
        "spec",
        "test-design",
        "test-break",
        "implement",
    }
