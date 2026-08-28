from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.stage_context import build_orchestration_context, gate_prep_target
from loregarden.models.domain import (
    AgentRun,
    MemoryBriefingAssembly,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
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
        skill_name="",
        stage_key="testing",
    )
    stage = WorkflowStageDef(
        key="testing",
        name="Testing",
        agent_id="static_qa",
        skill_name="",
        order=7,
    )
    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage)
    assert "authoritative for this run" in text
    assert "`testing`" in text
    assert "STATIC_QA" in text


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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        assert ticket
        run = AgentRun(
            run_code="run_prompt",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="",
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
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )
        assert "Loregarden run context (authoritative for this run)" in prompt
        assert "STATIC_QA" in prompt


def test_prompt_block_order_is_declarative(tmp_path):
    """The prompt is assembled from ordered blocks (S2), each dropping out when
    empty, so a new section is one entry rather than another conditional threaded
    through the assembly."""
    from loregarden.agents.executors.cli import _raw_block, _titled_block

    assert _titled_block("## T", "") == []
    assert _titled_block("## T", "body") == ["", "## T", "body"]
    # The cap applies to the body, never to the title.
    assert _titled_block("## T", "abcdef", cap=3) == ["", "## T", "abc"]
    assert _raw_block("") == []
    assert _raw_block("body") == ["", "body"]


def test_inherited_context_section_reaches_the_stage_prompt(tmp_path, monkeypatch):
    """#5: an earlier stage's checkpoint shows up without the agent searching."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore

    memory = AgentMemoryService(obsidian=ObsidianMemoryStore(tmp_path), graph_sqlite_base=None)

    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        workspace = session.get(Workspace, ticket.workspace_id)
        memory.append_checkpoint(
            ticket_id=ticket.external_id,
            workspace_slug=workspace.slug,
            run_id="run_earlier",
            entry="Assumed the runner streams stdout line-by-line.",
        )
        monkeypatch.setattr(
            "loregarden.agents.inherited_wisdom.AgentMemoryService.from_settings",
            classmethod(lambda cls: memory),
        )

        run = AgentRun(
            run_code="run_wisdom",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="static_qa",
            skill_name="",
            stage_key="testing",
        )
        executor = CliAgentExecutor(session)
        prompt = executor._build_prompt(
            ticket,
            run,
            {"role_file": "agents/9_static_qa/static_qa_v1.md"},
            resolve_agent_context_dir(workspace),
            workspace,
            executor._resolve_stage_def(ticket, run),
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )

    assert "## Inherited context (already decided — do not re-derive)" in prompt
    assert "streams stdout line-by-line" in prompt
    # Sits with the ticket's own context, ahead of the reference modules.
    assert prompt.index("Inherited context") < prompt.index("## Loregarden control-plane module")
    assert prompt.index("## Acceptance Criteria") < prompt.index("Inherited context")


def test_gate_prep_targets_the_last_authoring_stage_before_a_human_gate():
    """Only the implementer is briefed — not planning, spec, or the reviews in between."""
    stages = [
        WorkflowStageDef(key="planning", name="Planning", agent_id="planner", order=1),
        WorkflowStageDef(
            key="implementation",
            name="Implementation",
            agent_id="core_simulation",
            stage_type="classify",
            order=6,
        ),
        WorkflowStageDef(key="script_review", name="Script Review", stage_type="parallel", order=7),
        WorkflowStageDef(key="ac_gate", name="AC Gate", agent_id="ac", stage_type="gate", order=8),
        WorkflowStageDef(
            key="playtest", name="Playtest", order=9, checklist=["{{playtest_scenes}}"]
        ),
        WorkflowStageDef(key="done", name="Done", order=10, terminal=True),
    ]

    assert gate_prep_target(stages, "implementation").key == "playtest"
    assert gate_prep_target(stages, "planning") is None
    assert gate_prep_target(stages, "script_review") is None
    assert gate_prep_target(stages, "playtest") is None
    assert gate_prep_target(stages, "done") is None


def test_gate_prep_brief_tells_the_implementer_to_build_what_the_gate_runs():
    stages = [
        WorkflowStageDef(
            key="implementation", name="Implementation", agent_id="core_simulation", order=1
        ),
        WorkflowStageDef(
            key="playtest", name="Playtest", order=2, checklist=["{{playtest_scenes}}"]
        ),
    ]
    ticket = Ticket(
        external_id="gate-prep",
        workspace_id="ws",
        title="Dash",
        workflow_stage_key="implementation",
    )
    run = AgentRun(
        ticket_id="t", workspace_id="ws", agent_id="core_simulation", stage_key="implementation"
    )

    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stages[0], stages=stages)

    assert "last stage before the `playtest` human gate" in text
    assert "sign-off, not a build step" in text


def test_gate_prep_brief_absent_without_a_downstream_human_gate():
    stages = [
        WorkflowStageDef(key="implementation", name="Implementation", agent_id="dev", order=1),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True),
    ]
    ticket = Ticket(
        external_id="no-gate", workspace_id="ws", title="t", workflow_stage_key="implementation"
    )
    run = AgentRun(ticket_id="t", workspace_id="ws", agent_id="dev", stage_key="implementation")

    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stages[0], stages=stages)

    assert "human gate" not in text


def test_stage_brief_reaches_the_stage_it_was_written_for():
    stage = WorkflowStageDef(
        key="script_review",
        name="Script Review",
        agent_id="static_qa",
        stage_brief="Hunt regressions in the adjacent systems this change touches.",
    )
    ticket = Ticket(
        external_id="brief", workspace_id="ws", title="t", workflow_stage_key="script_review"
    )
    run = AgentRun(
        ticket_id="t", workspace_id="ws", agent_id="static_qa", stage_key="script_review"
    )

    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage, stages=[stage])

    assert "What this workflow wants from this stage" in text
    assert "Hunt regressions in the adjacent systems this change touches." in text


def test_no_brief_section_when_the_template_wrote_none():
    stage = WorkflowStageDef(key="implementation", name="Implementation", agent_id="dev")
    ticket = Ticket(
        external_id="no-brief", workspace_id="ws", title="t", workflow_stage_key="implementation"
    )
    run = AgentRun(ticket_id="t", workspace_id="ws", agent_id="dev", stage_key="implementation")

    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage, stages=[stage])

    assert "What this workflow wants from this stage" not in text
