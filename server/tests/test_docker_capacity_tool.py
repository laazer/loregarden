"""The docker capacity tools, over the real JSON-RPC envelope.

Through `/mcp` rather than by calling the handlers directly, because the wiring
is most of what can break: an enum member added but never advertised, a
normalizer that drops a declared field, a handler registered under the wrong
name. Calling `execute_tool` directly would skip all three.

The contract pinned here beyond "it returns something":

- **Refusals are payloads, not exceptions.** An agent has to tell "wait your
  turn" from "this will never fit" from "you asked for something impossible",
  and those call for three different next actions.
- **`reserve` does not block.** The inline wait is clamped whatever the caller
  asks for, because this runs inside an agent's turn and spends the run's own
  timeout budget.
- **Policy placement is asserted, not assumed.** Force-release being
  auto-approved would hand a looping agent the one action here that can take
  capacity from work that is still running.
"""

from __future__ import annotations

import json

import pytest
from loregarden.config import settings
from loregarden.mcp.docker_capacity_tool import clamp_inline_wait
from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    CAPACITY_LEASE_MCP_TOOLS,
    DOCKER_CAPACITY_MCP_TOOLS,
    ORCHESTRATED_DENIED_MCP_TOOLS,
    READ_ONLY_MCP_TOOLS,
    McpTool,
)
from loregarden.models.domain import DockerCeilingSource, DockerGrantState
from loregarden.services import docker_leases
from sqlmodel import Session
from tests.mcp_helpers import call_mcp


@pytest.fixture(name="ceiling", autouse=True)
def ceiling_fixture(isolated_db):
    """A known ceiling, so these tests do not depend on the host's docker."""
    with Session(isolated_db) as session:
        pool = docker_leases.load_pool(session)
        pool.ceiling_cpus = 4.0
        pool.ceiling_memory_mb = 4096
        pool.ceiling_leases = 2
        pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
        session.add(pool)
        session.commit()


def _payload(response: dict) -> dict:
    assert "error" not in response, response
    return json.loads(response["result"]["content"][0]["text"])


def test_reserve_grants_and_status_shows_the_holder(client) -> None:
    granted = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "e2e suite", "footprint": "service"},
        )
    )
    assert granted["state"] == DockerGrantState.GRANTED.value
    assert granted["cpus"] == 1.0
    assert granted["memory_mb"] == 1024
    assert granted["expires_at"]
    assert granted["renew_after_seconds"] > 0, "a holder must be told when to renew"

    board = _payload(call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {}))
    assert board["in_use"]["leases"] == 1
    assert [h["holder_label"] for h in board["holders"]] == ["e2e suite"]
    assert board["ceiling"]["source"] == DockerCeilingSource.CONFIG_OVERRIDE.value


def test_a_full_pool_queues_and_says_how_to_wait(client) -> None:
    for index in range(2):
        assert (
            _payload(
                call_mcp(
                    client,
                    McpTool.RESERVE_DOCKER_CAPACITY.value,
                    {"holder_label": f"holder-{index}", "footprint": "service"},
                )
            )["state"]
            == DockerGrantState.GRANTED.value
        )

    queued = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "third", "footprint": "service"},
        )
    )
    assert queued["state"] == DockerGrantState.QUEUED.value
    assert queued["position"] is not None
    assert queued["poll_after_seconds"] > 0, "a queued caller must be told when to come back"
    assert queued["lease_id"], "a waiter needs an id to poll with"

    # And the poll finds it.
    board = _payload(
        call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {"lease_id": queued["lease_id"]})
    )
    assert board["lease"]["found"] is True
    assert board["lease"]["status"] == "waiting"


def test_releasing_promotes_the_waiter_and_the_poll_reports_it(client) -> None:
    first = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "first", "footprint": "stack"},
        )
    )
    queued = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "second", "footprint": "stack"},
        )
    )
    assert queued["state"] == DockerGrantState.QUEUED.value

    released = _payload(
        call_mcp(client, McpTool.RELEASE_DOCKER_CAPACITY.value, {"lease_id": first["lease_id"]})
    )
    assert released["released"] is True

    board = _payload(
        call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {"lease_id": queued["lease_id"]})
    )
    assert board["lease"]["status"] == "held"


def test_renew_records_what_was_started(client) -> None:
    """The bind. Until a lease names its containers it can only be reclaimed on
    its clock; once it does, the reaper asks docker before taking anything."""
    granted = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "stack owner", "footprint": "stack"},
        )
    )
    renewed = _payload(
        call_mcp(
            client,
            McpTool.RENEW_DOCKER_LEASE.value,
            {
                "lease_id": granted["lease_id"],
                "compose_project": "myapp",
                "container_names": ["myapp-db-1", "myapp-web-1"],
            },
        )
    )
    assert renewed["ok"] is True
    assert renewed["compose_project"] == "myapp"

    board = _payload(call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {}))
    holder = board["holders"][0]
    assert holder["compose_project"] == "myapp"
    assert holder["container_names"] == ["myapp-db-1", "myapp-web-1"]


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        (
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "vague"},
            "claim_too_vague",
        ),
        (
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "greedy", "cpus": 999, "memory_mb": 999999},
            docker_leases.REJECT_EXCEEDS_CAPACITY,
        ),
    ],
)
def test_a_refusal_is_a_payload_with_a_reason(client, tool, args, expected) -> None:
    """Not an exception, and not a bare failure. "Wait your turn", "this will
    never fit" and "you asked for something impossible" need three different
    responses from the agent, so they get three different `error_kind`s."""
    payload = _payload(call_mcp(client, tool, args))
    assert payload["state"] == DockerGrantState.REJECTED.value
    assert payload["error_kind"] == expected
    assert payload["message"]


def test_a_vague_claim_is_told_what_the_sizes_are(client) -> None:
    payload = _payload(
        call_mcp(client, McpTool.RESERVE_DOCKER_CAPACITY.value, {"holder_label": "vague"})
    )
    assert "stack" in payload["footprints"]
    assert payload["footprints"]["stack"] == {"cpus": 2.0, "memory_mb": 4096}


def test_operating_on_an_unknown_lease_says_so(client) -> None:
    for tool in (McpTool.RENEW_DOCKER_LEASE.value, McpTool.RELEASE_DOCKER_CAPACITY.value):
        payload = _payload(call_mcp(client, tool, {"lease_id": "nope"}))
        assert payload["ok"] is False or payload["released"] is False


def test_the_inline_wait_is_clamped_however_long_the_caller_asks() -> None:
    """The cap, asserted as arithmetic rather than as a stopwatch.

    An earlier version of this test measured the call and asserted `elapsed < 30`.
    `test_suite_clock_hermeticity` rejects that shape and is right to: it passes
    on an idle machine, fails under load, and asserts the handler is *fast*,
    which nothing here promises. What is promised is that a caller asking for
    ten minutes gets the cap — a pure function of the argument.
    """
    cap = settings.docker_lease_inline_wait_max_seconds
    assert clamp_inline_wait(600) == cap
    assert clamp_inline_wait(cap + 1) == cap
    assert clamp_inline_wait(0) == 0
    assert clamp_inline_wait(-5) == 0, "a negative request must not become an infinite one"
    assert clamp_inline_wait(cap / 2) == cap / 2, "a modest request is honoured as asked"


def test_a_queued_reservation_still_answers_when_an_inline_wait_was_asked_for(client) -> None:
    """The wait changes how long the answer takes, never what it says."""
    for index in range(2):
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": f"holder-{index}", "footprint": "service"},
        )

    payload = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "patient", "footprint": "service", "wait_seconds": 1},
        )
    )
    assert payload["state"] == DockerGrantState.QUEUED.value
    assert payload["poll_after_seconds"] > 0


def test_force_release_warns_that_it_stops_nothing(client) -> None:
    granted = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "someone else", "footprint": "service"},
        )
    )
    payload = _payload(
        call_mcp(
            client,
            McpTool.FORCE_RELEASE_DOCKER_LEASE.value,
            {"lease_id": granted["lease_id"], "reason": "owner is gone"},
        )
    )
    assert payload["released"] is True
    assert "still running" in payload["warning"], (
        "taking the ledger's word for it without stopping the containers is the "
        "whole hazard of this tool, and it must say so"
    )


# ---- policy placement --------------------------------------------------


def test_the_lease_tools_are_auto_approved() -> None:
    """An agent that must wait for a human click to RELEASE a lease will simply
    not release it, and the ledger drifts. Every one of these is undone by the
    reaper, which is what makes auto-approval cheap."""
    for tool in CAPACITY_LEASE_MCP_TOOLS:
        assert tool in AUTO_APPROVED_MCP_TOOLS
    assert McpTool.DOCKER_CAPACITY_STATUS in READ_ONLY_MCP_TOOLS
    assert McpTool.DOCKER_CAPACITY_STATUS in AUTO_APPROVED_MCP_TOOLS


def test_force_release_is_gated_and_denied_to_orchestrated_agents() -> None:
    """The one action here that can take capacity from work still running. A
    looping agent that finds the pool full has every reason to reach for it and
    no way to know whose work it would break."""
    assert McpTool.FORCE_RELEASE_DOCKER_LEASE not in AUTO_APPROVED_MCP_TOOLS
    assert McpTool.FORCE_RELEASE_DOCKER_LEASE not in CAPACITY_LEASE_MCP_TOOLS
    assert McpTool.FORCE_RELEASE_DOCKER_LEASE in ORCHESTRATED_DENIED_MCP_TOOLS


def test_the_grant_tuple_offers_everything_but_force_release() -> None:
    """A workspace adding these to an agent's tool list is granting ad-hoc
    capacity booking, not the ability to take a peer's lease away."""
    assert set(DOCKER_CAPACITY_MCP_TOOLS) == set(CAPACITY_LEASE_MCP_TOOLS) | {
        McpTool.DOCKER_CAPACITY_STATUS
    }
    assert McpTool.FORCE_RELEASE_DOCKER_LEASE not in DOCKER_CAPACITY_MCP_TOOLS
