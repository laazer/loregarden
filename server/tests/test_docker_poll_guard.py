"""Back-pressure on the poll path, and the two ways it must not misbehave.

`poll_after_seconds` is advice, and advice is not a limit — an agent in a retry
loop calls as fast as the transport allows, and every one of those calls used to
run a promotion pass, which writes. The ledger would become its own bottleneck
at the moment it is most contended.

Two properties are load-bearing and neither is obvious:

- **A throttled call still tells the truth.** Answering "slow down" *instead of*
  "your lease was granted" would strand work that already had capacity.
- **A refused poll does not reset the window.** Stamping the clock on a refusal
  would let a tight loop hold it open forever: every call arrives too soon after
  the last refusal and the caller is never served at all. That is a livelock the
  rate limiter would have created.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from loregarden.config import settings
from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from loregarden.services.docker_poll_guard import note_poll
from sqlmodel import Session, select
from tests.mcp_helpers import call_mcp

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ceiling", autouse=True)
def ceiling_fixture(isolated_db):
    with Session(isolated_db) as session:
        pool = docker_leases.load_pool(session)
        pool.ceiling_cpus = 1.0
        pool.ceiling_memory_mb = 1024
        pool.ceiling_leases = 1
        pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
        session.add(pool)
        session.commit()


def _lease(session) -> DockerLease:
    reservation = docker_leases.reserve(
        session, holder_label="poller", footprint=DockerFootprint.CUSTOM, cpus=1.0, memory_mb=1024
    )
    return session.get(DockerLease, reservation.lease_id)


def test_the_first_poll_is_always_served(session) -> None:
    decision = note_poll(session, _lease(session), now=NOW)
    assert decision.throttled is False
    assert decision.retry_after_seconds == 0
    assert decision.poll_count == 1


def test_a_second_poll_inside_the_window_is_throttled(session) -> None:
    lease = _lease(session)
    note_poll(session, lease, now=NOW, min_interval=5.0)
    decision = note_poll(session, lease, now=NOW + timedelta(seconds=1), min_interval=5.0)

    assert decision.throttled is True
    assert decision.retry_after_seconds == 4
    assert decision.poll_count == 2


def test_a_poll_after_the_window_is_served_again(session) -> None:
    lease = _lease(session)
    note_poll(session, lease, now=NOW, min_interval=5.0)
    decision = note_poll(session, lease, now=NOW + timedelta(seconds=6), min_interval=5.0)
    assert decision.throttled is False


def test_a_refused_poll_does_not_hold_the_window_open(session) -> None:
    """The livelock this rate limiter would otherwise have created.

    A caller hammering every second is refused each time — and must still be
    served once the interval has elapsed *from the last served poll*, not from
    the last refusal. Stamping refusals would push the window forward on every
    call and the caller would never be served at all.
    """
    lease = _lease(session)
    note_poll(session, lease, now=NOW, min_interval=5.0)
    for offset in (1, 2, 3, 4):
        assert note_poll(
            session, lease, now=NOW + timedelta(seconds=offset), min_interval=5.0
        ).throttled

    served = note_poll(session, lease, now=NOW + timedelta(seconds=5.1), min_interval=5.0)
    assert served.throttled is False, "a steady hammer would never be served again"


def test_every_attempt_is_counted_even_when_refused(session) -> None:
    """A caller polling ten times a second is a fact worth having on the row: it
    is the difference between "the queue is slow" and "something is spinning on
    it", and only one of those is fixed by adding capacity."""
    lease = _lease(session)
    for offset in range(6):
        note_poll(session, lease, now=NOW + timedelta(seconds=offset * 0.5), min_interval=5.0)

    session.refresh(lease)
    assert lease.poll_count == 6
    assert lease.throttled_poll_count == 5


# ---- through the tool ---------------------------------------------------


def _payload(response: dict) -> dict:
    assert "error" not in response, response
    return json.loads(response["result"]["content"][0]["text"])


def _seed(client) -> tuple[str, str]:
    held = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "holder", "cpus": 1, "memory_mb": 1024},
        )
    )
    queued = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "waiter", "cpus": 1, "memory_mb": 1024},
        )
    )
    return held["lease_id"], queued["lease_id"]


def test_a_throttled_poll_still_reports_the_real_state(client) -> None:
    """Answering "slow down" instead of "you were granted" would strand work
    that already holds capacity."""
    held_id, queued_id = _seed(client)
    # These calls run on the wall clock, and on a loaded machine they took longer
    # than the real 5s window, so the second poll was served. An hour cannot pass.
    with patch.object(settings, "docker_poll_min_interval_seconds", 3600.0):
        call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {"lease_id": queued_id})

        # Free the capacity, then poll again immediately — inside the window.
        call_mcp(client, McpTool.RELEASE_DOCKER_CAPACITY.value, {"lease_id": held_id})
        board = _payload(
            call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {"lease_id": queued_id})
        )

    assert board["poll"]["throttled"] is True
    assert board["poll"]["retry_after_seconds"] > 0
    assert board["lease"]["status"] == DockerLeaseStatus.HELD.value, (
        "the caller was granted its lease and must be told so, throttled or not"
    )


def _room_for_one_more(isolated_db) -> None:
    """Free capacity without a release, which would promote the waiter itself."""
    with Session(isolated_db) as session:
        pool = docker_leases.load_pool(session)
        pool.ceiling_cpus += 1
        pool.ceiling_memory_mb += 1024
        pool.ceiling_leases += 1
        session.add(pool)
        session.commit()


def _poll(client, lease_id: str) -> dict:
    return _payload(call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {"lease_id": lease_id}))


def test_a_served_poll_advances_the_queue(isolated_db, client) -> None:
    """A caller with no timer of its own is promoted by its own poll."""
    _, queued_id = _seed(client)
    _room_for_one_more(isolated_db)

    board = _poll(client, queued_id)

    assert board["poll"]["throttled"] is False
    assert "message" not in board["poll"], "nothing to slow down from"
    assert board["lease"]["status"] == DockerLeaseStatus.HELD.value


def test_a_throttled_poll_is_served_from_the_row_without_a_promotion_pass(
    isolated_db, client
) -> None:
    _, queued_id = _seed(client)
    _poll(client, queued_id)
    _room_for_one_more(isolated_db)

    board = _poll(client, queued_id)

    assert board["poll"]["throttled"] is True
    assert board["lease"]["status"] == DockerLeaseStatus.WAITING.value
    assert board["poll"]["message"], "a throttled caller is told when to ask again"


def test_the_queue_can_be_left_off_the_board(client) -> None:
    _seed(client)

    def board(args: dict) -> dict:
        return _payload(call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, args))

    assert board({})["waiting"], "the queue is on the board by default"
    assert "waiting" not in board({"include_waiting": False})
    assert "lease" not in board({}), "a board read names no lease"
    assert board({"lease_id": "no-such-lease"})["lease"] == {
        "lease_id": "no-such-lease",
        "found": False,
    }


def test_a_board_read_with_no_lease_is_not_throttled(client) -> None:
    """Reading the board is not polling for a lease; an operator refreshing a
    dashboard is not a retry loop."""
    _seed(client)
    for _ in range(4):
        board = _payload(call_mcp(client, McpTool.DOCKER_CAPACITY_STATUS.value, {}))
        assert "poll" not in board


def test_the_queued_reply_carries_an_estimate_and_a_scaled_interval(client) -> None:
    _seed(client)
    queued = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "third", "cpus": 1, "memory_mb": 1024},
        )
    )
    assert queued["state"] == "queued"
    assert queued["poll_after_seconds"] >= 5
    assert "estimated_wait_seconds" in queued


# ---- duplicate suppression ----------------------------------------------


def test_reserving_again_returns_the_same_place_in_line(client) -> None:
    """The `add_to_lane` defect in a new table: three retries produced three
    entries. Here duplicates hold positions ahead of real work and, being
    head-of-line, block it."""
    _seed(client)
    first = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "retrier", "cpus": 1, "memory_mb": 1024},
        )
    )
    second = _payload(
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": "retrier", "cpus": 1, "memory_mb": 1024},
        )
    )
    assert first["state"] == second["state"] == "queued"
    assert second["lease_id"] == first["lease_id"]
    assert second["reused"] is True
    assert second["position"] == first["position"]


def test_a_differently_labelled_claim_is_a_separate_place_in_line(isolated_db, client) -> None:
    """Identity is the label plus the price, so two genuinely separate stacks
    queue separately — they just have to say which is which."""
    _seed(client)
    for label in ("stack-a", "stack-b"):
        call_mcp(
            client,
            McpTool.RESERVE_DOCKER_CAPACITY.value,
            {"holder_label": label, "cpus": 1, "memory_mb": 1024},
        )

    with Session(isolated_db) as session:
        waiting = session.exec(
            select(DockerLease).where(DockerLease.status == DockerLeaseStatus.WAITING)
        ).all()
    labels = sorted(lease.holder_label for lease in waiting)
    assert labels == ["stack-a", "stack-b", "waiter"]
