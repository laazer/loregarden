"""The endpoint the board's Machine tab reads.

Its own route rather than a block on the queue-status payload, and the test that
matters most pins why: the queue socket pushes that payload to every open tab on
a tick, and this read walks the ledger and can shell out. Riding it would spend
that on every poll for every viewer, including those who never open the tab.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace


@pytest.fixture(name="ceiling")
def ceiling_fixture(isolated_db):
    with Session(isolated_db) as session:
        pool = docker_leases.load_pool(session)
        pool.ceiling_cpus = 4.0
        pool.ceiling_memory_mb = 4096
        pool.ceiling_leases = 2
        pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
        session.add(pool)
        session.commit()


def test_it_reports_the_ceiling_and_what_is_free(client, ceiling) -> None:
    payload = client.get("/api/docker/capacity").json()

    assert payload["enabled"] is True
    assert payload["ceiling"]["cpus"] == 4.0
    assert payload["ceiling"]["source"] == DockerCeilingSource.CONFIG_OVERRIDE.value
    assert payload["available"] == {"cpus": 4.0, "memory_mb": 4096, "leases": 2}
    assert payload["holders"] == []
    assert payload["waiting"] == []


def test_a_holder_and_a_waiter_carry_what_the_rail_renders(client, isolated_db, ceiling) -> None:
    """Every field the tab draws has to survive the round trip.

    The rail decides what to say from `estimate_basis` and `last_probe_outcome`
    as much as from the numbers; a payload that dropped either would render a
    bound as a forecast, or an unverified lease as a healthy one.
    """
    with Session(isolated_db) as session:
        held = docker_leases.reserve(
            session, holder_label="e2e suite", footprint=DockerFootprint.STACK
        )
        assert held.granted
        held.bind(compose_project="myapp", container_names=["myapp-db-1"])
        queued = docker_leases.reserve(
            session, holder_label="second", footprint=DockerFootprint.STACK
        )
        assert queued.state.value == "queued"

    payload = client.get("/api/docker/capacity").json()

    holder = payload["holders"][0]
    assert holder["holder_label"] == "e2e suite"
    assert holder["compose_project"] == "myapp"
    assert holder["container_names"] == ["myapp-db-1"]
    assert holder["expires_in_seconds"] is not None

    waiter = payload["waiting"][0]
    assert waiter["position"] == 1
    assert "estimated_wait_seconds" in waiter
    assert waiter["estimate_basis"] in {"history", "ttl_bound", "unknown"}
    assert "last_probe_outcome" in waiter


def test_the_line_is_numbered_by_place_and_the_head_says_what_it_lacks(
    client, isolated_db, ceiling
) -> None:
    """`lease.position` is a ticket from a counter that never resets: the board
    once numbered a line of seven 72 to 78, and its head said "≈ 0s" with no
    word on why it was not running."""
    with Session(isolated_db) as session:
        pool = docker_leases.load_pool(session)
        pool.next_position = 72
        session.add(pool)
        session.commit()
        held = docker_leases.reserve(
            session,
            holder_label="suite",
            footprint=DockerFootprint.CUSTOM,
            cpus=3.0,
            memory_mb=1024,
        )
        assert held.granted
        first = docker_leases.reserve(
            session,
            holder_label="first",
            footprint=DockerFootprint.CUSTOM,
            cpus=2.0,
            memory_mb=1024,
        )
        second = docker_leases.reserve(
            session,
            holder_label="second",
            footprint=DockerFootprint.CUSTOM,
            cpus=1.0,
            memory_mb=1024,
        )
        assert first.position >= 72 and second.state.value == "queued"

    payload = client.get("/api/docker/capacity").json()

    assert [row["position"] for row in payload["waiting"]] == [1, 2]
    docker_gaps = [gap for gap in payload["head_shortfall"] if gap["pool"] == "docker"]
    assert docker_gaps == [{"pool": "docker", "resource": "cpus", "needed": 2.0, "free": 1.0}]


def test_with_nobody_waiting_there_is_no_head_to_explain(client, ceiling) -> None:
    assert client.get("/api/docker/capacity").json()["head_shortfall"] is None


def test_an_unmeasured_ceiling_is_reported_as_unknown_not_as_zero_capacity(
    client, isolated_db
) -> None:
    """A ceiling of zero and a ceiling nobody established are different claims,
    and the tab draws them differently — hatched and captioned, rather than an
    empty bar that reads as an idle machine."""
    payload = client.get("/api/docker/capacity").json()
    assert payload["ceiling"]["source"] in {"unknown", "probe", "stale_probe"}
    if payload["ceiling"]["source"] == "unknown":
        assert payload["ceiling"]["cpus"] == 0
        assert payload["ceiling"]["probed_at"] is None


def _hold_and_queue(isolated_db) -> tuple[str, str]:
    """A holder filling the ceiling's cpus, and one waiter behind it."""
    with Session(isolated_db) as session:
        held = docker_leases.reserve(
            session,
            holder_label="pre-push client-tests · lg-x-e33a13@claude/lg-x-e33a13 · pid 22339",
            footprint=DockerFootprint.CUSTOM,
            cpus=4.0,
            memory_mb=1024,
        )
        queued = docker_leases.reserve(
            session,
            holder_label="second",
            footprint=DockerFootprint.CUSTOM,
            cpus=1.0,
            memory_mb=512,
        )
        assert held.granted and queued.state.value == "queued"
        return held.lease_id, queued.lease_id


def test_the_label_arrives_split_into_what_where_and_pid(client, isolated_db, ceiling) -> None:
    _hold_and_queue(isolated_db)

    holder = client.get("/api/docker/capacity").json()["holders"][0]

    assert holder["holder"] == {
        "what": "pre-push client-tests",
        "branch": "claude/lg-x-e33a13",
        "worktree": None,
        "pid": 22339,
    }


def test_a_waiter_says_how_long_it_has_waited_and_when_it_stopped_asking(
    client, isolated_db, ceiling
) -> None:
    """A stuck waiter and one that just arrived differ in whether they still
    poll, not only in age — so both are on the row, and the holder carries
    neither."""
    _, waiter_id = _hold_and_queue(isolated_db)
    fresh = client.get("/api/docker/capacity").json()
    assert fresh["waiting"][0]["poll_stalled"] is False
    assert fresh["waiting"][0]["waiting_seconds"] >= 0
    assert fresh["holders"][0]["waiting_seconds"] is None

    with Session(isolated_db) as session:
        lease = session.get(DockerLease, waiter_id)
        long_ago = datetime.now(timezone.utc) - timedelta(minutes=6)
        lease.requested_at = long_ago
        lease.last_polled_at = long_ago
        session.add(lease)
        session.commit()

    waiter = client.get("/api/docker/capacity").json()["waiting"][0]
    assert waiter["poll_stalled"] is True
    assert waiter["waiting_seconds"] >= 360
    assert waiter["last_seen_seconds_ago"] >= 360
    # Dropped by the abandonment sweep at ten minutes unseen, so about four left.
    assert 0 < waiter["drops_in_seconds"] <= 240


def test_a_lease_naming_only_its_run_links_to_the_runs_ticket(client, isolated_db, ceiling) -> None:
    with Session(isolated_db) as session:
        workspace = make_workspace(session)
        run = make_agent_run(session, workspace_id=workspace.id, ticket_id="ticket-for-run")
        held = docker_leases.reserve(
            session,
            holder_label="stage",
            footprint=DockerFootprint.STACK,
            agent_run_id=run.id,
        )
        assert held.granted

    holder = client.get("/api/docker/capacity").json()["holders"][0]
    assert holder["ticket_id"] == "ticket-for-run"


def test_releasing_a_holder_frees_it_and_a_second_release_is_not_an_error(
    client, isolated_db, ceiling
) -> None:
    holder_id, _ = _hold_and_queue(isolated_db)

    first = client.post(
        f"/api/docker/capacity/leases/{holder_id}/release", json={"reason": "stuck push"}
    )
    again = client.post(
        f"/api/docker/capacity/leases/{holder_id}/release", json={"reason": "stuck push"}
    )

    assert first.status_code == 200 and first.json()["released"] is True
    assert again.status_code == 200 and again.json()["released"] is False
    with Session(isolated_db) as session:
        lease = session.get(DockerLease, holder_id)
        assert lease.end_reason is DockerLeaseEndReason.FORCE_RELEASED
        assert lease.note == "force-released: stuck push"
    # The waiter behind it fits now, and is promoted by the release's drain.
    board = client.get("/api/docker/capacity").json()
    assert board["waiting"] == []
    assert [row["holder_label"] for row in board["holders"]] == ["second"]


def test_dropping_a_waiter_takes_it_out_of_the_line(client, isolated_db, ceiling) -> None:
    _, waiter_id = _hold_and_queue(isolated_db)

    response = client.post(
        f"/api/docker/capacity/leases/{waiter_id}/release", json={"reason": "gone"}
    )

    assert response.json()["released"] is True
    with Session(isolated_db) as session:
        assert session.get(DockerLease, waiter_id).status is DockerLeaseStatus.RELEASED
    assert client.get("/api/docker/capacity").json()["waiting"] == []


def test_release_names_an_unknown_lease_and_requires_a_reason(client, ceiling) -> None:
    missing = client.post("/api/docker/capacity/leases/nope/release", json={"reason": "x"})
    assert missing.status_code == 404
    blank = client.post("/api/docker/capacity/leases/nope/release", json={"reason": ""})
    assert blank.status_code == 422
