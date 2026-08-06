import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import AgentRun, Ticket, WorkflowStageDef, Workspace
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.skills.registry import SkillNotFoundError


def _ticket(workspace: Workspace) -> Ticket:
    return Ticket(
        title="Missing skill",
        description="",
        external_id="missing-skill",
        workspace_id=workspace.id,
        acceptance_criteria_json="[]",
    )


def _stage(skill_name: str) -> WorkflowStageDef:
    return WorkflowStageDef(
        key="verify",
        name="Verify",
        order=1,
        stage_type="agent",
        agent_id="backend_implementer",
        skill_name=skill_name,
    )


def test_build_prompt_rejects_declared_skill_that_resolves_nowhere(db_session):
    workspace = db_session.query(Workspace).filter_by(slug="loregarden").one()
    ticket = _ticket(workspace)
    run = AgentRun(
        run_code="run_missing_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="verify",
        skill_name="no-such-skill",
    )

    with pytest.raises(SkillNotFoundError) as excinfo:
        CliAgentExecutor(db_session)._build_prompt(
            ticket,
            run,
            {"role_body": "role"},
            resolve_agent_context_dir(workspace),
            workspace,
            _stage("no-such-skill"),
        )

    message = str(excinfo.value)
    assert isinstance(excinfo.value, ValueError)
    assert "no-such-skill" in message
    for searched_dir in excinfo.value.searched_dirs:
        assert str(searched_dir) in message


def test_build_prompt_never_emits_empty_skill_block_for_declared_skill(db_session):
    workspace = db_session.query(Workspace).filter_by(slug="loregarden").one()
    ticket = _ticket(workspace)
    run = AgentRun(
        run_code="run_plan_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="plan",
        skill_name="plan",
    )

    prompt = CliAgentExecutor(db_session)._build_prompt(
        ticket,
        run,
        {"role_body": "role"},
        resolve_agent_context_dir(workspace),
        workspace,
        _stage("plan"),
    )

    assert "## Skill\n" in prompt
    assert "name: plan" in prompt
    assert "## Skill\n\n\n##" not in prompt


def test_stage_with_no_skill_emits_no_skill_section(db_session):
    workspace = db_session.query(Workspace).filter_by(slug="loregarden").one()
    ticket = _ticket(workspace)
    run = AgentRun(
        run_code="run_no_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="triage",
        skill_name="",
    )

    prompt = CliAgentExecutor(db_session)._build_prompt(
        ticket,
        run,
        {"role_body": "role"},
        resolve_agent_context_dir(workspace),
        workspace,
        _stage(""),
    )

    assert "## Skill" not in prompt


def test_missing_agent_default_skill_uses_agent_message_prefix(db_session):
    workspace = db_session.query(Workspace).filter_by(slug="loregarden").one()
    ticket = _ticket(workspace)
    run = AgentRun(
        run_code="run_missing_default_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        skill_name="",
    )

    with pytest.raises(SkillNotFoundError) as excinfo:
        CliAgentExecutor(db_session)._build_prompt(
            ticket,
            run,
            {"role_body": "role", "default_skill": "missing-default"},
            resolve_agent_context_dir(workspace),
            workspace,
            _stage(""),
        )

    message = str(excinfo.value)
    assert "Agent 'backend_implementer' declares default skill 'missing-default'" in message
    for searched_dir in excinfo.value.searched_dirs:
        assert str(searched_dir) in message
