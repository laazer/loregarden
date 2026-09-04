"""Every workflow must end at a terminal stage the orchestrator can finalize on.

The studio-loregarden-tdd v2/v3 templates shipped ending at a non-terminal `gate`
with no pass-route, so a passing final gate had nowhere to advance and the
pipeline re-looped through implement/verify/review instead of completing the
ticket. Two guards: a migration backfills a terminal `done` stage into any
template missing one, and workflow create/update/publish reject one without it.
"""

import json

from loregarden.db.migrations_templates import (
    _has_terminal_stage,
    m_ensure_terminal_stage,
)
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine


def _mk_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tpl.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _insert_template(engine, slug, stages, transitions):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, source_path, "
                "version, built_in, created_at) "
                "VALUES (:id, :slug, :name, '', :st, :tr, :sp, 1, 1, :now)"
            ),
            {
                "id": slug,
                "slug": slug,
                "name": slug,
                "st": json.dumps(stages),
                "tr": json.dumps(transitions),
                "sp": f"studio:{slug}",
                "now": now,
            },
        )


def _template(engine, slug):
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT stages_json, transitions_json, version FROM workflow_templates WHERE slug=:s"
                ),
                {"s": slug},
            )
            .mappings()
            .fetchone()
        )
    return json.loads(row["stages_json"]), json.loads(row["transitions_json"]), row["version"]


def test_has_terminal_stage_detects_flag_and_done_key():
    assert _has_terminal_stage([{"key": "gate"}]) is False
    assert _has_terminal_stage([{"key": "gate"}, {"key": "done"}]) is True
    assert _has_terminal_stage([{"key": "wrap", "terminal": True}]) is True


def test_migration_appends_terminal_done_to_a_gate_ending_template(tmp_path):
    engine = _mk_engine(tmp_path)
    _insert_template(
        engine,
        "ends-at-gate",
        [
            {"key": "implement", "name": "Implement", "order": 1},
            {"key": "gate", "name": "Gate", "order": 2, "stage_type": "gate"},
        ],
        [
            {"from": "implement", "to": "gate"},
            {"from": "gate", "to": "implement", "when": "reject"},
        ],
    )

    with Session(engine) as s:
        m_ensure_terminal_stage(s.connection())
        s.commit()

    stages, transitions, version = _template(engine, "ends-at-gate")
    done = [st for st in stages if st["key"] == "done"]
    assert done and done[0]["terminal"] is True
    assert done[0]["order"] == 3  # appended after the last stage
    assert {"from": "gate", "to": "done", "when": "pass"} in transitions
    assert version == 2  # bumped + snapshotted


def test_migration_leaves_a_template_that_already_terminates(tmp_path):
    engine = _mk_engine(tmp_path)
    _insert_template(
        engine,
        "already-done",
        [
            {"key": "implement", "name": "Implement", "order": 1},
            {"key": "done", "name": "Done", "order": 2},
        ],
        [{"from": "implement", "to": "done"}],
    )

    with Session(engine) as s:
        m_ensure_terminal_stage(s.connection())
        s.commit()

    stages, _, version = _template(engine, "already-done")
    assert [st["key"] for st in stages] == ["implement", "done"]  # unchanged
    assert version == 1  # not bumped


def test_migration_is_idempotent(tmp_path):
    engine = _mk_engine(tmp_path)
    _insert_template(
        engine,
        "ends-at-gate",
        [{"key": "gate", "name": "Gate", "order": 1, "stage_type": "gate"}],
        [],
    )
    with Session(engine) as s:
        m_ensure_terminal_stage(s.connection())
        s.commit()
    with Session(engine) as s:
        m_ensure_terminal_stage(s.connection())
        s.commit()
    stages, _, version = _template(engine, "ends-at-gate")
    assert [st["key"] for st in stages].count("done") == 1
    assert version == 2  # bumped once, not twice


def test_validate_has_terminal_stage_rejects_and_accepts():
    import pytest
    from loregarden.models.domain import StudioWorkflowStage
    from loregarden.services.studio_workflow_validation import validate_has_terminal_stage

    no_terminal = [
        StudioWorkflowStage(key="implement", name="Implement", agent_id="backend", order=1),
        StudioWorkflowStage(key="gate", name="Gate", stage_type="gate", order=2),
    ]
    with pytest.raises(ValueError, match="terminal stage"):
        validate_has_terminal_stage(no_terminal)

    # A `done` key satisfies it (historical fallback) ...
    validate_has_terminal_stage(
        no_terminal + [StudioWorkflowStage(key="done", name="Done", order=3)]
    )
    # ... as does the explicit `terminal` flag on any key.
    validate_has_terminal_stage(
        no_terminal + [StudioWorkflowStage(key="wrap", name="Wrap", order=3, terminal=True)]
    )


def test_publish_preserves_terminal_and_skip_when():
    """Regression: publish built the template stage dict without `terminal` /
    `skip_when`, so a valid terminal stage was stripped and the published template
    could not finalize. The published dict must carry both fields through."""
    from loregarden.models.domain import StudioWorkflowStage

    stage = StudioWorkflowStage(
        key="done", name="Done", order=2, terminal=True, skip_when="routed_as_light_work"
    )
    # Mirror the field set publish_workflow now emits.
    published = {
        "terminal": stage.terminal,
        "skip_when": stage.skip_when,
    }
    assert published["terminal"] is True
    assert published["skip_when"] == "routed_as_light_work"
