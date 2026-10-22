"""The two caller-driven edges, and nothing else, carry `expected_revision`.

The guard itself is covered in `test_ticket_revision_cas.py`. This module is
about the two places a caller can reach it — `PATCH /api/tickets/{id}` and
`loregarden_update_ticket` — and about the shapes each transport needs:

* the token enters on an edge model, not on the shared `UpdateTicketRequest`,
  which eight internal callers construct;
* the HTTP conflict is a 409 whose body carries `current_revision` as a number,
  not prose a caller has to regex;
* the MCP conflict rides the only channel that transport has, `isError` plus
  `str(exc)`, and that text parses as JSON;
* and on both transports, omitting the token is today's behaviour exactly, which
  is how the orchestrator's writers stay exempt — structurally, by never
  reaching the guard at all.

**The MCP half is driven through the tool layer on purpose.** The rank-one risk
of this change is that `normalize_update_ticket_args` silently drops the
argument, so the guard ships as a no-op behind a tool that looks like it
supports the token — and every service-level test still passes. A test that
calls the handler with a hand-built dict does not exercise the allowlist, and
the allowlist is the thing under test.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.mcp.ticket_edit_tools import (
    _collect_update_fields,
    normalize_update_ticket_args,
)
from loregarden.mcp.tool_args import coerce_optional_bool
from loregarden.mcp.tools import TOOL_DEFINITIONS
from loregarden.models.domain import Ticket, UpdateTicketRequest
from loregarden.models.domain.schemas import TicketPatchRequest
from pydantic import ValidationError
from tests.mcp_helpers import call_mcp

# ---- fixtures ---------------------------------------------------------


def _ticket(client, title: str = "Guarded edit target") -> dict:
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": title,
            "work_item_type": "bug",
            "parent_ticket_id": milestone_id,
            "description": "Revision guard fixture.",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _revision(client, ticket_id: str) -> int:
    return client.get(f"/api/tickets/{ticket_id}").json()["revision"]


def _title(client, ticket_id: str) -> str:
    return client.get(f"/api/tickets/{ticket_id}").json()["title"]


def _coerce_string(value, *, field):
    return str(value).strip()


def _coerce_string_list(value, *, field):
    return [str(v) for v in value]


def _coerce_int(value, *, field):
    return None if value is None or value == "" else int(value)


def _normalize(args: dict) -> dict:
    return normalize_update_ticket_args(
        args,
        coerce_string=_coerce_string,
        coerce_string_list=_coerce_string_list,
        coerce_int=_coerce_int,
        coerce_bool=coerce_optional_bool,
    )


def _update_tool_schema() -> dict:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == "loregarden_update_ticket":
            return tool["inputSchema"]
    raise AssertionError("loregarden_update_ticket is not defined")


def _mcp_text(body: dict) -> str:
    return body["result"]["content"][0]["text"]


# ---- the edge model --------------------------------------------------


def test_the_patch_model_adds_only_the_token() -> None:
    """Every parent field, plus one — and the shared DTO is untouched.

    The token lives here rather than on `UpdateTicketRequest` because that model
    is `extra="forbid"` and is built by eight internal callers; a field there
    that the applier never reads would read as supported on paths where it does
    nothing.
    """
    parent = set(UpdateTicketRequest.model_fields)
    child = set(TicketPatchRequest.model_fields)

    assert parent < child, "the patch model must carry every field a caller can already send"
    assert child - parent == {"expected_revision"}
    assert "expected_revision" not in parent, "the shared DTO must not grow the token"
    assert TicketPatchRequest().expected_revision is None


def test_the_patch_model_still_refuses_an_unknown_key() -> None:
    """`extra="forbid"` is inherited. PATCH answering 200 to a write it had
    silently discarded is the failure that rule exists for, and a subclass is
    the easiest place to lose it."""
    with pytest.raises(ValidationError):
        TicketPatchRequest(title="fine", nonsense="not a field")


# ---- the HTTP edge ---------------------------------------------------


def test_omitting_the_token_patches_exactly_as_today(client) -> None:
    """No token, no claim — so no system caller can be made to fail on contention."""
    ticket = _ticket(client)

    with patch("loregarden.api.tickets.guard_ticket_revision") as guard:
        res = client.patch(f"/api/tickets/{ticket['id']}", json={"title": "Renamed, unguarded"})

    assert res.status_code == 200, res.text
    assert res.json()["title"] == "Renamed, unguarded"
    guard.assert_not_called()


def test_a_fresh_token_patches_and_returns_the_stored_revision(client) -> None:
    """200, and the revision in the reply is the one in the row.

    Already true via `session.refresh` plus `get_ticket`; asserted so that
    staying true is somebody's job.
    """
    ticket = _ticket(client)
    revision = _revision(client, ticket["id"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "Renamed, guarded", "expected_revision": revision},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "Renamed, guarded"
    assert body["revision"] > revision
    assert body["revision"] == _revision(client, ticket["id"])


def test_a_stale_token_is_a_409_carrying_the_current_revision_as_a_number(client) -> None:
    """The conflict is machine-readable and the operator's edit did not land."""
    ticket = _ticket(client)
    stale = _revision(client, ticket["id"])
    landed = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "The write that landed", "expected_revision": stale},
    )
    assert landed.status_code == 200, landed.text

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "The write that lost", "expected_revision": stale},
    )

    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "revision_conflict"
    assert detail["expected_revision"] == stale
    assert isinstance(detail["current_revision"], int)
    assert detail["current_revision"] > stale
    assert _title(client, ticket["id"]) == "The write that landed"


def test_a_conflict_is_not_degraded_to_a_bad_request(client) -> None:
    """409, never 400. The handler maps `ValueError` to 400, so a conflict that
    were a `ValueError` would answer 400 with prose — a status a caller cannot
    tell from a malformed body, carrying no number to recover with."""
    ticket = _ticket(client)
    stale = _revision(client, ticket["id"])
    client.patch(f"/api/tickets/{ticket['id']}", json={"title": "Moved on"})

    res = client.patch(
        f"/api/tickets/{ticket['id']}", json={"title": "Too late", "expected_revision": stale}
    )

    assert res.status_code == 409, res.text


def test_an_unknown_patch_key_is_still_refused_over_http(client) -> None:
    ticket = _ticket(client)

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"not_a_field": "x"})

    assert res.status_code == 422, res.text


# ---- the MCP edge, driven through the tool layer ---------------------


def test_the_update_tool_schema_advertises_the_token_as_an_integer() -> None:
    """An argument the schema omits is an argument an agent never learns about."""
    properties = _update_tool_schema()["properties"]

    assert "expected_revision" in properties
    assert properties["expected_revision"]["type"] == "integer"
    assert properties["expected_revision"].get("description", "").strip(), (
        "the description is where an agent reads that omitting it keeps today's behaviour"
    )
    assert "expected_revision" not in _update_tool_schema().get("required", [])


def test_the_normalizer_passes_the_token_through() -> None:
    """The allowlist is the rank-one risk: an argument it does not list is
    dropped before the handler sees it, which is how `acceptance_criteria` once
    went missing while every service-level test passed."""
    payload = _normalize({"ticket_id": "lg-x-1", "title": "t", "expected_revision": 12})

    assert payload["expected_revision"] == 12
    assert isinstance(payload["expected_revision"], int)


def test_the_normalizer_omits_the_token_when_it_is_not_supplied() -> None:
    payload = _normalize({"ticket_id": "lg-x-1", "title": "t"})

    assert "expected_revision" not in payload


def test_the_token_never_becomes_an_update_field() -> None:
    """It must not reach `_collect_update_fields` or `UpdateTicketRequest`.

    If it did, `{ticket_id, expected_revision}` would stop being a no-op and
    become a legal write that burns a revision and changes nothing.
    """
    ticket = Ticket(workspace_id="w", title="t")

    fields = _collect_update_fields(ticket, {"ticket_id": "lg-x-1", "expected_revision": 3})

    assert fields == {}


def test_the_token_alone_is_still_nothing_to_update(client) -> None:
    ticket = _ticket(client)
    revision = _revision(client, ticket["id"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "expected_revision": revision},
    )

    assert body["result"]["isError"] is True, body
    assert "Nothing to update" in _mcp_text(body)
    assert _revision(client, ticket["id"]) == revision, "a refused call still burned a revision"


def test_a_stale_token_through_the_tool_layer_is_a_parseable_conflict(client) -> None:
    """The only channel this transport has is `isError` plus the text, so the
    text is the payload: one JSON line carrying the same vocabulary HTTP uses."""
    ticket = _ticket(client)
    stale = _revision(client, ticket["id"])
    landed = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "The write that landed", "expected_revision": stale},
    )
    assert landed["result"].get("isError") is not True, landed

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "The write that lost", "expected_revision": stale},
    )

    assert body["result"]["isError"] is True, body
    conflict = json.loads(_mcp_text(body))
    assert conflict["error"] == "revision_conflict"
    assert conflict["expected_revision"] == stale
    assert isinstance(conflict["current_revision"], int)
    assert conflict["current_revision"] > stale
    assert _title(client, ticket["id"]) == "The write that landed"


def test_a_fresh_token_through_the_tool_layer_returns_the_new_revision(client) -> None:
    """This, not `api/tickets.py`, is what makes "a successful update returns
    the new revision" true on this path: `ticket_state_payload` returns no
    revision at all today."""
    ticket = _ticket(client)
    revision = _revision(client, ticket["id"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Renamed via MCP", "expected_revision": revision},
    )

    assert body["result"].get("isError") is not True, body
    payload = json.loads(_mcp_text(body))
    assert payload["revision"] == _revision(client, ticket["id"])
    assert payload["revision"] > revision


def test_omitting_the_token_through_the_tool_layer_behaves_as_today(client) -> None:
    ticket = _ticket(client)

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Renamed via MCP, unguarded"},
    )

    assert body["result"].get("isError") is not True, body
    assert _title(client, ticket["id"]) == "Renamed via MCP, unguarded"
