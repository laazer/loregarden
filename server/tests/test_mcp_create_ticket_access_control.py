"""Who may file a ticket through `loregarden_create_ticket`, and on what terms.

Interactive and operator callers file freely. A pipeline agent was denied the
tool outright (a9-create-ticket-mcp-tool) while the review contract told it to
file findings with it; denied, a review lane of lg-milestone-that-1314 curled
the REST API instead and filed lg-milestone-that-1315 with no record of which
run did it. So a pipeline agent may file again, on two conditions the curl
lacked: the filing is attributed to its run, and a run files at most
`MAX_TICKETS_PER_RUN`.
"""

from __future__ import annotations

import json

import pytest
from loregarden.agents.executors.permission_bridge import AUTO_APPROVED_MCP_TOOLS
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TicketRelation
from loregarden.services.agent_ticket_filing import MAX_TICKETS_PER_RUN, filed_by
from loregarden.services.seed import seed_database
from loregarden.services.ticket_dependencies import TicketDependencyService
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket


@pytest.fixture(name="run")
def run_fixture(db_session: Session) -> AgentRun:
    """A review lane's run on a ticket, the way 1314's was."""
    seed_database(db_session)
    ticket = make_workspace_ticket(db_session, "filing-reviewed")
    run = AgentRun(
        run_code="run_filing",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="architecture_reviewer",
        stage_key="review",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _file(title: str, *, depends_on: list[str] | None = None) -> dict:
    return normalize_tool_arguments(
        "loregarden_create_ticket",
        {
            "workspace_slug": "loregarden",
            "title": title,
            "work_item_type": "milestone",
            "depends_on": depends_on or [],
        },
    )


def test_create_ticket_is_never_auto_approved():
    """Allowed is not silent: on a run that asks before writes, a person still sees it."""
    assert "loregarden_create_ticket" not in AUTO_APPROVED_MCP_TOOLS


def test_a_pipeline_agent_files_a_ticket_attributed_to_its_run(db_session, run):
    created = json.loads(
        execute_tool(
            db_session,
            "loregarden_create_ticket",
            _file("A finding no criterion covers"),
            orchestrated=True,
            run_id=run.id,
        )
    )

    relation = db_session.exec(
        select(TicketRelation).where(TicketRelation.created_by == filed_by(run))
    ).one()
    assert {relation.ticket_id, relation.related_ticket_id} == {run.ticket_id, created["id"]}
    assert db_session.get(Ticket, created["id"]).last_updated_by == "agent"


def test_a_run_files_at_most_its_budget(db_session, run):
    for index in range(MAX_TICKETS_PER_RUN):
        execute_tool(
            db_session,
            "loregarden_create_ticket",
            _file(f"Finding {index}"),
            orchestrated=True,
            run_id=run.id,
        )

    with pytest.raises(ValueError, match="already filed"):
        execute_tool(
            db_session,
            "loregarden_create_ticket",
            _file("One too many"),
            orchestrated=True,
            run_id=run.id,
        )
    assert not db_session.exec(select(Ticket).where(Ticket.title == "One too many")).first()


def test_a_pipeline_call_that_names_no_run_is_refused(db_session, run):
    with pytest.raises(ValueError, match="supervised run"):
        execute_tool(
            db_session,
            "loregarden_create_ticket",
            _file("Unattributed"),
            orchestrated=True,
        )


def test_a_filed_ticket_carries_its_prerequisites(db_session, run):
    """1315's wait was in its prose only, so nothing held it."""
    prerequisite = make_workspace_ticket(db_session, "filing-prereq")

    created = json.loads(
        execute_tool(
            db_session,
            "loregarden_create_ticket",
            _file("Waits on the prerequisite", depends_on=[prerequisite.external_id]),
            orchestrated=True,
            run_id=run.id,
        )
    )

    assert TicketDependencyService(db_session).prerequisites(created["id"]) == [prerequisite.id]


def test_an_unknown_prerequisite_refuses_before_anything_is_created(db_session, run):
    with pytest.raises(ValueError, match="not found"):
        execute_tool(
            db_session,
            "loregarden_create_ticket",
            _file(
                "Waits on nothing real",
                depends_on=["00000000-0000-4000-8000-000000000001"],
            ),
        )
    assert not db_session.exec(
        select(Ticket).where(Ticket.title == "Waits on nothing real")
    ).first()


def test_an_interactive_caller_files_without_a_run(db_session, run):
    execute_tool(db_session, "loregarden_create_ticket", _file("Interactive call"))
    assert db_session.exec(select(Ticket).where(Ticket.title == "Interactive call")).one()


def test_the_orchestrated_flag_still_denies_the_tools_on_the_list(db_session, run):
    with pytest.raises(ValueError, match="(?i)orchestrated"):
        execute_tool(
            db_session,
            "loregarden_start_orchestration",
            {"ticket_id": run.ticket_id},
            orchestrated=True,
        )


def test_the_denial_does_not_point_at_the_rest_api(db_session, run):
    """It used to say "Use the REST API", and an agent did."""
    with pytest.raises(ValueError) as refused:
        execute_tool(
            db_session,
            "loregarden_start_orchestration",
            {"ticket_id": run.ticket_id},
            orchestrated=True,
        )
    assert "Use the REST API" not in str(refused.value)
