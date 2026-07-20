"""The adversarial verify stage: a claim checked by something that didn't make it (#1)."""

import json

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.registry import get_agent
from loregarden.agents.verify_context import build_verify_context
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    Ticket,
    WorkflowStageDef,
    WorkItemType,
    Workspace,
)
from loregarden.services.seed import seed_database
from loregarden.services.studio_routing import (
    VERIFY_STAGE_TYPE,
    is_agentless_stage,
    resolve_stage_execution,
)
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


def _verify_stage(**kw):
    return WorkflowStageDef(
        key="verify", name="Verify", order=9, stage_type=VERIFY_STAGE_TYPE, **kw
    )


def _ticket():
    return Ticket(external_id="1-v", workspace_id="ws", title="T", work_item_type=WorkItemType.TASK)


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_database(session)
    return session


def _seeded_ticket(session):
    return session.exec(
        select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
    ).first()


def _prompt_for(session, stage_type, tmp_path):
    ticket = _seeded_ticket(session)
    # The seeded workspace carries repo_path="." — it resolves to the developer's
    # own checkout, and build_verify_context shells out `git diff main` there. Left
    # alone, this test would pass on a branch ahead of main and fail on a clean one.
    # Point it somewhere with no git checkout, and record the claim explicitly, so
    # the block under test comes from the DB rather than from the working tree.
    workspace = session.get(Workspace, ticket.workspace_id)
    workspace.repo_path = str(tmp_path)
    session.add(workspace)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind="context",
            title="stage report",
            content_json=json.dumps(
                {"stage_key": "implement", "status": "pass", "confidence": 0.9}
            ),
        )
    )
    session.commit()
    run = AgentRun(
        run_code="run_v",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="verifier",
        skill_name="verify",
        stage_key="verify",
    )
    executor = CliAgentExecutor(session)
    stage = WorkflowStageDef(key="verify", name="Verify", order=9, stage_type=stage_type)
    return executor._build_prompt(
        ticket,
        run,
        {"role_body": "role"},
        resolve_agent_context_dir(workspace),
        workspace,
        stage,
    )


def test_verify_stage_runs_the_verifier_agent():
    agent_id, skill = resolve_stage_execution(_ticket(), _verify_stage())
    assert agent_id == "verifier"
    assert skill == "verify"
    # It has an agent, so the orchestrator must not treat it as a human gate.
    assert is_agentless_stage(_verify_stage()) is False


def test_a_template_may_name_its_own_verifier():
    agent_id, _ = resolve_stage_execution(_ticket(), _verify_stage(agent_id="static_qa"))
    assert agent_id == "static_qa"


def test_verifier_agent_is_registered_with_a_role():
    agent = get_agent("verifier")
    assert agent is not None
    # The contract that makes it adversarial rather than a second reviewer.
    assert "Confirm only what you observed" in (agent.get("role_body") or "")


def test_verifier_is_not_given_the_inherited_context(tmp_path):
    """A verifier told what was 'already decided' is only a reader of the previous
    stage's reasoning, and cannot independently disagree with it."""
    session = _session()
    verify_prompt = _prompt_for(session, VERIFY_STAGE_TYPE, tmp_path)
    normal_prompt = _prompt_for(session, "agent", tmp_path)

    assert "already decided — do not re-derive" not in verify_prompt
    # The claim block is what a verifier gets instead, and only it gets one.
    assert "## Claim under review" in verify_prompt
    assert "## Claim under review" not in normal_prompt


def test_claim_under_review_carries_the_prior_stage_report():
    session = _session()
    ticket = _seeded_ticket(session)
    workspace = session.get(Workspace, ticket.workspace_id)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind="context",
            title="stage report",
            content_json=json.dumps(
                {
                    "stage_key": "implement",
                    "status": "pass",
                    "confidence": 0.9,
                    "reroute_context": "all green",
                }
            ),
        )
    )
    session.commit()

    context = build_verify_context(session, ticket, workspace)
    assert "stage: implement" in context
    assert "reported: pass" in context
    # Framing that tells the verifier which standard to apply.
    assert "did not make" in context


def test_verify_context_is_empty_when_there_is_nothing_to_check(tmp_path):
    """With no claim recorded and no diff to read, the block drops out rather
    than emitting a heading with nothing under it."""
    session = _session()
    ticket = _seeded_ticket(session)
    workspace = session.get(Workspace, ticket.workspace_id)
    # Point at a directory that is not a git checkout, so there is no diff to
    # capture — the seeded workspace resolves to a real repo with real changes.
    workspace.repo_path = str(tmp_path)
    session.add(workspace)
    session.commit()

    assert build_verify_context(session, ticket, workspace) == ""
