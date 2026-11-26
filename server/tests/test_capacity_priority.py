"""A waiting lease put ahead of the line goes first, for a reason, and never preempts.

The line is strict FIFO; on 2026-11-25 the two fixes that would have sped up
every suite were 17th and 18th of 18. Prioritizing moves a waiter into a tier
ahead of every normal waiter (FIFO within each tier), changes nothing about
holders, and keeps `capacity_passing`'s promise to whichever lease is now the
head (lg-machine-resource-2807).
"""

from __future__ import annotations

import pytest
from loregarden.agents.executors.tool_auto_approve import is_auto_approved_mcp_tool
from loregarden.config import settings
from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    DOCKER_CAPACITY_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    McpTool,
)
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerHolderKind,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from loregarden.services.capacity_line import leases_ahead
from loregarden.services.capacity_priority import LeaseNotWaiting, prioritize_lease
from loregarden.services.docker_board import capacity_status
from loregarden.services.docker_leases import UnknownLease
from sqlmodel import Session, select
from tests.mcp_helpers import call_mcp
from tests.test_capacity_host_pool import _set_ceiling

HEAVY = 4.0


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        # The development machine's host pool: one heavy suite at a time.
        _set_ceiling(session, CapacityPool.HOST, cpus=7, memory_mb=48_000, leases=6)
        _set_ceiling(session, CapacityPool.DOCKER, cpus=6.5, memory_mb=9_929, leases=4)
        yield session


def _suite(session: Session, label: str, *, priority_reason: str = ""):
    return docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=HEAVY,
        memory_mb=8192,
        pool=CapacityPool.HOST,
        priority_reason=priority_reason,
    )


def _lease(session: Session, lease_id: str) -> DockerLease:
    session.expire_all()
    lease = session.get(DockerLease, lease_id)
    assert lease is not None
    return lease


def _line(session: Session) -> list[str]:
    """Holder labels of the waiters, in the order the board (and the granter) reads them."""
    return [row["holder_label"] for row in capacity_status(session)["waiting"]]


def _release_holder(session: Session) -> None:
    holder = next(
        lease
        for lease in session.exec(
            select(DockerLease).where(DockerLease.status == DockerLeaseStatus.HELD)
        ).all()
    )
    docker_leases.release_lease(session, holder.id)


@pytest.fixture(name="queued")
def queued_fixture(session):
    """One suite holding the machine and three queued behind it, in order."""
    running = _suite(session, "running")
    assert running.granted
    waiters = {label: _suite(session, label) for label in ("first", "second", "third")}
    assert not any(w.granted for w in waiters.values())
    return running, waiters


# ---- ordering ----------------------------------------------------------


def test_a_prioritized_waiter_goes_ahead_of_every_normal_one(session, queued) -> None:
    _, waiters = queued

    result = prioritize_lease(session, waiters["third"].lease_id, reason="fix push")

    assert result.moved and result.ahead == 0
    assert _line(session) == ["third", "first", "second"]
    assert _lease(session, waiters["third"].lease_id).priority_reason == "fix push"


def test_priority_waiters_keep_the_order_they_were_prioritized_in(session, queued) -> None:
    _, waiters = queued

    prioritize_lease(session, waiters["third"].lease_id, reason="a")
    prioritize_lease(session, waiters["second"].lease_id, reason="b")

    # FIFO within each tier: third was prioritized first, first stays normal.
    assert _line(session) == ["third", "second", "first"]


def test_a_priority_reservation_joins_behind_earlier_priority_waiters(session, queued) -> None:
    _, waiters = queued
    prioritize_lease(session, waiters["second"].lease_id, reason="earlier")

    late = _suite(session, "late urgent", priority_reason="  urgent  ")

    assert not late.granted
    assert _line(session) == ["second", "late urgent", "first", "third"]
    assert _lease(session, late.lease_id).priority_reason == "urgent"


def test_the_promoted_lease_is_granted_on_the_next_release(session, queued) -> None:
    _, waiters = queued
    prioritize_lease(session, waiters["third"].lease_id, reason="fix push")

    _release_holder(session)

    assert _lease(session, waiters["third"].lease_id).status is DockerLeaseStatus.HELD
    assert _lease(session, waiters["first"].lease_id).status is DockerLeaseStatus.WAITING


def test_a_promoted_lease_that_fits_now_is_granted_at_once(session) -> None:
    """The head does not fit; a small claim behind it would. Promoting it grants
    it in the same call rather than at the waiter's next poll."""
    assert _suite(session, "running").granted
    assert not _suite(session, "head").granted
    small = docker_leases.reserve(
        session,
        holder_label="small fix",
        footprint=DockerFootprint.CUSTOM,
        cpus=2.0,
        memory_mb=1024,
        pool=CapacityPool.HOST,
    )
    assert not small.granted, "head-of-line: it waits behind the head"

    result = prioritize_lease(session, small.lease_id, reason="unblocks the queue")

    assert result.status is DockerLeaseStatus.HELD and result.ahead is None


def test_the_waiter_sees_its_new_place_without_restarting(session, queued) -> None:
    """`capacity_run` and `capacity_line` re-read their lease every poll; what they
    wait behind must shrink to the holder alone once the lease is promoted."""
    running, waiters = queued
    third = _lease(session, waiters["third"].lease_id)
    assert len(leases_ahead(session, third)) == 3

    prioritize_lease(session, third.id, reason="fix push")

    assert set(leases_ahead(session, _lease(session, third.id))) == {running.lease_id}


# ---- never preempts --------------------------------------------------------


def test_a_holder_cannot_be_prioritized_and_is_left_alone(session, queued) -> None:
    running, _ = queued
    before = _lease(session, running.lease_id)
    granted_at, position = before.granted_at, before.position

    with pytest.raises(LeaseNotWaiting):
        prioritize_lease(session, running.lease_id, reason="mine")

    after = _lease(session, running.lease_id)
    assert (after.status, after.granted_at, after.position, after.priority_reason) == (
        DockerLeaseStatus.HELD,
        granted_at,
        position,
        "",
    )


def test_promoting_does_not_take_capacity_from_the_holder(session, queued) -> None:
    running, waiters = queued

    prioritize_lease(session, waiters["first"].lease_id, reason="fix push")

    assert _lease(session, running.lease_id).status is DockerLeaseStatus.HELD
    assert _lease(session, waiters["first"].lease_id).status is DockerLeaseStatus.WAITING


def test_an_ended_lease_cannot_be_prioritized(session, queued) -> None:
    _, waiters = queued
    docker_leases.release_lease(session, waiters["first"].lease_id)

    with pytest.raises(LeaseNotWaiting):
        prioritize_lease(session, waiters["first"].lease_id, reason="too late")


def test_prioritizing_twice_keeps_the_first_place_and_reason(session, queued) -> None:
    _, waiters = queued
    prioritize_lease(session, waiters["third"].lease_id, reason="first reason")
    prioritize_lease(session, waiters["second"].lease_id, reason="later")

    again = prioritize_lease(session, waiters["third"].lease_id, reason="second reason")

    assert not again.moved and again.reason == "first reason"
    assert _line(session) == ["third", "second", "first"]


# ---- the reason ------------------------------------------------------------


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_reason_is_required(session, queued, reason) -> None:
    _, waiters = queued

    with pytest.raises(ValueError, match="reason"):
        prioritize_lease(session, waiters["third"].lease_id, reason=reason)

    assert _line(session) == ["first", "second", "third"]


def test_an_unknown_lease_is_an_error(session) -> None:
    with pytest.raises(UnknownLease):
        prioritize_lease(session, "no-such-lease", reason="x")


def test_the_board_shows_the_priority_and_its_reason(session, queued) -> None:
    _, waiters = queued
    prioritize_lease(session, waiters["second"].lease_id, reason="unblocks 16 suites")

    rows = {row["holder_label"]: row for row in capacity_status(session)["waiting"]}

    assert rows["second"]["priority_reason"] == "unblocks 16 suites"
    assert rows["second"]["prioritized_at"] is not None
    assert rows["second"]["position"] == 1, "the board numbers the line from 1, not by ticket"
    assert rows["first"]["priority_reason"] is None


# ---- the backfill rule with a promoted head --------------------------------


def test_a_standing_claim_may_not_pass_a_promoted_head_it_would_delay(session) -> None:
    """7 cpus, a 4-cpu suite running, a normal 4-cpu waiter, then a 5-cpu urgent
    claim promoted to the head. Protecting the old head would leave 3 cpus for
    standing claims (7 - 4); protecting the promoted one leaves 2 (7 - 5). The
    granter must protect the head it reads now: 8 agents of 0.25 pass, not 12."""
    assert _suite(session, "running").granted
    assert not _suite(session, "normal head").granted
    urgent = docker_leases.reserve(
        session,
        holder_label="urgent",
        footprint=DockerFootprint.CUSTOM,
        cpus=5.0,
        memory_mb=1024,
        pool=CapacityPool.HOST,
    )
    prioritize_lease(session, urgent.lease_id, reason="fix push")
    assert _lease(session, urgent.lease_id).status is DockerLeaseStatus.WAITING

    passed = [
        docker_leases.reserve(
            session,
            holder_label=f"agent {n}",
            footprint=DockerFootprint.CUSTOM,
            cpus=0.25,
            memory_mb=256,
            holder_kind=DockerHolderKind.AGENT_RUN,
            pool=CapacityPool.HOST,
            takes_slot=False,
        )
        for n in range(13)
    ]
    assert [claim.granted for claim in passed] == [True] * 8 + [False] * 5

    _release_holder(session)
    assert _lease(session, urgent.lease_id).status is DockerLeaseStatus.HELD, (
        "the promoted head starts on the release it would have started on"
    )


# ---- approval gating -------------------------------------------------------


def test_promoting_is_a_gated_write_for_agents() -> None:
    tool = McpTool.PRIORITIZE_CAPACITY_LEASE
    assert tool not in AUTO_APPROVED_MCP_TOOLS
    assert not is_auto_approved_mcp_tool(
        f"mcp__loregarden__{tool.value}", {"lease_id": "x", "reason": "y"}
    )
    # Not handed to every stage alongside reserve/renew/release.
    assert tool not in DOCKER_CAPACITY_MCP_TOOLS
    assert tool not in STAGE_DEFAULT_MCP_TOOLS


def test_the_mcp_tool_promotes_and_requires_a_reason(client, isolated_db) -> None:
    with Session(isolated_db) as session:
        _set_ceiling(session, CapacityPool.HOST, cpus=7, memory_mb=48_000, leases=6)
        assert _suite(session, "running").granted
        first = _suite(session, "first")
        second = _suite(session, "second")

    refused = call_mcp(
        client,
        McpTool.PRIORITIZE_CAPACITY_LEASE.value,
        {"lease_id": second.lease_id, "reason": "  "},
    )
    assert refused["result"]["isError"] is True

    ok = call_mcp(
        client,
        McpTool.PRIORITIZE_CAPACITY_LEASE.value,
        {"lease_id": second.lease_id, "reason": "fix push"},
    )
    assert ok["result"].get("isError") is not True
    with Session(isolated_db) as session:
        assert _line(session) == ["second", "first"]
        assert first.lease_id != second.lease_id


def test_the_rest_route_answers_a_holder_with_409(client, isolated_db) -> None:
    with Session(isolated_db) as session:
        _set_ceiling(session, CapacityPool.HOST, cpus=7, memory_mb=48_000, leases=6)
        running = _suite(session, "running")

    response = client.post(
        f"/api/docker/capacity/leases/{running.lease_id}/prioritize", json={"reason": "mine"}
    )

    assert response.status_code == 409


def test_the_ledger_must_be_enabled_for_these_tests() -> None:
    assert settings.docker_capacity_enabled
