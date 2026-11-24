"""What the shield must not do, and the sites it must not miss (AC3-AC6).

`test_shaped_errors.py` pins the five recoverable conditions and the two
negatives. Three things it cannot see are the ones that turn this criterion
from a module into a change:

1. **The raise sites.** A perfect classifier reached by nothing leaves every
   `raise ValueError("Workspace not found: …")` exactly where it is, and every
   test in that module stays green. The sweep below is the only assertion that
   the migration happened — and it finds two sites the criterion's own list of
   twelve omits.
2. **`api/` keeps its status.** AC3 tells the implementer to check what `api/`
   turns a ValueError into *first*. Subclassing `ValueError` is the mechanism;
   a 400 coming back from a real request is the property.
3. **The shield's own blast radius.** `except Exception` is already wide. The
   tests here pin what must still escape it, because an interpreter-level
   signal absorbed into a reassuring reply is the worst version of this
   ticket's failure class.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.mcp import protocol
from loregarden.mcp.shaped_errors import (
    RecoverableToolCondition,
    StaleIndex,
    UnknownWorkspaceSlug,
    shape,
)
from loregarden.models.domain.mcp_tables import McpToolCall
from loregarden.services.tool_telemetry import DECISION_SHAPED, DECISIONS
from sqlmodel import Session, select

_ROOT = Path(__file__).resolve().parents[1] / "loregarden"
_RAISES_UNTYPED_SLUG = re.compile(r"raise\s+ValueError\(\s*f?[\"'][^\"']*Workspace not found")


def _call(session: Session, raises: BaseException) -> dict:
    with patch("loregarden.mcp.protocol.execute_tool", side_effect=raises):
        response = protocol.handle_request(
            session,
            {
                "jsonrpc": "2.0",
                "id": "req-7",
                "method": "tools/call",
                "params": {"name": "loregarden_search_memory", "arguments": {"query": "x"}},
            },
        )
    session.commit()
    return response


# --- AC3 the raise sites actually migrate -----------------------------------


def test_no_service_still_raises_an_untyped_workspace_not_found():
    """The criterion names twelve sites. The tree holds sixteen.

    `github_pr_service.py:62`, `git_commit_push_service.py:237` and
    `oversized_ticket_split.py:283,303` raise the same untyped
    `ValueError("Workspace not found…")` and appear on no list. A
    migration driven by the enumeration rather than by the tree leaves them
    behind, and an agent that reaches one gets `isError` for a condition the
    other twelve answer with a remedy — which is worse than the uniform
    failure it replaced, because now it is arbitrary.
    """
    stragglers = [
        f"{path.relative_to(_ROOT)}:{number}"
        for path in sorted((_ROOT / "services").rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _RAISES_UNTYPED_SLUG.search(line)
    ]
    assert stragglers == [], f"these never reach the shaped reply: {stragglers}"


def test_the_sweep_is_calibrated_against_the_tree_it_polices():
    """A regex that stopped matching would make the sweep above vacuous."""
    assert _RAISES_UNTYPED_SLUG.search('raise ValueError(f"Workspace not found: {slug}")')
    assert _RAISES_UNTYPED_SLUG.search('raise ValueError("Workspace not found")')
    assert not _RAISES_UNTYPED_SLUG.search("raise UnknownWorkspaceSlug(workspace_slug)")


def test_an_unknown_slug_through_the_http_api_is_still_a_client_error(client):
    """The regression AC3 tells the implementer to check for, as a request.

    `PATCH /api/workspaces/{slug}/workflow` catches `ValueError` and answers
    400. If the typed condition stops being one, a browser gets a 500 from a
    change whose entire subject is the MCP transport.
    """
    response = client.patch(
        "/api/workspaces/no-such-workspace/workflow",
        json={"workflow_template_slug": "anything"},
    )
    assert response.status_code < 500
    assert response.status_code == 400


# --- AC4 what must still escape the shield ----------------------------------


@pytest.mark.parametrize(
    "signal", [KeyboardInterrupt(), SystemExit(1)], ids=["KeyboardInterrupt", "SystemExit"]
)
def test_an_interpreter_signal_is_never_absorbed_into_a_reply(isolated_db, signal):
    """`except Exception` does not catch these today, and must not start to.

    A shield widened to `BaseException` while someone was making the five
    conditions reachable would turn an operator's Ctrl-C into a tool reply
    saying everything is fine.
    """
    with Session(isolated_db) as session, pytest.raises(type(signal)):
        _call(session, raises=signal)


def test_an_unknown_exception_type_is_not_shaped_merely_for_subclassing_value_error():
    """The base is a `ValueError` for `api/`'s sake; that must not widen it.

    Every `raise ValueError` left in the tree — and there are hundreds — would
    otherwise become a recoverable condition with an empty remedy.
    """
    assert shape(ValueError("Unknown workflow template: ghost"))["shaped"] is False


def test_a_condition_declared_outside_the_module_is_still_classified():
    """Registry, not a hardcoded tuple of five.

    A sixth condition added by a later ticket must be shaped by inheritance,
    or the default branch quietly swallows it into an internal error.
    """

    class StoreUnseeded(RecoverableToolCondition):
        error = "the store exists and holds nothing"
        remedy = "seed it"
        guidance = "carry on without it"

    assert shape(StoreUnseeded("memory graph"))["shaped"] is True


def test_the_shaped_payload_is_json_serializable_and_carries_no_traceback():
    """The envelope `json.dumps` this. An exception object in a field raises
    at the boundary, where the failure reads as a tool fault rather than a
    serialization bug."""
    payload = shape(UnknownWorkspaceSlug("ghost"))
    text = json.dumps(payload)
    assert "Traceback" not in text
    assert "object at 0x" not in text


def test_the_reply_keeps_the_request_id_it_was_asked_under(isolated_db):
    """A success-shaped reply is still JSON-RPC. A client correlates on `id`."""
    with Session(isolated_db) as session:
        response = _call(session, raises=UnknownWorkspaceSlug("ghost"))
    assert response["id"] == "req-7"
    assert response["jsonrpc"] == "2.0"


def test_the_shaped_reply_is_still_a_content_list_the_transport_can_read(isolated_db):
    with Session(isolated_db) as session:
        response = _call(session, raises=StaleIndex("symbol map"))
    content = response["result"]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    json.loads(content[0]["text"])


# --- AC5 one row, and the vocabulary stays closed ---------------------------


def test_a_shaped_reply_writes_exactly_one_ledger_row(isolated_db):
    """Two rows would double-count the refusal on the operator's surface, and
    a `failed` row beside a `shaped` one would make the 5.4% figure the
    criterion is protecting unreadable."""
    with Session(isolated_db) as session:
        _call(session, raises=UnknownWorkspaceSlug("ghost"))
        rows = session.exec(select(McpToolCall)).all()
        assert len(rows) == 1
        assert rows[0].decision == DECISION_SHAPED
        assert rows[0].tool_name.endswith("loregarden_search_memory")


def test_the_decision_vocabulary_gains_shaped_and_loses_nothing():
    """Adding a value must not quietly retire one; `executed` and `failed`
    are what every row written before this change says."""
    assert set(DECISIONS) >= {"executed", "failed", "shaped"}
    assert len(set(DECISIONS)) == len(DECISIONS)


def test_a_shaped_reply_still_records_how_long_the_call_took(isolated_db):
    """The ledger's other columns are not optional on the new path."""
    with Session(isolated_db) as session:
        _call(session, raises=UnknownWorkspaceSlug("ghost"))
        row = session.exec(select(McpToolCall)).one()
        assert row.decision_ms is not None
        assert row.decision_ms >= 0


# --- AC6 forgery, from the directions the planted tests do not cover --------


def test_a_condition_subject_that_is_itself_a_marker_does_not_change_the_shape():
    """Caller text reaches the *subject*, not just the message.

    `UnknownWorkspaceSlug(<another condition's remedy>)` is a slug an agent can
    literally pass. It must still shape as an unknown slug, and must not pick
    up the other condition's remedy.
    """
    stale = shape(StaleIndex("symbol map"))
    forged = shape(UnknownWorkspaceSlug(stale["remedy"]))
    assert forged["shaped"] is True
    assert forged["remedy"] != stale["remedy"]


def test_an_exception_merely_chained_to_a_condition_is_not_shaped():
    """A tool that caught a recoverable condition and re-raised chose to fail.

    Walking `__cause__` would let any fault inherit a reassurance from
    something it already decided was fatal.
    """
    fault = RuntimeError("the index write failed")
    fault.__cause__ = StaleIndex("symbol map")
    assert shape(fault)["shaped"] is False


def test_shape_is_total_over_what_an_except_clause_can_hand_it():
    """The default branch is a default, not a known-type lookup.

    An exception with no message, a non-ASCII one, and one whose `str()` is
    itself JSON all reach `shape` from real tools.
    """
    for exc in (Exception(), RuntimeError(""), ValueError("ошибка"), TypeError('{"a": 1}')):
        shaped = shape(exc)
        assert shaped["shaped"] is False
        assert json.dumps(shaped)
