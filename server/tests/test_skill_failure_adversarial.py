from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.config import settings
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkflowTemplate, Workspace
from loregarden.skills.registry import SkillNotFoundError


def _repo(root: Path) -> Path:
    root.mkdir()
    (root / ".git").mkdir()
    return root


def test_execute_marks_run_failed_when_declared_skill_is_missing(db_session, tmp_path):
    repo = _repo(tmp_path / "repo")
    workspace = Workspace(slug="skill-fail", name="Skill Fail", repo_path=str(repo))
    template = WorkflowTemplate(
        slug="skill-fail-template",
        name="Skill Fail",
        stages_json='[{"key":"verify","name":"Verify","order":1,"stage_type":"agent",'
        '"agent_id":"backend_implementer","skill_name":"missing-skill"}]',
    )
    db_session.add(workspace)
    db_session.add(template)
    db_session.commit()
    workspace.workflow_template_id = template.id
    ticket = Ticket(
        title="Missing skill",
        external_id="missing-skill",
        workspace_id=workspace.id,
        acceptance_criteria_json="[]",
    )
    # Committed before the run that names it: one flush can emit the run first.
    db_session.add(ticket)
    db_session.commit()
    run = AgentRun(
        run_code="run_missing_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="verify",
        skill_name="missing-skill",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()

    with (
        patch("loregarden.agents.executors.cli.ensure_ticket_branch"),
        patch("loregarden.agents.executors.cli.working_tree_paths", return_value=[]),
    ):
        completed = CliAgentExecutor(db_session).execute(run, ticket, advance_workflow=False)

    db_session.refresh(run)
    assert completed.status == RunStatus.FAILED
    assert run.status == RunStatus.FAILED
    assert run.command == ""
    assert "missing-skill" in run.stderr
    assert str((repo / "agent_context" / "skills").resolve()) in run.stderr
    assert str((settings.agent_context_dir / "skills").resolve()) in run.stderr


def test_prepare_terminal_handoff_propagates_skill_not_found(db_session):
    workspace = db_session.query(Workspace).filter_by(slug="loregarden").one()
    ticket = Ticket(
        title="Terminal missing skill",
        external_id="terminal-missing-skill",
        workspace_id=workspace.id,
        acceptance_criteria_json="[]",
    )
    # Committed before the run that names it: one flush can emit the run first.
    db_session.add(ticket)
    db_session.commit()
    run = AgentRun(
        run_code="run_terminal_missing_skill",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="verify",
        skill_name="missing-terminal-skill",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()

    with pytest.raises(SkillNotFoundError) as excinfo:
        CliAgentExecutor(db_session).prepare_terminal_handoff(run, ticket)

    assert isinstance(excinfo.value, ValueError)
    assert "missing-terminal-skill" in str(excinfo.value)
