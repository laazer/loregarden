"""Three planning lanes, and the stage that reconciles them."""

import json
from uuid import uuid4

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.plan_context import (
    ARTIFACT_KIND,
    SYNTHESIS_SKILL,
    build_plan_synthesis_context,
    round_plans,
)
from loregarden.config import settings
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ParallelAgentSpec,
    RunStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.seed import seed_database
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.skills.registry import SKILL_PROMPT_CAP, get_skill, list_skills
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

_LANES = ("plan-simplest", "plan-risk", "plan-seams")


def _passing_report() -> str:
    return (
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9}\n'
        "<<<END_STAGE_REPORT>>>"
    )


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _lane_run(session: Session, ticket: Ticket, skill: str, orch_id: str) -> AgentRun:
    run = AgentRun(
        run_code=f"run_{skill}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        skill_name=skill,
        stage_key="plan",
        orchestration_run_id=orch_id,
    )
    session.add(run)
    session.commit()
    return run


def _attach(session: Session, ticket: Ticket, run: AgentRun | None, content: dict) -> None:
    session.add(
        Artifact(
            ticket_id=ticket.id,
            run_id=run.id if run else None,
            kind=ARTIFACT_KIND,
            title="Plan",
            content_json=json.dumps(content),
        )
    )
    session.commit()


def test_every_lane_skill_is_registered_and_fits_the_prompt():
    for skill in (*_LANES, SYNTHESIS_SKILL):
        assert skill in list_skills(), skill
        source = settings.agent_context_dir / "skills" / skill / "SKILL.md"
        body = source.read_text(encoding="utf-8")
        assert len(body) <= SKILL_PROMPT_CAP, f"{skill} is {len(body)} chars"
        assert get_skill(skill) == body


def test_each_lane_is_told_to_attach_its_plan():
    """A lane whose plan stays in its reply is invisible to synthesis, so the
    contract has to be in every lane's own skill — only one skill reaches a run."""
    for skill in _LANES:
        body = get_skill(skill) or ""
        assert "loregarden_attach_artifact" in body, skill
        assert f'kind="{ARTIFACT_KIND}"' in body, skill


def test_lanes_are_labelled_by_their_lens():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        orch = str(uuid4())
        for skill in _LANES:
            _attach(session, ticket, _lane_run(session, ticket, skill, orch), {"approach": skill})

        labels = [label for label, _ in round_plans(session, ticket)]
        assert sorted(labels) == sorted(_LANES)


def test_an_earlier_round_is_not_mixed_into_this_one():
    """Lanes are grouped by orchestration run, so a prior round's plans do not
    reappear as if they were arguments in the current one."""
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()

        old = str(uuid4())
        _attach(session, ticket, _lane_run(session, ticket, "plan-risk", old), {"a": "stale"})
        new = str(uuid4())
        for skill in _LANES:
            _attach(
                session, ticket, _lane_run(session, ticket, skill, new), {"a": f"fresh-{skill}"}
            )

        plans = [content for _, content in round_plans(session, ticket)]
        assert len(plans) == 3
        assert {"a": "stale"} not in plans


def test_synthesis_context_carries_every_lane():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        orch = str(uuid4())
        for skill in _LANES:
            _attach(
                session,
                ticket,
                _lane_run(session, ticket, skill, orch),
                {"approach": f"argument from {skill}"},
            )

        rendered = build_plan_synthesis_context(session, ticket)
        for skill in _LANES:
            assert f"### Lane: {skill}" in rendered
            assert f"argument from {skill}" in rendered


def test_a_single_lane_is_not_worth_synthesizing():
    """Asking an agent to reconcile one plan just asks it to rewrite the plan."""
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()
        orch = str(uuid4())
        _attach(session, ticket, _lane_run(session, ticket, "plan-risk", orch), {"a": "only one"})
        assert build_plan_synthesis_context(session, ticket) == ""


def test_the_synthesizer_gets_the_lanes_and_not_a_settled_plan():
    with Session(_engine()) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        workspace = session.get(Workspace, ticket.workspace_id)
        orch = str(uuid4())
        for skill in _LANES:
            _attach(
                session,
                ticket,
                _lane_run(session, ticket, skill, orch),
                {"approach": f"argument from {skill}"},
            )

        run = AgentRun(
            run_code="run_syn",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            skill_name=SYNTHESIS_SKILL,
            stage_key="plan-synthesis",
        )
        executor = CliAgentExecutor(session)
        prompt = executor._build_prompt(
            ticket,
            run,
            {"role_body": "role"},
            resolve_agent_context_dir(workspace),
            workspace,
            executor._resolve_stage_def(ticket, run),
        )
        assert "## Plans to reconcile" in prompt
        assert "argument from plan-risk" in prompt
        # It is producing the settled plan, so it must not be handed one.
        assert "## Plan (settled by the plan stage)" not in prompt


def test_resume_tells_same_agent_lanes_apart():
    """Lanes are identified by agent *and* skill.

    All three planning lanes run the `planner` agent, so matching on agent alone
    let one finished lane mark its siblings complete — a crash mid-stage would
    resume with two lenses silently missing.
    """
    engine = _engine()
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket)).first()

        # Only the first lane got as far as succeeding before the interruption.
        done = _lane_run(session, ticket, "plan-simplest", str(uuid4()))
        done.status = RunStatus.SUCCEEDED
        done.stdout = _passing_report()
        session.add(done)
        session.commit()

        stage_def = WorkflowStageDef(
            key="plan",
            name="Plan",
            order=2,
            stage_type="parallel",
            agent_id="planner",
            parallel_agents=[
                ParallelAgentSpec(agent_id="planner", skill_name=skill) for skill in _LANES
            ],
        )
        orchestrator = BuiltinOrchestrator(session)
        pending = orchestrator._incomplete_parallel_specs(
            ticket, stage_def, "plan", stage_def.parallel_agents
        )

        assert [spec.skill_name for spec in pending] == ["plan-risk", "plan-seams"]


def _seed_v3(engine, stages: list[dict], transitions: list[dict]) -> str:
    template_id = str(uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates (id, slug, name, description, stages_json, "
                "transitions_json, source_path, created_at, version, built_in) VALUES "
                "(:id, 'studio-loregarden-tdd-v3', 'v3', '', :st, :tr, '', :now, 1, 0)"
            ),
            {
                "id": template_id,
                "st": json.dumps(stages),
                "tr": json.dumps(transitions),
                "now": "2026-01-01T00:00:00",
            },
        )
    return template_id


def test_the_migration_fans_plan_out_and_inserts_synthesis(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'hyper.db'}")
    SQLModel.metadata.create_all(engine)
    template_id = _seed_v3(
        engine,
        [
            {
                "key": "plan",
                "name": "Plan",
                "order": 2,
                "stage_type": "agent",
                "agent_id": "planner",
            },
            {"key": "spec", "name": "Spec", "order": 3, "stage_type": "agent", "agent_id": "spec"},
        ],
        [{"from": "plan", "to": "spec"}],
    )
    apply_migrations(engine)

    with engine.connect() as conn:
        stages_json, transitions_json = conn.execute(
            text("SELECT stages_json, transitions_json FROM workflow_templates WHERE id=:id"),
            {"id": template_id},
        ).fetchone()
    stages = {s["key"]: s for s in json.loads(stages_json)}

    assert stages["plan"]["stage_type"] == "parallel"
    assert [a["skill_name"] for a in stages["plan"]["parallel_agents"]] == list(_LANES)
    # The lanes carry the lens, so a stage-level skill would contradict them.
    assert stages["plan"]["skill_name"] == ""
    assert stages["plan-synthesis"]["skill_name"] == SYNTHESIS_SKILL
    # Synthesis sits between plan and whatever plan used to advance to, so the
    # settled plan exists before anything downstream reads one.
    assert stages["plan-synthesis"]["order"] == 3
    assert stages["spec"]["order"] == 4

    transitions = json.loads(transitions_json)
    assert {"from": "plan", "to": "plan-synthesis"} in transitions
    assert {"from": "plan-synthesis", "to": "spec", "when": "pass"} in transitions

    assert apply_migrations(engine) == []
