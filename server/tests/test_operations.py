"""Every transport reaches an operation through `operation_call`, or says why not.

The REST state route was a hand-kept copy of an MCP tool's payload that had
drifted: two fields missing, and a GET that committed after the MCP read had
stopped. An operation is declared once and each transport is either bound to
it or listed as unavailable with a reason, so a copy has nowhere to live.

The two ledgers under `operation_ledger/` hold what has not moved yet. They
only shrink: a tool or route that moves must leave its ledger, and a new one
must be an operation from the start.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from unittest import mock

import pytest
from loregarden.mcp.operation_tools import OPERATION_TOOLS
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Artifact, Ticket
from loregarden.services.operations import (
    OPERATIONS,
    STRICT_READS_ENV,
    Caller,
    Effect,
    Operation,
    OperationId,
    OperationUnavailable,
    ReadOperationWrote,
    Transport,
    run_operation,
)
from loregarden.services.operations.ticket_reads import TicketRef
from tests.factories import make_workspace_ticket

API = Path(__file__).resolve().parents[1] / "loregarden" / "api"
LEDGER = Path(__file__).resolve().parent / "operation_ledger"

_ROUTE_DECORATORS = frozenset({"get", "post", "put", "patch", "delete", "websocket", "api_route"})

#: Routes that are a transport themselves rather than an operation on one.
_TRANSPORT_ROUTES = frozenset({"mcp.py::mcp_info", "mcp.py::mcp_post"})


def _ledger(name: str) -> set[str]:
    return {line for line in (LEDGER / name).read_text().splitlines() if line}


def _is_route(node: ast.AST) -> bool:
    return any(
        isinstance(dec, ast.Call)  # py-org: allow-isinstance — walking a syntax tree
        and isinstance(dec.func, ast.Attribute)  # py-org: allow-isinstance
        and dec.func.attr in _ROUTE_DECORATORS
        for dec in getattr(node, "decorator_list", [])
    )


def _rest_bindings() -> dict[str, set[OperationId]]:
    """Each route function in `api/`, and the operations it runs."""
    routes: dict[str, set[OperationId]] = {}
    for path in sorted(API.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not _is_route(node):
                continue
            ran = {
                OperationId[call.args[1].attr]
                for call in ast.walk(node)
                if isinstance(call, ast.Call)  # py-org: allow-isinstance
                and getattr(call.func, "id", None) == "run_rest_operation"
                and len(call.args) > 1
                and isinstance(call.args[1], ast.Attribute)  # py-org: allow-isinstance
            }
            routes[f"{path.name}::{node.name}"] = ran  # type: ignore[attr-defined]
    return routes


def _bound(operation: Operation) -> dict[Transport, bool]:
    mcp = operation.id in OPERATION_TOOLS.values()
    rest = any(operation.id in ran for ran in _rest_bindings().values())
    # The CLI runs MCP's tools in-process, so it is bound wherever MCP is.
    return {Transport.MCP: mcp, Transport.CLI: mcp, Transport.REST: rest}


@pytest.mark.parametrize("operation", OPERATIONS.values(), ids=lambda op: op.id.value)
def test_each_transport_is_bound_or_says_why_not(operation: Operation):
    bound = _bound(operation)
    for transport in Transport:
        reason = operation.unavailable_on.get(transport)
        if transport is Transport.CLI and bound[Transport.MCP] and reason:
            continue  # An opt-out `operation_call` enforces: MCP offers it, the CLI does not.
        assert bound[transport] != bool(reason), (
            f"{operation.id} on {transport}: bound={bound[transport]}, "
            f"unavailable_on={reason!r}. Bind it, or say why it is not offered."
        )


def test_every_mcp_tool_is_an_operation_or_in_the_ledger():
    tools = {tool.value for tool in McpTool}
    ledger = _ledger("mcp_tools.txt")
    operations = set(OPERATION_TOOLS)

    assert not operations & ledger, (
        f"moved onto operations; remove from the ledger: {operations & ledger}"
    )
    assert not tools - operations - ledger, (
        f"new MCP tools must be operations (mcp/operation_tools.py): {tools - operations - ledger}"
    )
    assert not ledger - tools, f"no such tool; remove from the ledger: {ledger - tools}"


def test_every_rest_route_runs_an_operation_or_is_in_the_ledger():
    routes = _rest_bindings()
    ledger = _ledger("rest_routes.txt")
    through = {name for name, ran in routes.items() if ran}
    bypassing = set(routes) - through - _TRANSPORT_ROUTES

    assert not through & ledger, (
        f"moved onto operations; remove from the ledger: {through & ledger}"
    )
    assert not bypassing - ledger, (
        f"new routes must run an operation (api/operation_routes.py): {bypassing - ledger}"
    )
    assert not ledger - set(routes), (
        f"no such route; remove from the ledger: {ledger - set(routes)}"
    )


def test_a_transport_an_operation_is_not_offered_on_is_refused(db_session):
    ticket = make_workspace_ticket(db_session, "lg-operation-optout")
    with pytest.raises(OperationUnavailable, match="not available over mcp"):
        run_operation(
            db_session,
            OperationId.TICKET_DETAIL,
            {"ticket_id": ticket.id},
            Caller(transport=Transport.MCP),
        )


def test_the_cli_is_held_to_its_own_opt_out(db_session):
    """An operation offered on MCP but not the CLI refuses the CLI's in-process call."""
    ticket = make_workspace_ticket(db_session, "lg-operation-cli-optout")
    brief = OPERATIONS[OperationId.TICKET_BRIEF]
    cli_off = Operation(
        id=brief.id,
        effect=brief.effect,
        args=brief.args,
        handler=brief.handler,
        unavailable_on={Transport.CLI: "not on the CLI", Transport.REST: "no route"},
    )
    arguments = {"ticket_id": ticket.id}
    with mock.patch.dict(OPERATIONS, {OperationId.TICKET_BRIEF: cli_off}):
        over_mcp = json.loads(execute_tool(db_session, McpTool.GET_TICKET.value, arguments))
        with pytest.raises(OperationUnavailable, match="not available over cli"):
            execute_tool(db_session, McpTool.GET_TICKET.value, arguments, transport=Transport.CLI)

    assert over_mcp["ticket_id"] == ticket.id


def _renaming_read(session, ref: TicketRef, _caller: Caller) -> dict:
    ticket = ref.resolve(session)
    ticket.title = "renamed by a read"
    session.add(ticket)
    session.commit()
    return {}


_WRITING_READ = Operation(
    id=OperationId.TICKET_BRIEF, effect=Effect.READ, args=TicketRef, handler=_renaming_read
)


def test_a_read_operation_that_writes_is_stopped_where_reads_are_strict(db_session):
    ticket = make_workspace_ticket(db_session, "lg-operation-strict")
    with (
        mock.patch.dict(OPERATIONS, {OperationId.TICKET_BRIEF: _WRITING_READ}),
        pytest.raises(ReadOperationWrote, match="ticket_brief wrote"),
    ):
        run_operation(
            db_session, OperationId.TICKET_BRIEF, {"ticket_id": ticket.id}, Caller(Transport.MCP)
        )
    db_session.rollback()

    assert db_session.get(Ticket, ticket.id).title != "renamed by a read"


def test_a_read_operation_that_writes_is_reported_where_reads_are_not_strict(
    db_session, monkeypatch, caplog
):
    monkeypatch.delenv(STRICT_READS_ENV)
    ticket = make_workspace_ticket(db_session, "lg-operation-lenient")
    with (
        mock.patch.dict(OPERATIONS, {OperationId.TICKET_BRIEF: _WRITING_READ}),
        caplog.at_level(logging.WARNING, logger="loregarden.services.operations.call"),
    ):
        run_operation(
            db_session, OperationId.TICKET_BRIEF, {"ticket_id": ticket.id}, Caller(Transport.MCP)
        )

    assert any(
        "ticket_brief" in rec.getMessage() and "Ticket" in rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.WARNING
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/tickets/no-such-ticket",
        "/api/orchestration/tickets/no-such-ticket/state",
        "/api/orchestration/tickets/by-external/loregarden/no-such-ticket/state",
    ],
)
def test_a_missing_ticket_is_a_404_on_every_read_route(client, path):
    assert client.get(path).status_code == 404


def test_a_brief_naming_no_ticket_is_refused(db_session):
    with pytest.raises(ValueError, match="ticket_id or external_id is required"):
        execute_tool(db_session, McpTool.GET_TICKET.value, {})


def test_the_artifact_feed_takes_an_external_id_as_the_ticket_page_does(client, db_session):
    ticket = make_workspace_ticket(db_session, "lg-operation-feed")
    db_session.add(Artifact(ticket_id=ticket.id, kind="log", title="run log", content_json="{}"))
    db_session.commit()
    by_id = client.get(f"/api/tickets/{ticket.id}/artifacts")
    by_external = client.get(f"/api/tickets/{ticket.external_id}/artifacts")

    assert by_id.status_code == by_external.status_code == 200
    assert [item["title"] for item in by_external.json()["items"]] == ["run log"]
    assert by_id.json() == by_external.json()


def test_a_ticket_search_naming_no_state_it_knows_is_refused(db_session):
    with pytest.raises(ValueError, match="state"):
        execute_tool(
            db_session,
            McpTool.LIST_TICKETS.value,
            {"workspace_slug": "loregarden", "state": "sideways"},
        )
