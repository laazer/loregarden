"""Whether a routing hint may override a stage's declared agent is the stage's
own business, not a property of how it happens to be named.

`studio_routing` used to decide this with a hardcoded set of stage keys. That
encoded one workspace's naming into routing logic every workspace runs: a new
template keyed `implementation` inherited the override by accident, and a
template that wanted the behaviour under any other name could not have it.
"""

import json

from loregarden.db.migrations_templates import m_agent_is_default_stages
from loregarden.models.domain import (
    StudioWorkflowCreate,
    StudioWorkflowStage,
    WorkflowStageDef,
    WorkflowTemplate,
)
from loregarden.services.studio_routing import (
    LEGACY_DEFAULT_AGENT_STAGES,
    agent_is_overridable,
)
from loregarden.services.studio_service import StudioService
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select


def _stage(**kwargs) -> WorkflowStageDef:
    base = {"key": "impl", "name": "Impl", "order": 1, "agent_id": "backend_implementer"}
    return WorkflowStageDef(**{**base, **kwargs})


# --- AC1 / AC3: the flag decides, and absence keeps the declared agent --------


def test_a_stage_carrying_the_flag_lets_a_hint_override_it():
    assert agent_is_overridable(_stage(key="anything-at-all", agent_is_default=True))


def test_a_stage_without_the_flag_keeps_its_declared_agent():
    """AC3, and the failure the branch exists to prevent. `next_agent` is sticky:
    on a standalone start nothing refreshes it, so it can still hold the PREVIOUS
    stage's agent — which is how run_43ea0c ran `learning` under `ac_gatekeeper`.
    """
    assert not agent_is_overridable(_stage(key="learning", agent_id="learning_agent"))


def test_the_behaviour_is_no_longer_tied_to_the_stage_key():
    """The point of the ticket. A stage named `implementation` that does NOT want
    the override can now say so — impossible while a key set decided it."""
    assert agent_is_overridable(_stage(key="implementation"))  # legacy fallback
    assert not agent_is_overridable(_stage(key="ships-its-own-agent", agent_id="verifier"))


def test_the_legacy_key_fallback_covers_snapshots_written_before_the_flag():
    """Version-pinned instances resolve stages from a snapshot that will never
    grow the field. 16 open tickets were pinned to `loregarden-tdd` when this
    landed, all on `planning` and yet to reach `implementation`; without the
    fallback they would have lost the override on the way through.

    Same reasoning as `is_terminal_stage`'s `key == "done"` fallback.
    """
    assert LEGACY_DEFAULT_AGENT_STAGES == {"implementation", "implement"}
    for key in LEGACY_DEFAULT_AGENT_STAGES:
        assert agent_is_overridable(_stage(key=key, agent_is_default=False))


def test_backend_and_frontend_impl_are_still_not_overridable():
    """They look like they belong to the legacy set and deliberately do not: they
    have already made the specialist choice, so a stale frontend hint would run
    the backend stage (lg-workflow-integrity-102)."""
    assert not agent_is_overridable(_stage(key="backend-impl"))
    assert not agent_is_overridable(_stage(key="frontend-impl"))


# --- AC4: the field survives a publish ---------------------------------------


def test_the_flag_survives_a_studio_publish(db_session: Session):
    """AC4. Publish builds its stage dict from `model_dump()` since
    lg-workflow-integrity-559, so a new field travels automatically — worth
    asserting precisely because the same change before 559 would have dropped it
    silently."""
    svc = StudioService(db_session)
    svc.create_workflow(
        StudioWorkflowCreate(
            slug="hintable",
            name="Hintable",
            stages=[
                StudioWorkflowStage(
                    key="impl",
                    name="Impl",
                    order=1,
                    agent_id="backend_implementer",
                    agent_is_default=True,
                ),
                StudioWorkflowStage(key="done", name="Done", order=2, terminal=True),
            ],
        )
    )
    svc.publish_workflow("hintable")

    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-hintable")
    ).one()
    stages = {s["key"]: s for s in json.loads(template.stages_json)}
    assert stages["impl"]["agent_is_default"] is True
    assert stages["done"].get("agent_is_default") is False


# --- AC2: the migration preserves today's behaviour --------------------------


def _template(engine, slug: str, stages: list[dict]) -> None:
    from datetime import datetime, timezone

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, source_path, "
                "version, built_in, created_at) "
                "VALUES (:id, :slug, :name, '', :st, '[]', '', 1, 1, :now)"
            ),
            {
                "id": slug,
                "slug": slug,
                "name": slug,
                "st": json.dumps(stages),
                "now": datetime.now(timezone.utc),
            },
        )


def _stages_of(engine, slug: str) -> dict[str, dict]:
    with engine.begin() as conn:
        raw = conn.execute(
            text("SELECT stages_json FROM workflow_templates WHERE slug=:s"), {"s": slug}
        ).scalar_one()
    return {s["key"]: s for s in json.loads(raw)}


def test_the_migration_marks_exactly_the_stages_that_reach_the_branch(tmp_path):
    """AC2. These three were measured against the live templates. The two that
    look like the obvious cases are classify stages that return earlier and are
    unaffected either way."""
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    SQLModel.metadata.create_all(engine)
    _template(engine, "loregarden-tdd", [{"key": "implementation", "name": "I", "order": 1}])
    _template(engine, "blobert-tdd", [{"key": "implementation", "name": "I", "order": 1}])

    with engine.begin() as conn:
        m_agent_is_default_stages(conn)

    assert _stages_of(engine, "loregarden-tdd")["implementation"]["agent_is_default"] is True
    # Not in the mapping: its `implementation` is a classify stage that never
    # reaches the branch, so marking it would change what it means.
    assert "agent_is_default" not in _stages_of(engine, "blobert-tdd")["implementation"]


def test_the_migration_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    SQLModel.metadata.create_all(engine)
    _template(engine, "loregarden-tdd", [{"key": "implementation", "name": "I", "order": 1}])

    def version() -> int:
        with engine.begin() as conn:
            return conn.execute(
                text("SELECT version FROM workflow_templates WHERE slug='loregarden-tdd'")
            ).scalar_one()

    with engine.begin() as conn:
        m_agent_is_default_stages(conn)
    first = version()
    with engine.begin() as conn:
        m_agent_is_default_stages(conn)
    assert version() == first
