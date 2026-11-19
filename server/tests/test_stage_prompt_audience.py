"""A stage prompt carries the stage-report rules and tool list its run uses, not every run's.

Every stage prompt used to carry the whole stage-report contract (the `findings`
and `rechecks` a judging stage backs a reject with, and the rules for when that
reject stands) and a guide to every control-plane tool, orchestrator-only ones
included. On the Harbor eval (`evals/harbor`) those two sections were 38% of an
implement prompt, and the bug being fixed was 3%.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.mcp_context import select_audience_blocks
from loregarden.models.domain import (
    AgentRun,
    ContractAudience,
    ExternalHarness,
    MemoryBriefingAssembly,
    Ticket,
    Workspace,
)
from loregarden.services.seed import seed_database
from loregarden.services.studio_agent_prompt import build_studio_prompt_sections
from loregarden.services.studio_mcp_tool_guides import MCP_TOOL_GUIDES
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

ORCHESTRATOR_ONLY = sorted(g.name for g in MCP_TOOL_GUIDES if g.orchestrator_only)
FINDINGS_SCHEMA = '"findings": [{"summary"'


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        yield session


def _render(session: Session, agent_id: str, stage_key: str) -> str:
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket
    workspace = session.get(Workspace, ticket.workspace_id)
    run = AgentRun(
        run_code="run_audience",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        skill_name="",
        stage_key=stage_key,
        external_harness=ExternalHarness.CLAUDE_CODE,
    )
    executor = CliAgentExecutor(session)
    with mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": "claude"}):
        return executor._build_prompt(
            ticket,
            run,
            {"adapter": "claude"},
            resolve_agent_context_dir(workspace),
            workspace,
            executor._resolve_stage_def(ticket, run),
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )


def test_a_judging_stage_gets_the_findings_and_recheck_rules(session):
    prompt = _render(session, "static_qa", "testing")

    assert FINDINGS_SCHEMA in prompt
    assert '"rechecks": [' in prompt
    assert "loregarden:audience" not in prompt


def test_an_implementer_gets_the_verdict_fields_without_the_judging_rules(session):
    prompt = _render(session, "backend_implementer", "implement")

    assert FINDINGS_SCHEMA not in prompt
    assert '"rechecks": [' not in prompt
    assert "loregarden:audience" not in prompt
    # What an implementer reports with is all still there.
    for field in (
        '"status": "pass|fail|needs_rework|blocked"',
        '"reroute_to_agent"',
        '"blocked_kind"',
    ):
        assert field in prompt


def test_a_stage_run_lists_the_tools_it_may_call_by_name_only(session):
    prompt = _render(session, "backend_implementer", "implement")
    tools = prompt.split("## Loregarden control-plane tools", 1)[1].split("\n## ", 1)[0]

    assert "loregarden_attach_artifact" in tools
    assert "mcp describe <tool>" in tools
    assert "**When to use:**" not in tools
    for name in ORCHESTRATOR_ONLY:
        assert name not in tools


def test_chat_and_triage_still_get_the_full_tool_guide():
    sections = build_studio_prompt_sections({"mcp_tools": ["loregarden_get_ticket"]})

    assert "### loregarden_get_ticket" in sections
    assert "**When to use:**" in sections


def test_a_marker_naming_no_audience_is_refused_not_dropped():
    text = "<!-- loregarden:audience=reviewers -->\nrule\n<!-- /loregarden:audience -->\n"

    with pytest.raises(ValueError, match="reviewers"):
        select_audience_blocks(text, ContractAudience.OTHER)


@pytest.mark.parametrize("audience", list(ContractAudience))
def test_unmarked_text_reaches_every_audience(audience):
    text = "common\n<!-- loregarden:audience=judging -->\nj\n<!-- /loregarden:audience -->\n"

    kept = select_audience_blocks(text, audience)

    assert kept.startswith("common\n")
    assert ("j\n" in kept) is (audience is ContractAudience.JUDGING)
