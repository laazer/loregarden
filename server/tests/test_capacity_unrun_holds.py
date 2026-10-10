"""The migration that stops instant pre-push holds teaching the wait estimator.

Narrow on purpose: only a host pre-push hold too short to have run anything is
re-tagged. A real hold, a short hold of something else, and a lease that ended
some other way are history, and must stay history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loregarden.db.versions.capacity_unrun_holds import m_capacity_unrun_holds
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from sqlmodel import Session

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _hold(
    session: Session,
    label: str,
    *,
    held_seconds: float,
    pool: CapacityPool = CapacityPool.HOST,
    end_reason: DockerLeaseEndReason = DockerLeaseEndReason.RELEASED,
) -> str:
    lease = DockerLease(
        status=DockerLeaseStatus.RELEASED,
        holder_label=label,
        pool=pool,
        footprint=DockerFootprint.HEAVY,
        cpus=4.0,
        memory_mb=8192,
        end_reason=end_reason,
        granted_at=NOW - timedelta(seconds=held_seconds),
        released_at=NOW,
    )
    session.add(lease)
    session.commit()
    return lease.id


def test_only_instant_host_pre_push_holds_are_retagged(isolated_db) -> None:
    with Session(isolated_db) as session:
        instant = _hold(session, "pre-push client-tests · wt@claude/b · pid 1", held_seconds=0.2)
        real = _hold(session, "pre-push server-tests · wt@claude/b · pid 2", held_seconds=1500)
        other = _hold(session, "loregarden: quick check · pid 3", held_seconds=0.5)
        docker = _hold(session, "pre-push stack", held_seconds=0.5, pool=CapacityPool.DOCKER)
        expired = _hold(
            session,
            "pre-push client-tests · pid 4",
            held_seconds=0.5,
            end_reason=DockerLeaseEndReason.TTL_EXPIRED,
        )

    with isolated_db.begin() as conn:
        m_capacity_unrun_holds(conn)
        m_capacity_unrun_holds(conn)  # re-running is a no-op

    with Session(isolated_db) as session:
        reasons = {
            lease_id: session.get(DockerLease, lease_id).end_reason
            for lease_id in (instant, real, other, docker, expired)
        }
    assert reasons == {
        instant: DockerLeaseEndReason.COMMAND_NOT_RUN,
        real: DockerLeaseEndReason.RELEASED,
        other: DockerLeaseEndReason.RELEASED,
        docker: DockerLeaseEndReason.RELEASED,
        expired: DockerLeaseEndReason.TTL_EXPIRED,
    }
