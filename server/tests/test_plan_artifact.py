"""The plan as a retrievable artifact, and its reach into later stages."""

import json
from uuid import uuid4

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.plan_context import (
    ARTIFACT_KIND,
    MAX_PLAN_CHARS,
    build_plan_context,
    latest_plan,
)
from loregarden.config import settings
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import AgentRun, Artifact, Ticket, WorkflowStageDef, Workspace
from loregarden.services.seed import seed_database
from loregarden.services.studio_routing import VERIFY_STAGE_TYPE
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.skills.registry import SKILL_PROMPT_CAP, get_skill, list_skills
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _attach_plan(session: Session, ticket: Ticket, content: dict) -> Artifact:
    artifact = Artifact(
        ticket_id=ticket.id,
        kind=ARTIFACT_KIND,
        title="Plan",
        content_json=json.dumps(content),
    )
    session.add(artifact)
    session.commit()
    return artifact


def test_the_plan_skill_is_registered_and_fits_the_prompt():
    assert "plan" in list_skills()
    source = settings.agent_context_dir / "skills" / "plan" / "SKILL.md"
    body = source.read_text(encoding="utf-8")
    assert len(body) <= SKILL_PROMPT_CAP, f"{source.name} is {len(body)} chars"
    assert get_skill("plan") == body


def test_the_skill_names_the_artifact_contract():
    """The whole point of the skill is telling the planner where the plan goes."""
    body = get_skill("plan") or ""
    assert "loregarden_attach_artifact" in body
    assert f'kind="{ARTIFACT_KIND}"' in body


def test_no_plan_yields_no_block():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        assert build_plan_context(session, ticket, "spec") == ""


def test_the_newest_plan_supersedes_an_earlier_one():
    """A reroute back through planning replaces the plan that was rejected;
    showing both would leave the next stage to guess which one holds."""
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        _attach_plan(session, ticket, {"approach": "first attempt"})
        _attach_plan(session, ticket, {"approach": "revised after rework"})

        assert latest_plan(session, ticket) == {"approach": "revised after rework"}
        assert "revised after rework" in build_plan_context(session, ticket, "spec")
        assert "first attempt" not in build_plan_context(session, ticket, "spec")


def test_the_plan_stage_is_not_shown_the_plan_it_is_writing():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        _attach_plan(session, ticket, {"approach": "x"})
        assert build_plan_context(session, ticket, "plan") == ""
        assert build_plan_context(session, ticket, "spec") != ""


def test_an_unexpected_plan_shape_is_still_carried():
    """The artifact is free-form JSON, so the renderer cannot assume a schema —
    a plan the planner shaped differently is still worth passing on."""
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        _attach_plan(
            session,
            ticket,
            {"steps": ["one", "two"], "risk_count": 3, "seams": {"module": "services/x.py"}},
        )
        rendered = build_plan_context(session, ticket, "spec")
        assert "- one" in rendered and "- two" in rendered
        assert "**Risk count:** 3" in rendered
        assert "services/x.py" in rendered


def test_a_malformed_plan_artifact_is_skipped_not_fatal():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        session.add(
            Artifact(ticket_id=ticket.id, kind=ARTIFACT_KIND, title="Plan", content_json="{oops")
        )
        session.commit()
        _attach_plan(session, ticket, {"approach": "good one"})
        assert "good one" in build_plan_context(session, ticket, "spec")


def test_a_long_plan_is_capped():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        _attach_plan(session, ticket, {"approach": "x" * (MAX_PLAN_CHARS * 2)})
        assert len(build_plan_context(session, ticket, "spec")) == MAX_PLAN_CHARS


def _prompt_for_stage(session: Session, ticket: Ticket, stage_key: str) -> str:
    workspace = session.get(Workspace, ticket.workspace_id)
    run = AgentRun(
        run_code="run_plan",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="spec",
        stage_key=stage_key,
    )
    executor = CliAgentExecutor(session)
    return executor._build_prompt(
        ticket,
        run,
        {"role_file": "agents/9_static_qa/static_qa_v1.md"},
        resolve_agent_context_dir(workspace),
        workspace,
        executor._resolve_stage_def(ticket, run),
    )


def test_a_later_stage_prompt_carries_the_plan():
    """Retrieval is worth nothing if the prompt never includes it."""
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        _attach_plan(session, ticket, {"approach": "split the resolver out of the service"})

        prompt = _prompt_for_stage(session, ticket, "spec")
        assert "## Plan (settled by the plan stage)" in prompt
        assert "split the resolver out of the service" in prompt


def test_a_verifier_is_not_shown_the_plan():
    """The plan is the reasoning behind the claim under review. A verifier that
    reads it is checking the work against its own author's argument."""
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        _attach_plan(session, ticket, {"approach": "split the resolver out of the service"})

        workspace = session.get(Workspace, ticket.workspace_id)
        run = AgentRun(
            run_code="run_v",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="verifier",
            stage_key="verify",
        )
        executor = CliAgentExecutor(session)
        prompt = executor._build_prompt(
            ticket,
            run,
            {"role_body": "role"},
            resolve_agent_context_dir(workspace),
            workspace,
            WorkflowStageDef(key="verify", name="Verify", order=9, stage_type=VERIFY_STAGE_TYPE),
        )
        assert "## Plan (settled by the plan stage)" not in prompt
        assert "split the resolver out of the service" not in prompt


def test_the_migration_sets_the_skill_but_leaves_an_operator_choice_alone(tmp_path):
    for stage_skill, expected in (("", "plan"), ("custom", "custom")):
        name = stage_skill or "none"
        engine = create_engine(f"sqlite:///{tmp_path / ('tpl-' + name + '.db')}")
        SQLModel.metadata.create_all(engine)
        template_id = str(uuid4())
        stages = [{"key": "plan", "name": "Plan", "order": 2, "agent_id": "planner"}]
        if stage_skill:
            stages[0]["skill_name"] = stage_skill
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workflow_templates (id, slug, name, description, stages_json, "
                    "transitions_json, source_path, created_at, version, built_in) VALUES "
                    "(:id, 'studio-loregarden-tdd-v3', 'v3', '', :st, '[]', '', :now, 1, 0)"
                ),
                {"id": template_id, "st": json.dumps(stages), "now": "2026-01-01T00:00:00"},
            )
        apply_migrations(engine)

        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT stages_json FROM workflow_templates WHERE id=:id"), {"id": template_id}
            ).scalar_one()
        assert json.loads(stored)[0].get("skill_name") == expected
