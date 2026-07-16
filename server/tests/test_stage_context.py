from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.models.domain import AgentRun, Ticket, WorkflowStageDef, Workspace
from loregarden.services.seed import seed_database
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_build_orchestration_context_maps_testing_to_static_qa():
    ticket = Ticket(
        external_id="03-wire-cli-agent-runner",
        title="Wire CLI agent runner",
        workspace_id="ws",
        workflow_stage_key="testing",
    )
    run = AgentRun(
        run_code="run_test",
        ticket_id="ticket",
        workspace_id="ws",
        agent_id="static_qa",
        skill_name="run_tests",
        stage_key="testing",
    )
    stage = WorkflowStageDef(
        key="testing",
        name="Testing",
        agent_id="static_qa",
        skill_name="run_tests",
        order=7,
    )
    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage)
    assert "authoritative for this run" in text
    assert "`testing`" in text
    assert "STATIC_QA" in text
    assert "run_tests" in text


def test_build_orchestration_context_does_not_imply_ticket_markdown():
    """The context must not send agents hunting for a ticket file.

    It used to say "even if the project_board ticket markdown WORKFLOW STATE section
    shows a different legacy Stage", which presupposed a ticket file that no longer
    exists for any modern ticket. Agents grepped for it, found the legacy
    project_board/ tree, and burned turns reconciling the contradiction.
    """
    ticket = Ticket(
        external_id="82-show-child-tickets",
        title="Show child tickets",
        workspace_id="ws",
        workflow_stage_key="implement",
    )
    run = AgentRun(
        run_code="run_test",
        ticket_id="ticket",
        workspace_id="ws",
        agent_id="backend_implementer",
        stage_key="implement",
    )
    stage = WorkflowStageDef(key="implement", name="Implement", order=7)
    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage)

    assert "ticket markdown" not in text.lower()
    assert "update the ticket file" not in text.lower()
    assert "no markdown file" in text.lower()


def test_cli_prompt_includes_orchestration_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket
        run = AgentRun(
            run_code="run_prompt",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="run_tests",
            stage_key="testing",
        )
        executor = CliAgentExecutor(session)
        agent = {"role_file": "agents/9_static_qa/static_qa_v1.md"}
        workspace = session.get(Workspace, ticket.workspace_id)
        stage_def = executor._resolve_stage_def(ticket, run)
        prompt = executor._build_prompt(
            ticket,
            run,
            agent,
            resolve_agent_context_dir(workspace),
            workspace,
            stage_def,
        )
        assert "Loregarden run context (authoritative for this run)" in prompt
        assert "STATIC_QA" in prompt
