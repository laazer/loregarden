"""Built-in agents keep being offered the tools added after they were seeded.

`seed_builtin_agents` writes `tool_names()` into `mcp_tools_json` once and never
again, so every tool added after an install's first boot was missing from every
built-in row — 21 of 42 on the development database. These pin the reconcile and,
more importantly, its two guards: a custom agent and an operator narrowing must
survive it untouched.
"""

from __future__ import annotations

import json
import logging

import pytest
from loregarden.mcp.tool_ids import TRIAGE_OPS_MCP_TOOLS, McpTool
from loregarden.mcp.tools import tool_names
from loregarden.models.domain import StudioAgent, StudioAgentVersion
from loregarden.services.studio_service import reconcile_builtin_mcp_tools
from sqlmodel import Session, select


@pytest.fixture(name="db_session")
def db_session_fixture(isolated_db):
    """A schema'd but *unseeded* session.

    Deliberately not the shared `db_session`, which is built on `client` and so
    seeds the real built-in agents: the reconcile's whole subject is what a row
    was seeded with, and these tests construct that history themselves.
    """
    with Session(isolated_db) as session:
        yield session


#: A row as the seeder left it before the triage ops, the reference cache and
#: the docker ledger existed — the shape every built-in row on this install has.
STALE_TOOLS = [
    McpTool.GET_TICKET.value,
    McpTool.LIST_TICKETS.value,
    McpTool.START_ORCHESTRATION.value,
    McpTool.COMPLETE_STAGE.value,
    McpTool.UPDATE_TICKET.value,
]


def _agent(
    session: Session,
    *,
    slug: str = "triage",
    tools: list[str],
    seeded_tools: list[str] | None,
    built_in: bool = True,
    version: int = 1,
) -> StudioAgent:
    """A row plus the version-1 seed entry the reconcile compares against.

    ``seeded_tools=None`` writes no seed entry at all — the "unknowable" case.
    """
    agent = StudioAgent(
        id=f"agent-{slug}",
        slug=slug,
        name=slug,
        description="",
        role_body="body",
        adapter="claude",
        default_model="",
        timeout=600,
        default_skill="",
        mcp_enabled=True,
        mcp_tools_json=json.dumps(tools),
        gate_checks_json="[]",
        handoff_checks_json="[]",
        tool_grants_json="{}",
        version=version,
        built_in=built_in,
    )
    session.add(agent)
    if seeded_tools is not None:
        session.add(
            StudioAgentVersion(
                id=f"v1-{slug}",
                agent_id=agent.id,
                version=1,
                # The real shape: `_agent_snapshot` dumps *columns*, so the
                # tool list is a JSON string under `mcp_tools_json`. The first
                # version of this fixture invented `mcp_tools`, which made every
                # guard test pass against a function that skipped every real row.
                snapshot_json=json.dumps(
                    {"slug": slug, "mcp_tools_json": json.dumps(seeded_tools)}
                ),
                created_by="seed",
                change_note="",
            )
        )
    session.commit()
    return agent


def _tools(session: Session, slug: str) -> list[str]:
    agent = session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()
    return json.loads(agent.mcp_tools_json)


class TestStaleRowIsReconciled:
    def test_offers_every_current_tool(self, db_session: Session):
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=STALE_TOOLS)

        assert reconcile_builtin_mcp_tools(db_session) == ["triage"]
        assert set(_tools(db_session, "triage")) == set(tool_names())

    def test_offers_the_triage_ops_the_ticket_rail_is_documented_to_get(self, db_session: Session):
        """The defect this was written for: documented as offered, never offered."""
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=STALE_TOOLS)
        reconcile_builtin_mcp_tools(db_session)

        offered = _tools(db_session, "triage")
        for tool in TRIAGE_OPS_MCP_TOOLS:
            assert tool.value in offered
        assert McpTool.CREATE_TICKET.value in offered
        assert McpTool.SEARCH_PRIOR_WORK.value in offered

    def test_records_a_version_entry_so_the_change_is_explicable(self, db_session: Session):
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=STALE_TOOLS, version=4)
        reconcile_builtin_mcp_tools(db_session)

        agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == "triage")).one()
        assert agent.version == 5
        entry = db_session.exec(
            select(StudioAgentVersion)
            .where(StudioAgentVersion.agent_id == agent.id)
            .where(StudioAgentVersion.version == 5)
        ).one()
        assert entry.created_by == "reconcile"
        assert "seeded" in entry.change_note

    def test_is_idempotent(self, db_session: Session):
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=STALE_TOOLS)
        reconcile_builtin_mcp_tools(db_session)
        version_after_first = (
            db_session.exec(select(StudioAgent).where(StudioAgent.slug == "triage")).one().version
        )

        assert reconcile_builtin_mcp_tools(db_session) == []
        assert (
            db_session.exec(select(StudioAgent).where(StudioAgent.slug == "triage")).one().version
            == version_after_first
        )


class TestGuards:
    def test_an_operator_narrowing_survives(self, db_session: Session, caplog):
        """`mcp_tools_json` is the only place a per-MCP-tool narrowing can live.

        `StudioAgentToolGrants.disallowed_tools` holds `CliTool` members, so an
        operator who unticks MCP tools in Studio has nowhere else to record it —
        re-offering them would silently undo a deliberate choice.
        """
        narrowed = [McpTool.GET_TICKET.value]
        _agent(db_session, tools=narrowed, seeded_tools=STALE_TOOLS)

        with caplog.at_level(logging.INFO, logger="loregarden.services.studio_service"):
            assert reconcile_builtin_mcp_tools(db_session) == []
        assert _tools(db_session, "triage") == narrowed
        assert "operator narrowing" in caplog.text

    def test_a_tool_a_migration_appended_is_not_a_narrowing(self, db_session: Session):
        """The case that made the first version of this a no-op on every real row.

        `m_require_verify_evidence` appended `attach_evidence` to every agent
        after seeding, so no row matches its seed snapshot exactly any more.
        Requiring an exact match skipped the whole fleet while reporting success.
        """
        migrated = [*STALE_TOOLS, McpTool.ATTACH_EVIDENCE.value]
        _agent(db_session, tools=migrated, seeded_tools=STALE_TOOLS)

        assert reconcile_builtin_mcp_tools(db_session) == ["triage"]
        assert set(_tools(db_session, "triage")) == set(tool_names())

    def test_tools_already_present_keep_their_position(self, db_session: Session):
        """Appends. A row's existing list is a prefix of the reconciled one."""
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=STALE_TOOLS)
        reconcile_builtin_mcp_tools(db_session)

        assert _tools(db_session, "triage")[: len(STALE_TOOLS)] == STALE_TOOLS

    def test_a_custom_agent_is_left_alone(self, db_session: Session):
        _agent(
            db_session, slug="my-agent", tools=STALE_TOOLS, seeded_tools=STALE_TOOLS, built_in=False
        )

        assert reconcile_builtin_mcp_tools(db_session) == []
        assert _tools(db_session, "my-agent") == STALE_TOOLS

    def test_a_row_with_no_seed_entry_is_left_alone(self, db_session: Session, caplog):
        """Unknowable is not unchanged — without the snapshot there is no evidence."""
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=None)

        with caplog.at_level(logging.WARNING, logger="loregarden.services.studio_service"):
            assert reconcile_builtin_mcp_tools(db_session) == []
        assert _tools(db_session, "triage") == STALE_TOOLS
        assert "no readable seed snapshot" in caplog.text

    def test_an_unreadable_seed_snapshot_is_left_alone_and_says_so(
        self, db_session: Session, caplog
    ):
        _agent(db_session, tools=STALE_TOOLS, seeded_tools=STALE_TOOLS)
        entry = db_session.exec(
            select(StudioAgentVersion).where(StudioAgentVersion.version == 1)
        ).one()
        entry.snapshot_json = "{not json"
        db_session.add(entry)
        db_session.commit()

        with caplog.at_level(logging.WARNING, logger="loregarden.services.studio_service"):
            assert reconcile_builtin_mcp_tools(db_session) == []
        assert _tools(db_session, "triage") == STALE_TOOLS
        assert "unreadable seed snapshot" in caplog.text
