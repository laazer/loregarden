"""Declared claims (`loregarden_claim_paths`), required plan files, and the skill text."""

from __future__ import annotations

import json

import pytest
from loregarden.db.versions.plan_skills_name_their_files import (
    FILES_SECTION,
    PLAN_SKILLS,
    m_plan_skills_name_their_files,
)
from loregarden.mcp.tool_ids import CONTROL_PLANE_WRITE_MCP_TOOLS, STAGE_DEFAULT_MCP_TOOLS, McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    DeclaredPathClaim,
    Skill,
    SkillVersion,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.path_claim_enums import PathClaimSource
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.path_claims import (
    MAX_DECLARED_PATHS,
    PlanFilesMissingError,
    declare_paths,
    ticket_claims,
    validate_plan_files,
)
from loregarden.testing.factories import make_artifact, make_ticket
from sqlmodel import Session, select
from tests.factories import make_agent_run
from tests.repo_paths import REPO_ROOT

LAND = "server/loregarden/services/land_ticket.py"
SKILLS_DIR = REPO_ROOT / "agent_context" / "skills"


@pytest.fixture(name="pair")
def pair_fixture(db_session: Session):
    """The seeded ticket and an in-progress sibling under one milestone, each with a run."""
    ticket = db_session.exec(select(Ticket)).first()
    milestone = make_ticket(
        db_session,
        workspace_id=ticket.workspace_id,
        external_id="ms-claims",
        work_item_type=WorkItemType.MILESTONE,
    )
    ticket.parent_ticket_id = milestone.id
    db_session.add(ticket)
    db_session.commit()
    sibling = make_ticket(
        db_session,
        workspace_id=ticket.workspace_id,
        external_id="sib-claims",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    return ticket, sibling, _agent_run(db_session, ticket), _agent_run(db_session, sibling)


def _agent_run(session: Session, ticket: Ticket) -> AgentRun:
    return make_agent_run(
        session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code=f"run_{ticket.external_id}",
        stage_key="implement",
        skill_name="",
        status="running",
        command="",
        stdout="",
        stderr="",
    )


def _claim(session: Session, run: AgentRun, paths: list[str], *, transport: str = "") -> dict:
    return json.loads(
        execute_tool(
            session,
            McpTool.CLAIM_PATHS.value,
            {"run_id": run.id, "paths": paths, "reason": "changing the run_git signature"},
            orchestrated=True,
            run_id=transport or run.id,
        )
    )


# -- the tool ---------------------------------------------------------------


def test_a_claim_is_stored_against_the_run_and_its_ticket(db_session, pair):
    ticket, _, run, _ = pair

    reply = _claim(db_session, run, ["./server/git.py", "client/src/lib/"])

    assert reply["claimed"] == ["server/git.py", "client/src/lib/"]
    rows = db_session.exec(select(DeclaredPathClaim)).all()
    assert {(r.ticket_id, r.run_id, r.path) for r in rows} == {
        (ticket.id, run.id, "server/git.py"),
        (ticket.id, run.id, "client/src/lib/"),
    }


def test_the_reply_names_a_sibling_already_claiming_those_files(db_session, pair):
    _, sibling, run, sibling_run = pair
    _claim(db_session, sibling_run, [LAND])

    reply = _claim(db_session, run, [LAND, "server/unrelated.py"])

    assert reply["siblings"] == [{"ticket": sibling.external_id, "paths": [LAND]}]


def test_a_claim_is_refused_for_another_tickets_run(db_session, pair):
    """The supervised run is working its own ticket; it may not claim for a sibling."""
    _, _, run, sibling_run = pair

    with pytest.raises(ValueError, match="your own ticket"):
        _claim(db_session, sibling_run, [LAND], transport=run.id)

    assert db_session.exec(select(DeclaredPathClaim)).all() == []


def test_the_tool_is_offered_to_stages_and_needs_no_click():
    assert McpTool.CLAIM_PATHS in STAGE_DEFAULT_MCP_TOOLS
    assert McpTool.CLAIM_PATHS in CONTROL_PLANE_WRITE_MCP_TOOLS


# -- declared claims --------------------------------------------------------


def test_a_declared_path_is_claimed_and_outranked_only_by_an_observed_one(db_session, pair):
    ticket, _, run, _ = pair
    make_artifact(
        db_session,
        ticket_id=ticket.id,
        kind="plan",
        title="Plan",
        content={"modules_touched": [LAND, "a.py", "b.py"]},
    )
    declare_paths(db_session, ticket, [LAND, "a.py"], reason="r", run_id=run.id)
    run.changed_paths_json = json.dumps([LAND])
    run.changed_paths_recorded_at = utcnow()
    db_session.add(run)
    db_session.commit()

    assert ticket_claims(db_session, ticket) == {
        LAND: PathClaimSource.OBSERVED,
        "a.py": PathClaimSource.DECLARED,
        "b.py": PathClaimSource.PLANNED,
    }


def test_declaring_a_path_twice_stores_it_once(db_session, pair):
    ticket, _, run, _ = pair
    declare_paths(db_session, ticket, ["a.py"], reason="r", run_id=run.id)

    assert declare_paths(db_session, ticket, ["a.py", "b.py"], reason="r", run_id=run.id) == [
        "b.py"
    ]
    assert len(db_session.exec(select(DeclaredPathClaim)).all()) == 2


@pytest.mark.parametrize("bad", ["/etc/passwd", "~/x.py", "server/../../x.py", "  "])
def test_a_path_outside_the_repo_is_refused(db_session, pair, bad):
    ticket, _, run, _ = pair

    with pytest.raises(ValueError, match="repo-relative"):
        declare_paths(db_session, ticket, ["ok.py", bad], reason="r", run_id=run.id)

    assert db_session.exec(select(DeclaredPathClaim)).all() == []


def test_too_many_paths_are_refused_rather_than_trimmed(db_session, pair):
    ticket, _, run, _ = pair
    paths = [f"f{i}.py" for i in range(MAX_DECLARED_PATHS + 1)]

    with pytest.raises(ValueError, match="at most"):
        declare_paths(db_session, ticket, paths, reason="r", run_id=run.id)

    assert db_session.exec(select(DeclaredPathClaim)).all() == []


# -- a plan must name its files ---------------------------------------------


def test_annotated_plan_files_are_accepted():
    files = validate_plan_files({"modules_touched": [f"{LAND} — the landing path", "x.tsx (new)"]})

    assert files == [LAND, "x.tsx"]


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"modules_touched": []},
        {"modules_touched": {"new": 3, "edited": 10}},
        {"modules_touched": ["the landing service", "tests"]},
        {"modules_touched": "server/x.py"},
    ],
    ids=["missing", "empty", "count-summary", "prose-only", "a-string"],
)
def test_a_plan_naming_no_file_is_refused(content):
    with pytest.raises(PlanFilesMissingError, match="modules_touched"):
        validate_plan_files(content)


def test_attaching_a_plan_without_files_is_refused(db_session, pair):
    ticket, *_ = pair
    callbacks = OrchestrationCallbackService(db_session)

    with pytest.raises(PlanFilesMissingError):
        callbacks.attach_artifact(ticket, kind="plan", title="Plan", content={"approach": "x"})

    attached = callbacks.attach_artifact(
        ticket, kind="plan", title="Plan", content={"approach": "x", "modules_touched": [LAND]}
    )
    assert attached.id


def test_other_artifact_kinds_need_no_files(db_session, pair):
    ticket, *_ = pair

    assert (
        OrchestrationCallbackService(db_session)
        .attach_artifact(ticket, kind="log", title="Log", content={"x": 1})
        .id
    )


# -- the plan skills say so -------------------------------------------------


@pytest.mark.parametrize("slug", PLAN_SKILLS)
def test_each_plan_skill_file_carries_the_section_the_migration_appends(slug):
    assert FILES_SECTION in (SKILLS_DIR / slug / "SKILL.md").read_text(encoding="utf-8")


def _seeded_skill(session: Session, slug: str, body: str, *, built_in: bool = True) -> Skill:
    """A seeded plan skill whose body is set to ``body``, as an older database holds it."""
    skill = session.exec(select(Skill).where(Skill.slug == slug)).one()
    skill.body = body
    skill.version = 3
    skill.built_in = built_in
    session.add(skill)
    session.commit()
    return skill


def _migrate(session: Session) -> None:
    m_plan_skills_name_their_files(session.connection())
    session.commit()


def test_the_migration_appends_the_section_once_as_a_new_version(db_session):
    skill = _seeded_skill(db_session, "plan-risk", "# Plan\n\nBody.")
    before = {v.id for v in db_session.exec(select(SkillVersion)).all()}

    _migrate(db_session)
    _migrate(db_session)

    db_session.refresh(skill)
    assert skill.body == f"# Plan\n\nBody.\n\n{FILES_SECTION}"
    assert skill.version == 4
    [version] = [
        v
        for v in db_session.exec(select(SkillVersion).where(SkillVersion.skill_id == skill.id))
        if v.id not in before
    ]
    assert version.version == 4
    assert json.loads(version.snapshot_json)["body"] == skill.body


def test_the_migration_leaves_a_skill_that_already_names_the_field(db_session):
    body = "# Plan\n\nList modules_touched as paths."
    skill = _seeded_skill(db_session, "plan", body)

    _migrate(db_session)

    db_session.refresh(skill)
    assert skill.body == body
    assert skill.version == 3


def test_the_migration_leaves_an_operator_owned_skill(db_session):
    """A plan skill the operator owns is theirs; the attach refusal still teaches the rule."""
    skill = _seeded_skill(db_session, "plan-seams", "# My plan", built_in=False)

    _migrate(db_session)

    db_session.refresh(skill)
    assert skill.body == "# My plan"
    assert skill.version == 3
