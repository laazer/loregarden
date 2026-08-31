"""Priority is editable after a ticket is filed, not only when it is created.

`loregarden_create_ticket` has always taken a priority and `UpdateTicketRequest`
has always accepted one, but the update tool's schema omitted the field — so an
agent driving through MCP could file a ticket at a priority and never change it.
The normalizer whitelist is the reason a missing schema entry is not merely
cosmetic: an argument it does not list is dropped before the handler sees it.
"""

from __future__ import annotations

import pytest
from loregarden.mcp.ticket_edit_tools import normalize_update_ticket_args
from loregarden.mcp.tools import TOOL_DEFINITIONS


def _coerce_string(value, *, field):
    return str(value).strip()


def _coerce_string_list(value, *, field):
    return [str(v) for v in value]


def _coerce_int(value, *, field):
    return None if value is None or value == "" else int(value)


def _update_tool_schema() -> dict:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == "loregarden_update_ticket":
            return tool["inputSchema"]
    raise AssertionError("loregarden_update_ticket is not defined")


def test_the_schema_advertises_priority() -> None:
    assert "priority" in _update_tool_schema()["properties"]


def test_the_normalizer_passes_priority_through() -> None:
    payload = normalize_update_ticket_args(
        {"ticket_id": "lg-x-1", "priority": 1},
        coerce_string=_coerce_string,
        coerce_string_list=_coerce_string_list,
        coerce_int=_coerce_int,
    )
    assert payload["priority"] == 1


def test_priority_is_omitted_when_not_supplied() -> None:
    """An absent field must not become a write — the tool edits what it is given."""
    payload = normalize_update_ticket_args(
        {"ticket_id": "lg-x-1", "title": "t"},
        coerce_string=_coerce_string,
        coerce_string_list=_coerce_string_list,
        coerce_int=_coerce_int,
    )
    assert "priority" not in payload


@pytest.mark.parametrize("bad", [0, 4, -1])
def test_a_priority_outside_the_range_is_refused(bad: int) -> None:
    from loregarden.mcp.ticket_edit_tools import _collect_update_fields
    from loregarden.models.domain import Ticket

    ticket = Ticket(workspace_id="w", title="t")
    with pytest.raises(ValueError, match="must be in"):
        _collect_update_fields(ticket, {"ticket_id": "lg-x-1", "priority": bad})


def test_priority_alone_is_enough_to_be_a_real_update() -> None:
    """The 'nothing to update' guard must not reject a priority-only edit."""
    from loregarden.mcp.ticket_edit_tools import _collect_update_fields
    from loregarden.models.domain import Ticket

    ticket = Ticket(workspace_id="w", title="t")
    assert _collect_update_fields(ticket, {"ticket_id": "lg-x-1", "priority": 2}) == {"priority": 2}
