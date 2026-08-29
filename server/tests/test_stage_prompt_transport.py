"""A stage prompt describes the channel its run actually has, and nothing else.

The defect these pin: every stage prompt carried the MCP protocol — tool names,
transport, when-to-use tables — whether or not the run receiving it had MCP tools
at all. An externally driven run has none, so it was told to call tools it did
not have and then corrected by whatever harness happened to be driving it.
"""

import os
import re
from pathlib import Path
from unittest import mock

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.mcp_context import (
    CLAUDE_MCP_TOOL_PREFIX,
    CLI_TOOL_COMMAND,
    resolve_control_plane_transport,
)
from loregarden.models.domain import (
    AgentRun,
    ControlPlaneTransport,
    ExternalHarness,
    MemoryBriefingAssembly,
    Ticket,
    Workspace,
)
from loregarden.services.code_map import MAP_FILENAME
from loregarden.services.seed import seed_database
from loregarden.services.workspace_paths import resolve_agent_context_dir, resolve_workspace_root
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

_AGENT = {"role_file": "agents/9_static_qa/static_qa_v1.md", "adapter": "claude"}


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_database(session)
    return session


def _render(session: Session, *, harness: ExternalHarness | None) -> str:
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket
    workspace = session.get(Workspace, ticket.workspace_id)
    run = AgentRun(
        run_code="run_transport",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        skill_name="",
        stage_key="testing",
        external_harness=harness,
    )
    executor = CliAgentExecutor(session)
    return executor._build_prompt(
        ticket,
        run,
        _AGENT,
        resolve_agent_context_dir(workspace),
        workspace,
        executor._resolve_stage_def(ticket, run),
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )


def _run(*, harness: ExternalHarness | None = None) -> AgentRun:
    return AgentRun(
        run_code="run_t",
        ticket_id="t",
        workspace_id="ws",
        agent_id="static_qa",
        stage_key="testing",
        external_harness=harness,
    )


def _sections(prompt: str) -> dict[str, str]:
    parts = re.split(r"^(#{1,3} .+)$", prompt, flags=re.MULTILINE)
    sections: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        sections[parts[index].strip()] = parts[index + 1]
    return sections


def test_a_cli_transport_run_is_not_told_to_call_mcp_tools():
    """The falsehood, in the direction it actually shipped."""
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
        with _session() as session:
            prompt = _render(session, harness=ExternalHarness.CLAUDE_CODE)

    assert f"{CLAUDE_MCP_TOOL_PREFIX}loregarden_" not in prompt
    assert "MCP server is **pre-configured**" not in prompt
    assert CLI_TOOL_COMMAND in prompt
    assert "no MCP tools attached" in prompt


def test_a_supervised_run_is_still_told_to_call_mcp_tools():
    """The other direction: trimming must not strip a run's real protocol."""
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
        with _session() as session:
            prompt = _render(session, harness=None)

    assert f"{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket" in prompt
    assert "MCP server is **pre-configured**" in prompt
    assert "no MCP tools attached" not in prompt


def test_transport_is_read_off_the_wiring_not_off_the_kind_of_run():
    """ "External runs get the short version" would be the same defect reversed.

    A supervised run on a runner this process wires no MCP server into, or with
    injection switched off, reaches the control plane over the CLI too.
    """
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LOREGARDEN_DISABLE_MCP_CLI", None)
        assert (
            resolve_control_plane_transport(run=_run(), adapter="claude")
            is ControlPlaneTransport.MCP
        )
        assert (
            resolve_control_plane_transport(run=_run(), adapter="lmstudio")
            is ControlPlaneTransport.CLI
        )
        assert (
            resolve_control_plane_transport(run=_run(), adapter="not-a-runner")
            is ControlPlaneTransport.CLI
        )

    with mock.patch.dict(os.environ, {"LOREGARDEN_DISABLE_MCP_CLI": "1"}):
        assert (
            resolve_control_plane_transport(run=_run(), adapter="claude")
            is ControlPlaneTransport.CLI
        )


def test_the_ticket_specific_sections_are_identical_across_transports():
    """Transport decides how the agent reports, never what the stage is for.

    Shrinking the plan, description, criteria or run context is how a later
    stage starts re-deriving what an earlier one settled.
    """
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
        with _session() as session:
            mcp_sections = _sections(_render(session, harness=None))
        with _session() as session:
            cli_sections = _sections(_render(session, harness=ExternalHarness.CLAUDE_CODE))

    for title in (
        "## Description",
        "## Acceptance Criteria",
        "## Loregarden run context (authoritative for this run)",
    ):
        assert title in mcp_sections, f"{title} missing from the supervised prompt"
        assert mcp_sections[title] == cli_sections[title]


def test_the_repository_map_is_reachable_where_the_prompt_points():
    """What moved out of the prompt has to still be findable by the agent."""
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
        with _session() as session:
            ticket = session.exec(
                select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
            ).first()
            workspace = session.get(Workspace, ticket.workspace_id)
            repo_root = resolve_workspace_root(workspace)
            prompt = _render(session, harness=None)

    reference = _sections(prompt)["## Repository map"]
    assert MAP_FILENAME in reference
    # The pointer is only worth anything if the file it names is really there,
    # with the two sections it claims.
    named = Path(repo_root) / MAP_FILENAME
    assert named.is_file()
    text = named.read_text(encoding="utf-8")
    assert "## STRUCTURE" in text
    assert "## WHERE TO LOOK" in text
    # …and the map itself is no longer copied in beside the pointer.
    assert "### Repository structure" not in prompt
