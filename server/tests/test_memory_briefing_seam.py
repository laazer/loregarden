"""One recording point, exercised through the prompt assembly that owns it.

S6 of the ticket spec. These tests drive `CliAgentExecutor._build_prompt` rather
than calling `record_briefing` directly: asserting against the writer in
isolation leaves the seam itself — the thing AC1 and AC5 are actually about —
untested, and a seam that stopped calling the writer is precisely the failure the
aggregate exists to detect.
"""

import time
from unittest.mock import patch

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    MemoryBriefing,
    MemoryBriefingAssembly,
    MemoryBriefingOutcome,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore
from loregarden.services.seed import seed_database
from loregarden.services.studio_routing import VERIFY_STAGE_TYPE
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, select

_INHERITED_HEADING = "## Inherited context (already decided — do not re-derive)"
_CHECKPOINT = "Assumed the runner streams stdout line-by-line."


def _memory(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(obsidian=ObsidianMemoryStore(tmp_path), graph_sqlite_base=None)


def _fixture(session: Session, tmp_path, *, seed_checkpoint: bool = True):
    seed_database(session)
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket
    workspace = session.get(Workspace, ticket.workspace_id)
    memory = _memory(tmp_path)
    if seed_checkpoint:
        memory.append_checkpoint(
            ticket_id=ticket.external_id,
            workspace_slug=workspace.slug,
            run_id="run_earlier",
            entry=_CHECKPOINT,
        )
    run = AgentRun(
        run_code="run_wisdom",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        skill_name="",
        stage_key="testing",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return ticket, workspace, run, memory


def _assemble(
    session,
    ticket,
    workspace,
    run,
    *,
    assembly_source=MemoryBriefingAssembly.DISPATCH,
    stage_def=None,
) -> str:
    executor = CliAgentExecutor(session)
    return executor._build_prompt(
        ticket,
        run,
        {"role_file": "agents/9_static_qa/static_qa_v1.md"},
        resolve_agent_context_dir(workspace),
        workspace,
        stage_def if stage_def is not None else executor._resolve_stage_def(ticket, run),
        assembly_source=assembly_source,
    )


def _rows(engine, run_id: str) -> list[MemoryBriefing]:
    with Session(engine) as reader:
        return list(
            reader.exec(
                select(MemoryBriefing)
                .where(MemoryBriefing.run_id == run_id)
                .order_by(MemoryBriefing.created_at)
            ).all()
        )


def test_a_stage_prompt_build_records_one_briefing_row(isolated_db, tmp_path):
    """AC1 — every assembly that calls the briefing leaves one queryable record,
    and the record describes the briefing that actually went into this prompt."""
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with patch.object(AgentMemoryService, "from_settings", return_value=memory):
            prompt = _assemble(session, ticket, workspace, run)

    assert _CHECKPOINT in prompt
    rows = _rows(isolated_db, run.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == MemoryBriefingOutcome.BUILT
    assert row.chars_injected > 0
    assert row.checkpoints_injected == 1
    assert row.ticket_id == ticket.id
    assert row.workspace_id == workspace.id
    assert row.stage_key == "testing"
    assert row.assembly_source == MemoryBriefingAssembly.DISPATCH


@pytest.mark.parametrize(
    "assembly_source", [MemoryBriefingAssembly.DISPATCH, MemoryBriefingAssembly.RENDER]
)
def test_the_row_records_which_assembly_path_built_it(isolated_db, tmp_path, assembly_source):
    """Two assemblies for one run is a live path — supervised dispatch and
    `render_stage_prompt` both reach `_build_prompt`. A row that cannot say which
    one it came from makes the pair indistinguishable from a double write."""
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with patch.object(AgentMemoryService, "from_settings", return_value=memory):
            _assemble(session, ticket, workspace, run, assembly_source=assembly_source)

    assert _rows(isolated_db, run.id)[0].assembly_source == assembly_source


def test_each_assembly_writes_its_own_row(isolated_db, tmp_path):
    """No unique constraint on run_id: one run really can assemble twice, and
    collapsing the two would hide a re-dispatch."""
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with patch.object(AgentMemoryService, "from_settings", return_value=memory):
            _assemble(session, ticket, workspace, run)
            _assemble(
                session, ticket, workspace, run, assembly_source=MemoryBriefingAssembly.RENDER
            )

    assert [row.assembly_source for row in _rows(isolated_db, run.id)] == [
        MemoryBriefingAssembly.DISPATCH,
        MemoryBriefingAssembly.RENDER,
    ]


def test_a_verify_assembly_records_a_skipped_row_and_carries_no_inherited_context(
    isolated_db, tmp_path
):
    """AC5 / S8 case 5 — a verifier is deliberately starved of inherited context.
    The row exists anyway: it records a prompt assembly that happened and
    deliberately carried no briefing, so a verify stage does not read as a run
    whose telemetry write silently failed."""
    verify_stage = WorkflowStageDef(
        key="testing", name="Verify", stage_type=VERIFY_STAGE_TYPE, order=7
    )
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with patch.object(AgentMemoryService, "from_settings", return_value=memory):
            prompt = _assemble(session, ticket, workspace, run, stage_def=verify_stage)

    assert _INHERITED_HEADING not in prompt
    assert _CHECKPOINT not in prompt
    rows = _rows(isolated_db, run.id)
    assert len(rows) == 1
    assert rows[0].outcome == MemoryBriefingOutcome.SKIPPED
    assert rows[0].store_states_json == "{}"
    assert rows[0].chars_injected == 0
    assert rows[0].elapsed_ms == 0


def test_a_non_verify_assembly_of_the_same_ticket_is_not_skipped(isolated_db, tmp_path):
    """The positive control for the test above. Without it an implementation
    that records SKIPPED unconditionally passes."""
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with patch.object(AgentMemoryService, "from_settings", return_value=memory):
            prompt = _assemble(session, ticket, workspace, run)

    assert _INHERITED_HEADING in prompt
    assert _rows(isolated_db, run.id)[0].outcome != MemoryBriefingOutcome.SKIPPED


def test_the_recorded_row_id_is_resolvable_for_ticket_178(isolated_db, tmp_path):
    """AC5 — the seam's documented attach point is a row id, so the row the seam
    wrote has to be findable by it."""
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with patch.object(AgentMemoryService, "from_settings", return_value=memory):
            _assemble(session, ticket, workspace, run)

    row_id = _rows(isolated_db, run.id)[0].id
    with Session(isolated_db) as reader:
        assert reader.get(MemoryBriefing, row_id) is not None


def test_a_failing_telemetry_write_costs_the_row_and_nothing_else(isolated_db, tmp_path):
    """AC4 — a telemetry write failure must not fail the prompt build or the run.

    The prompt must still carry the briefing it assembled: a guard that
    degrades the prompt to protect the telemetry has the dependency backwards.
    """
    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with (
            patch.object(AgentMemoryService, "from_settings", return_value=memory),
            patch(
                "loregarden.services.memory_briefing_telemetry.Session",
                side_effect=RuntimeError("telemetry backend down"),
            ),
        ):
            prompt = _assemble(session, ticket, workspace, run)

        run.stage_key = "still-usable"
        session.add(run)
        session.commit()

    assert _INHERITED_HEADING in prompt
    assert _CHECKPOINT in prompt
    assert _rows(isolated_db, run.id) == []
    with Session(isolated_db) as reader:
        assert reader.get(AgentRun, run.id).stage_key == "still-usable"


def test_elapsed_ms_measures_the_briefing_and_not_the_telemetry_write(isolated_db, tmp_path):
    """AC1's elapsed figure is the retrieval cost an operator would act on. A
    measurement that swallowed the write would attribute a slow database to a
    slow vault."""
    real_session = Session

    def slow(*args, **kwargs):
        time.sleep(0.4)
        return real_session(*args, **kwargs)

    with Session(isolated_db) as session:
        ticket, workspace, run, memory = _fixture(session, tmp_path)
        with (
            patch.object(AgentMemoryService, "from_settings", return_value=memory),
            patch("loregarden.services.memory_briefing_telemetry.Session", slow),
        ):
            _assemble(session, ticket, workspace, run)

    assert _rows(isolated_db, run.id)[0].elapsed_ms < 250
