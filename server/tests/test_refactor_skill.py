"""The refactor skill, and the routing that decides when an agent gets it."""

import json
import logging
from uuid import uuid4

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.config import settings
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import AgentRun, ClassifyRoute, Ticket, WorkflowStageDef, Workspace
from loregarden.services.seed import seed_database
from loregarden.services.studio_routing import resolve_classify_route
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.skills.registry import SKILL_PROMPT_CAP, get_skill, list_skills
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

_SKILL = "refactor"


def _ticket(title: str) -> Ticket:
    return Ticket(title=title, description="", external_id="", acceptance_criteria_json="[]")


def _implement_stage() -> WorkflowStageDef:
    """The live implement stage as migration 0030 leaves it."""
    return WorkflowStageDef(
        key="implement",
        name="Implement",
        order=7,
        stage_type="classify",
        agent_id="backend_implementer",
        classify_routes=[
            ClassifyRoute(
                specialties=["gameplay"], languages=["gdscript"], agent_id="gdscript_reviewer"
            ),
            ClassifyRoute(
                specialties=["frontend"],
                languages=["typescript", "javascript"],
                agent_id="frontend_implementer",
            ),
            ClassifyRoute(
                specialties=["refactor"], agent_id="backend_implementer", skill_name=_SKILL
            ),
            ClassifyRoute(
                specialties=["refactor", "frontend"],
                languages=["typescript", "javascript"],
                agent_id="frontend_implementer",
                skill_name=_SKILL,
            ),
            ClassifyRoute(
                specialties=["backend"],
                languages=["typescript", "javascript"],
                agent_id="backend_implementer",
                default=True,
            ),
        ],
    )


def test_the_skill_is_registered():
    assert _SKILL in list_skills()
    assert (get_skill(_SKILL) or "").strip()


def test_the_skill_reaches_an_agent_whole():
    """The registry truncates at the prompt cap, so an over-long skill loses its
    tail silently — and the tail is where "done means" lives."""
    source = settings.agent_context_dir / "skills" / _SKILL / "SKILL.md"
    body = source.read_text(encoding="utf-8")
    assert len(body) <= SKILL_PROMPT_CAP, (
        f"{source.name} is {len(body)} chars; the last "
        f"{len(body) - SKILL_PROMPT_CAP} would be cut from every prompt"
    )
    assert get_skill(_SKILL) == body


def test_a_frontend_refactor_keeps_its_specialist():
    """The skill is orthogonal to ownership: restructuring UI code is still the
    frontend implementer's job, just with a method attached."""
    agent, skill = resolve_classify_route(
        _ticket("Refactor the modal component to share state"), _implement_stage()
    )
    assert (agent, skill) == ("frontend_implementer", _SKILL)


def test_a_backend_refactor_routes_to_the_backend_implementer():
    agent, skill = resolve_classify_route(
        _ticket("Extract the retry loop out of the orchestration service"), _implement_stage()
    )
    assert (agent, skill) == ("backend_implementer", _SKILL)


def test_gameplay_work_is_not_taken_over_by_the_refactor_lane():
    """A refactor route matches on one word and so ties with the gameplay lane.
    Route order breaks the tie, and it has to break toward the specialist."""
    agent, _ = resolve_classify_route(
        _ticket("Rename the gameplay tick handler"), _implement_stage()
    )
    assert agent == "gdscript_reviewer"


def test_feature_work_does_not_get_the_refactor_skill():
    """Generic structural verbs were left out of the synonyms for this reason —
    plenty of new features move, split, and simplify things."""
    for title in (
        "Add a workflow picker to the Ticket Studio draft editor",
        "Fix the /queue endpoint returning 404",
        "Split the incoming payload across two queues",
        "Move the user to the next onboarding step after signup",
    ):
        _, skill = resolve_classify_route(_ticket(title), _implement_stage())
        assert skill != _SKILL, title


def test_ordinary_ui_work_is_not_swept_up_by_the_frontend_refactor_lane():
    """Specialties are OR-matched, so the refactor+frontend lane fires on a bare
    "tab" or "editor" too. Ahead of the plain frontend lane it tied on that one
    hit and took over ordinary UI work — 12 of 60 real tickets. Behind it, a
    refactor lane can only win by matching strictly more."""
    for title in (
        "Studio Creatures tab with primitive hierarchy tree and param editors",
        "Material slot editor reusing studio color/finish controls",
        "Locomotion and behavior parameter forms (idle, tail, jump, hover)",
    ):
        _, skill = resolve_classify_route(_ticket(title), _implement_stage())
        assert skill == "", title


def _prompt_for_skill(skill_name: str) -> str:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        workspace = session.get(Workspace, ticket.workspace_id)
        run = AgentRun(
            run_code="run_refactor",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            skill_name=skill_name,
            stage_key="implement",
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


def test_the_skill_body_reaches_the_assembled_prompt():
    """Routing to a skill is worth nothing if the prompt drops it."""
    prompt = _prompt_for_skill(_SKILL)
    assert "## Skill" in prompt
    assert "change the shape, keep the behavior" in prompt
    # The closing section is what a cap would eat first, and it carries the
    # part that decides whether the stage may pass.
    assert "## 5. Done means" in prompt


def test_a_skill_with_no_file_is_reported(caplog):
    """A stage naming a missing skill rendered an empty section and no other
    trace, so a template could keep claiming guidance nothing delivered."""
    with caplog.at_level(logging.WARNING):
        prompt = _prompt_for_skill("no-such-skill")
    assert "## Skill" not in prompt
    assert "no-such-skill" in caplog.text


def _seed_template(engine, routes: list[dict]) -> str:
    template_id = str(uuid4())
    stages = [
        {
            "key": "implement",
            "name": "Implement",
            "order": 7,
            "stage_type": "classify",
            "agent_id": "backend_implementer",
            "classify_routes": routes,
        }
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, source_path, "
                "created_at, version, built_in) "
                "VALUES (:id, 'studio-loregarden-tdd-v3', 'v3', '', :st, '[]', '', "
                ":now, 1, 0)"
            ),
            {"id": template_id, "st": json.dumps(stages), "now": "2026-01-01T00:00:00"},
        )
    return template_id


def _routes(engine, template_id: str) -> list[dict]:
    with engine.connect() as conn:
        stages_json = conn.execute(
            text("SELECT stages_json FROM workflow_templates WHERE id=:id"), {"id": template_id}
        ).scalar_one()
    stages = json.loads(stages_json)
    return stages[0]["classify_routes"]


def test_the_migration_inserts_the_lanes_last_before_the_fallback(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tpl.db'}")
    SQLModel.metadata.create_all(engine)
    template_id = _seed_template(
        engine,
        [
            {
                "specialties": ["gameplay"],
                "languages": ["gdscript"],
                "agent_id": "gdscript_reviewer",
            },
            {
                "specialties": ["frontend"],
                "languages": ["typescript"],
                "agent_id": "frontend_implementer",
            },
            {
                "specialties": ["backend"],
                "languages": [],
                "agent_id": "backend_implementer",
                "default": True,
            },
        ],
    )
    apply_migrations(engine)

    routes = _routes(engine, template_id)
    assert [(r["agent_id"], r.get("skill_name", "")) for r in routes] == [
        ("gdscript_reviewer", ""),
        ("frontend_implementer", ""),
        ("backend_implementer", _SKILL),
        ("frontend_implementer", _SKILL),
        ("backend_implementer", ""),  # the default fallback stays last
    ]


def test_the_migration_leaves_an_already_routed_template_alone(tmp_path):
    """Re-running must not stack a second pair of lanes onto a Studio edit."""
    engine = create_engine(f"sqlite:///{tmp_path / 'tpl2.db'}")
    SQLModel.metadata.create_all(engine)
    template_id = _seed_template(
        engine,
        [
            {
                "specialties": ["refactor"],
                "languages": [],
                "agent_id": "backend_implementer",
                "skill_name": _SKILL,
            },
        ],
    )
    apply_migrations(engine)

    assert len(_routes(engine, template_id)) == 1
