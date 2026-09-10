"""The endpoint the board's Docker tab reads.

Its own route rather than a block on the queue-status payload, and the test that
matters most pins why: the queue socket pushes that payload to every open tab on
a tick, and this read walks the ledger and can shell out. Riding it would spend
that on every poll for every viewer, including those who never open the tab.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import DockerCeilingSource, DockerFootprint
from loregarden.services import docker_leases
from sqlmodel import Session


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
    assert waiter["position"] == queued.position
    assert "estimated_wait_seconds" in waiter
    assert waiter["estimate_basis"] in {"history", "ttl_bound", "unknown"}
    assert "last_probe_outcome" in waiter


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
