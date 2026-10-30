"""The two edges, attacked with the arguments a real caller sends by accident.

`test_ticket_revision_edges.py` proves each edge carries the token and answers a
conflict in its transport's own vocabulary. This module is about the inputs
neither edge's happy path describes — a falsy token, a token that is not a
number, an absent ticket, a refused edit that must not have written half of
itself — and about two claims the contract's tests make through a mock rather
than through the behaviour:

* `test_omitting_the_token_patches_exactly_as_today` asserts the guard was not
  *called*. That is an interaction, and it passes against an edge that calls no
  guard because it has none. The behaviour behind it — two unguarded writes both
  land, last one wins — is asserted here against the real endpoint.
* `test_a_fresh_token_through_the_tool_layer_returns_the_new_revision` only sees
  `revision` in the MCP payload on the *guarded* path, so a payload that grew
  the key inside the `expected_revision` branch would pass it. S9(d) says the
  payload carries it; the unguarded call is where that is checked.

The MCP half is driven through `POST /mcp` throughout, for the reason the
contract's module gives: the argument allowlist is the thing under test, and a
hand-built arguments dict walks straight past it.
"""

from __future__ import annotations

import json

import pytest
from tests.mcp_helpers import call_mcp

# The same fixture the contract module builds: one bug under loregarden's
# milestone, created through the real endpoint so its revision starts where a
# real ticket's does. Imported rather than rebuilt — a second copy that drifted
# would make the two modules disagree about what they are attacking.
from tests.test_ticket_revision_edges import _ticket


def _detail(client, ticket_id: str) -> dict:
    return client.get(f"/api/tickets/{ticket_id}").json()


def _mcp_text(body: dict) -> str:
    return body["result"]["content"][0]["text"]


# ---- the HTTP edge: tokens that are not a fresh integer ---------------


def test_an_explicit_null_token_is_the_same_as_omitting_it(client) -> None:
    """`{"expected_revision": null}` is a client that has no revision yet.

    The pane sends the key on every edit-form save, and a draft seeded before
    the first load has nothing to put in it. `is not None` is the whole gate, so
    an explicit null must be today's behaviour rather than a guard against the
    revision `None`.
    """
    ticket = _ticket(client)

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "Renamed with a null token", "expected_revision": None},
    )

    assert res.status_code == 200, res.text
    assert res.json()["title"] == "Renamed with a null token"


def test_a_zero_token_is_honoured_rather_than_read_as_absent(client) -> None:
    """0 is falsy and is also the revision every freshly created ticket holds.

    So the edge cannot test truthiness, and both halves are reachable in one
    test: a ticket nobody has edited is at 0, so a token of 0 is *fresh* and
    must land; once that edit has moved the row, the same token is *stale* and
    must be refused. An edge gated on `if body.expected_revision:` passes the
    first half for the wrong reason and answers 200 to the second.
    """
    ticket = _ticket(client)
    assert _detail(client, ticket["id"])["revision"] == 0, "a new ticket holds the falsy revision"

    landed = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "Renamed against revision zero", "expected_revision": 0},
    )

    assert landed.status_code == 200, landed.text
    assert landed.json()["revision"] > 0

    refused = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "Renamed against revision zero again", "expected_revision": 0},
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["expected_revision"] == 0
    assert _detail(client, ticket["id"])["title"] == "Renamed against revision zero"


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("prose", "not a number"),
        ("a fraction", 5.5),
        ("a list", [5]),
        ("an object", {"revision": 5}),
    ],
)
def test_a_token_that_is_not_an_integer_is_refused_before_anything_is_written(
    client, label: str, token: object
) -> None:
    """422 from the edge model, and the ticket untouched.

    The token arrives as JSON from a browser, so every one of these is a shape
    the endpoint will eventually be sent. What matters is not the status code so
    much as the ordering: a request the model rejects must never have reached
    the applier, or a malformed token would be a write.
    """
    ticket = _ticket(client)
    before = _detail(client, ticket["id"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "A title that must not land", "expected_revision": token},
    )

    assert res.status_code == 422, f"{label}: {res.text}"
    after = _detail(client, ticket["id"])
    assert after["title"] == before["title"]
    assert after["revision"] == before["revision"]


def test_a_token_on_a_ticket_that_does_not_exist_is_still_a_404(client) -> None:
    """The guard sits inside the handler, after the row is found.

    A guard hoisted above the lookup would claim nothing, re-read nothing, and
    answer 409 with whatever it made of a missing row — a conflict about a
    ticket that never existed, in place of the 404 every other verb returns.
    """
    res = client.patch(
        "/api/tickets/no-such-ticket-id",
        json={"title": "Renamed", "expected_revision": 3},
    )

    assert res.status_code == 404, res.text


def test_a_refused_patch_lands_none_of_the_fields_it_carried(client) -> None:
    """A 409 is all-or-nothing, across every field the one request held.

    The contract's test checks the title. A guard that ran after the applier
    would pass that and still have committed the state change, the description
    and the tags — and then answered 409, which is the worst of both.
    """
    ticket = _ticket(client)
    stale = _detail(client, ticket["id"])["revision"]
    client.patch(f"/api/tickets/{ticket['id']}", json={"title": "Moved on by somebody else"})
    before = _detail(client, ticket["id"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={
            "title": "A title that must not land",
            "description": "A description that must not land",
            "state": "blocked",
            "priority": 3,
            "tags": ["must-not-land"],
            "acceptance_criteria": ["must not land"],
            "expected_revision": stale,
        },
    )

    assert res.status_code == 409, res.text
    after = _detail(client, ticket["id"])
    for field in ("title", "description", "state", "priority", "tags", "acceptance_criteria"):
        assert after[field] == before[field], f"{field} was written by a refused patch"
    assert after["revision"] == before["revision"], "a refused patch burned a revision"


def test_two_unguarded_patches_are_still_last_write_wins(client) -> None:
    """Today's behaviour, through the endpoint, with nothing mocked.

    This is the orchestrator's exemption stated as behaviour: a stage completing
    while somebody else writes must not be refused. Asserting that the guard was
    not *called* says the same thing only as long as a guard exists to call —
    the first version of this change, with the edge untouched, passes that
    assertion and fails this one.
    """
    ticket = _ticket(client)

    first = client.patch(f"/api/tickets/{ticket['id']}", json={"title": "First unguarded"})
    second = client.patch(f"/api/tickets/{ticket['id']}", json={"title": "Second unguarded"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert _detail(client, ticket["id"])["title"] == "Second unguarded"


def test_the_token_is_not_stored_as_a_field_of_the_ticket(client) -> None:
    """The edge model carries the token; the applier must read nothing new.

    `TicketPatchRequest` is handed to `update_ticket_manual` unchanged, so an
    applier that round-tripped the request through `model_dump` would meet a key
    it has no column for. A successful guarded save proves the subclass passes
    through the shared applier intact.
    """
    ticket = _ticket(client)
    revision = _detail(client, ticket["id"])["revision"]

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"title": "Renamed, guarded", "expected_revision": revision},
    )

    assert res.status_code == 200, res.text
    assert "expected_revision" not in res.json()


# ---- the MCP edge: the allowlist, and the order of its refusals -------


def test_the_mcp_payload_carries_the_revision_on_an_unguarded_call(client) -> None:
    """S9(d) is about the payload, not about the guarded branch of the handler.

    `ticket_state_payload` returns no revision at all today, and it is the reply
    every write on this transport gets — including the eight system writers'.
    An agent cannot send a token on its next call if the key only appears when
    it sent one on this call.
    """
    ticket = _ticket(client)

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Renamed via MCP, unguarded"},
    )

    assert body["result"].get("isError") is not True, body
    payload = json.loads(_mcp_text(body))
    assert "revision" in payload, "the reply cannot be used to send the next token"
    assert type(payload["revision"]) is int
    assert payload["revision"] == _detail(client, ticket["id"])["revision"]


def test_a_zero_token_survives_the_allowlist(client) -> None:
    """The normalizer's own idiom is `if args.get(field) is not None`, and the
    one argument where the easier `if args.get(field)` differs is this one.

    Dropped there, the token never reaches the handler and the write lands
    unguarded — the exact silent no-op the MCP half of this change is most at
    risk of, and invisible to any test that sends a non-zero number. Both halves
    again, because a new ticket really is at revision 0: fresh must land, and
    the same token stale must be refused.
    """
    ticket = _ticket(client)
    assert _detail(client, ticket["id"])["revision"] == 0

    landed = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Renamed against zero", "expected_revision": 0},
    )
    assert landed["result"].get("isError") is not True, landed

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Renamed against zero again", "expected_revision": 0},
    )

    assert body["result"]["isError"] is True, body
    conflict = json.loads(_mcp_text(body))
    assert conflict["error"] == "revision_conflict"
    assert conflict["expected_revision"] == 0
    assert _detail(client, ticket["id"])["title"] == "Renamed against zero"


def test_a_numeric_string_token_is_coerced_like_every_other_integer_argument(client) -> None:
    """An agent writing JSON by hand sends `"7"`; `priority` already accepts it.

    The injected coercer is `coerce_optional_int`, so the token inherits that
    behaviour rather than defining its own — and a handler that compared the raw
    string to an integer column would refuse a fresh token with a conflict.
    """
    ticket = _ticket(client)
    revision = _detail(client, ticket["id"])["revision"]

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket["id"],
            "title": "Renamed with a string token",
            "expected_revision": str(revision),
        },
    )

    assert body["result"].get("isError") is not True, body
    assert _detail(client, ticket["id"])["title"] == "Renamed with a string token"


@pytest.mark.parametrize(("label", "token"), [("prose", "not a number"), ("a fraction", 5.5)])
def test_a_token_the_coercer_refuses_writes_nothing(client, label: str, token: object) -> None:
    """The refusal is the tool's own error, and the edit does not land anyway.

    A coercion that fell through to `None` would be worse than an error: the
    call would succeed, unguarded, having been told to guard.
    """
    ticket = _ticket(client)
    before = _detail(client, ticket["id"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket["id"],
            "title": "A title that must not land",
            "expected_revision": token,
        },
    )

    assert body["result"]["isError"] is True, f"{label}: {body}"
    after = _detail(client, ticket["id"])
    assert after["title"] == before["title"]
    assert after["revision"] == before["revision"]


def test_a_stale_token_through_mcp_lands_none_of_the_fields_it_carried(client) -> None:
    """The same all-or-nothing claim as the HTTP edge, on the other transport."""
    ticket = _ticket(client)
    stale = _detail(client, ticket["id"])["revision"]
    call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Moved on by somebody else"},
    )
    before = _detail(client, ticket["id"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket["id"],
            "title": "A title that must not land",
            "description": "A description that must not land",
            "state": "blocked",
            "acceptance_criteria": ["must not land"],
            "expected_revision": stale,
        },
    )

    assert body["result"]["isError"] is True, body
    after = _detail(client, ticket["id"])
    for field in ("title", "description", "state", "acceptance_criteria"):
        assert after[field] == before[field], f"{field} was written by a refused call"
    assert after["revision"] == before["revision"]


def test_a_stale_token_with_nothing_else_to_update_writes_nothing(client) -> None:
    """Two refusals race for this call, and either is defensible — but not a write.

    `{ticket_id, expected_revision}` is both "nothing to update" and a conflict.
    Which error wins depends on whether the handler checks the fields or the
    token first, and no criterion settles it. What is not negotiable is that a
    call carrying no edit cannot consume a revision: that would make the pane's
    own retry a writer.
    """
    ticket = _ticket(client)
    stale = _detail(client, ticket["id"])["revision"]
    call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "title": "Moved on by somebody else"},
    )
    before = _detail(client, ticket["id"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "expected_revision": stale},
    )

    assert body["result"]["isError"] is True, body
    text = _mcp_text(body)
    assert "Nothing to update" in text or json.loads(text)["error"] == "revision_conflict", text
    after = _detail(client, ticket["id"])
    assert after["revision"] == before["revision"], "a call with no edit burned a revision"
    assert after["title"] == before["title"]


def test_an_unknown_ticket_with_a_token_reports_the_ticket_not_a_conflict(client) -> None:
    """The id is resolved before the guard, as it is on the HTTP edge.

    A guard above the resolve would report a revision conflict for a ticket id
    that does not exist, which sends an agent looking for a concurrent writer
    instead of a typo.
    """
    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": "lg-no-such-ticket-9999", "title": "Renamed", "expected_revision": 3},
    )

    assert body["result"]["isError"] is True, body
    text = _mcp_text(body)
    assert "revision_conflict" not in text, text
