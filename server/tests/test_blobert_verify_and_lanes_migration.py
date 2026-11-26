"""blobert-tdd gains verify and two review lanes, and no ticket in flight loses its stage."""

from __future__ import annotations

import json

import pytest
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.versions.blobert_verify_and_lanes import (
    NEW_LANES,
    TEMPLATE,
    m_blobert_verify_and_lanes,
)
from loregarden.models.domain import TicketState, WorkflowTemplateVersion, Workspace
from loregarden.services.finding_bar import JUDGING_AGENTS
from loregarden.services.read_evidence import CHECKED_AGENTS
from loregarden.services.studio_service import StudioService, seed_builtin_agents
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, select
from tests.template_move_helpers import (
    bound_workspace,
    current_stage_after_a_read,
    forget_template,
    instance_of,
    pass_stage,
    template,
    ticket_on,
)

LANES = ["gdscript_reviewer", "static_qa", "architecture_reviewer", *NEW_LANES]


@pytest.fixture
def blobert(db_session: Session) -> Workspace:
    forget_template(db_session, TEMPLATE)  # re-seeded from its YAML, which has neither change
    return bound_workspace(db_session, "blobert", TEMPLATE)


def _migrate(isolated_db, session: Session) -> None:
    session.commit()
    with isolated_db.begin() as conn:
        m_blobert_verify_and_lanes(conn)
    session.expire_all()


def _keys(session: Session) -> list[str]:
    stages = json.loads(template(session, TEMPLATE).stages_json)
    return [stage["key"] for stage in sorted(stages, key=lambda s: s["order"])]


def test_the_migration_is_one_snapshotted_version_however_often_it_runs(
    isolated_db, db_session: Session, blobert: Workspace
):
    before = template(db_session, TEMPLATE)
    old_version, old_keys = before.version, _keys(db_session)

    _migrate(isolated_db, db_session)
    _migrate(isolated_db, db_session)

    row = template(db_session, TEMPLATE)
    assert row.version == old_version + 1
    at = old_keys.index("implement") + 1
    assert _keys(db_session) == [*old_keys[:at], "verify", *old_keys[at:]]

    stages = {stage["key"]: stage for stage in json.loads(row.stages_json)}
    assert [lane["agent_id"] for lane in stages["script_review"]["parallel_agents"]] == LANES
    assert "Hunt regressions" in stages["script_review"]["stage_brief"]
    assert stages["verify"]["agent_id"] == "verifier"
    assert stages["verify"]["required_evidence"] == ["verify_verdict"]

    snapshot = db_session.exec(
        select(WorkflowTemplateVersion).where(
            WorkflowTemplateVersion.template_id == row.id,
            WorkflowTemplateVersion.version == row.version,
        )
    ).one()
    assert snapshot.created_by == "migration"
    assert json.loads(snapshot.snapshot_json)["stages_json"] == row.stages_json


def test_every_edge_names_a_stage_and_implement_reaches_review_through_verify(
    isolated_db, db_session: Session, blobert: Workspace
):
    _migrate(isolated_db, db_session)

    edges = json.loads(template(db_session, TEMPLATE).transitions_json)
    assert {e["from"] for e in edges} | {e["to"] for e in edges} <= set(_keys(db_session))
    assert [e["to"] for e in edges if e["from"] == "implement"] == ["verify"]
    assert {(e["to"], e["when"]) for e in edges if e["from"] == "verify"} == {
        ("script_review", "pass"),
        ("implement", "reject"),
    }


def test_a_template_a_person_reshaped_is_left_alone_and_said_so(
    isolated_db, db_session: Session, blobert: Workspace, caplog
):
    row = template(db_session, TEMPLATE)
    stages = [s for s in json.loads(row.stages_json) if s["key"] != "script_review"]
    row.stages_json = json.dumps(stages)
    db_session.add(row)
    version = row.version

    _migrate(isolated_db, db_session)

    assert template(db_session, TEMPLATE).version == version
    assert "leaving it as it is" in caplog.text


# ---- tickets in flight ---------------------------------------------------------------


@pytest.mark.parametrize("pinned", [True, False])
@pytest.mark.parametrize(
    ("stage_key", "next_key"),
    [
        ("implement", "script_review"),
        ("script_review", "ac_gate"),
        ("ac_gate", "playtest"),
        ("playtest", "learning"),
    ],
)
def test_an_in_progress_ticket_keeps_a_valid_current_stage(
    isolated_db,
    db_session: Session,
    blobert: Workspace,
    stage_key: str,
    next_key: str,
    pinned: bool,
):
    """An unpinned ticket past implement would otherwise meet a pending verify behind it."""
    old_version, keys = template(db_session, TEMPLATE).version, _keys(db_session)
    ticket = ticket_on(
        db_session,
        blobert,
        f"{stage_key}-{pinned}",
        state=TicketState.IN_PROGRESS,
        stage_key=stage_key,
        statuses=dict.fromkeys(keys[: keys.index(stage_key)], "done"),
        pinned=pinned,
    )

    _migrate(isolated_db, db_session)

    assert instance_of(db_session, ticket).template_version == old_version
    assert current_stage_after_a_read(db_session, ticket) == stage_key
    _, stages = resolve_ticket_stages(db_session, ticket)
    assert [stage.key for stage in stages] == keys
    # The live template now says implement -> verify, a stage this ticket lacks.
    assert pass_stage(db_session, ticket, stage_key) == next_key


@pytest.mark.parametrize("pinned", [True, False])
def test_a_finished_ticket_stays_finished(
    isolated_db, db_session: Session, blobert: Workspace, pinned: bool
):
    keys = _keys(db_session)
    ticket = ticket_on(
        db_session,
        blobert,
        f"done-{pinned}",
        state=TicketState.DONE,
        stage_key="done",
        statuses=dict.fromkeys(keys, "done"),
        pinned=pinned,
    )

    _migrate(isolated_db, db_session)

    assert current_stage_after_a_read(db_session, ticket) == "done"
    assert ticket.state == TicketState.DONE


@pytest.mark.parametrize("pinned", [True, False])
def test_never_run_backlog_gets_the_new_stages(
    isolated_db, db_session: Session, blobert: Workspace, pinned: bool
):
    ticket = ticket_on(
        db_session,
        blobert,
        f"backlog-{pinned}",
        state=TicketState.BACKLOG,
        stage_key="plan",
        pinned=pinned,
    )

    _migrate(isolated_db, db_session)

    row = template(db_session, TEMPLATE)
    assert instance_of(db_session, ticket).template_version == row.version
    assert current_stage_after_a_read(db_session, ticket) == "plan"
    assert pass_stage(db_session, ticket, "implement") == "verify"


# ---- the Python reviewer -------------------------------------------------------------


def test_the_python_reviewer_is_seeded_with_a_role_scoped_to_blobert(db_session: Session):
    seed_builtin_agents(db_session)
    agent = next(a for a in StudioService(db_session).list_agents() if a.slug == "python_reviewer")
    body = (settings.agent_context_dir / AGENTS["python_reviewer"]["role_file"]).read_text()

    assert agent.built_in
    for path in ("asset_generation/python/", "asset_generation/web/backend/"):
        assert path in body
    assert "/server/" not in body
    assert "<<<LOREGARDEN_STAGE_REPORT>>>" in body


@pytest.mark.parametrize("agent", NEW_LANES)
def test_the_new_lanes_answer_for_their_verdicts_like_the_others(agent: str):
    assert agent in JUDGING_AGENTS
    assert agent in CHECKED_AGENTS
