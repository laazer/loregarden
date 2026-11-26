"""Putting a waiting capacity lease ahead of the line, for a stated reason.

The line is first come, first served, and that is right almost always. It was
wrong on 2026-11-25, when 18 waiters stood about 4.5 hours deep and the two
fixes that would have made every suite behind them faster were 17th and 18th.
Nothing supported could reorder the line, and a hand-written UPDATE is not a
supported way (lg-machine-resource-2807).

**How the order changes.** A prioritized waiter takes a fresh ticket from the
same counter normal waiters do, and stands `docker_ledger.PRIORITY_BAND` below
it. So every priority waiter is ahead of every normal one, priority waiters
keep the order they were prioritized in, and normal waiters keep theirs. The
key is `position` and nothing else, on purpose: the granter, the wait
estimator, the board, the passing rule and the pre-push wrapper's own count all
already order the line by it, and a second key would be one more thing each of
them could disagree about.

**What it never does.** It never touches a holder: only a WAITING row is moved,
in a compare-and-set, so a lease granted in the same instant is refused rather
than rewritten. The head that `capacity_passing` protects is read from the
line each time, so a promoted head is protected like any other.

**Who may.** A reason is required and kept on the lease, and the board shows
it. The MCP tool is a write that is not auto-approved, so an agent asking goes
to the approval inbox; nobody jumps the line without a name on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import DockerLease, DockerLeaseStatus
from loregarden.models.domain.enums import utcnow
from loregarden.services.docker_leases import UnknownLease, drain_waiters, take_position
from loregarden.services.docker_ledger import priority_position
from sqlalchemy import func, update
from sqlmodel import Session, col, select


class LeaseNotWaiting(ValueError):  # noqa: N818 — reads as the condition, like UnknownLease
    """Only a lease still in line can be put ahead of it."""


@dataclass(frozen=True)
class Prioritized:
    """What prioritizing did: whether it moved the lease, and where it stands now."""

    lease_id: str
    #: False when the lease was already in the priority tier; it kept its place.
    moved: bool
    #: The reason now on the lease (the original one when it was not moved).
    reason: str
    #: WAITING, or HELD when the drain that follows granted it at once.
    status: DockerLeaseStatus
    #: Waiters still ahead of it, 0 at the head; None once it holds capacity.
    ahead: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "moved": self.moved,
            "priority_reason": self.reason,
            "status": self.status.value,
            "ahead": self.ahead,
        }


def prioritize_lease(session: Session, lease_id: str, *, reason: str) -> Prioritized:
    """Move a waiting lease into the priority tier and run a promotion pass.

    Raises `ValueError` for an empty reason, `UnknownLease` for an id nothing
    answers to, and `LeaseNotWaiting` for a lease that holds capacity or has
    ended. Prioritizing a lease already in the tier changes nothing: moving it
    again would send it behind priority waiters that came after it.
    """
    if not reason.strip():
        raise ValueError("a reason is required to put a lease ahead of the line")
    lease = session.get(DockerLease, lease_id)
    if lease is None:
        raise UnknownLease(lease_id)
    if lease.status is not DockerLeaseStatus.WAITING:
        raise LeaseNotWaiting(_not_waiting(lease))
    if lease.priority_reason:
        return _outcome(session, lease, moved=False)

    position = priority_position(take_position(session))
    result = session.exec(
        update(DockerLease)
        .where(col(DockerLease.id) == lease_id)
        .where(col(DockerLease.status) == DockerLeaseStatus.WAITING)
        .values(position=position, priority_reason=reason, prioritized_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if result.rowcount != 1:
        # Granted or ended between the read and the write: nothing was moved.
        session.expire_all()
        lease = session.get(DockerLease, lease_id)
        if lease is None:  # pragma: no cover — leases are never deleted
            raise UnknownLease(lease_id)
        raise LeaseNotWaiting(_not_waiting(lease))

    # It may fit right now; granting here saves the waiter one poll interval.
    drain_waiters(session)
    session.expire_all()
    lease = session.get(DockerLease, lease_id)
    if lease is None:  # pragma: no cover — leases are never deleted
        raise UnknownLease(lease_id)
    return _outcome(session, lease, moved=True)


def _not_waiting(lease: DockerLease) -> str:
    if lease.status is DockerLeaseStatus.RELEASED:
        return f"lease {lease.id} has ended ({lease.end_reason}); there is no place in line to move"
    return (
        f"lease {lease.id} already holds capacity ({lease.status.value}); prioritizing "
        "changes only the line and never preempts a holder"
    )


def _outcome(session: Session, lease: DockerLease, *, moved: bool) -> Prioritized:
    ahead = None
    if lease.status is DockerLeaseStatus.WAITING:
        ahead = session.exec(
            select(func.count())
            .select_from(DockerLease)
            .where(
                DockerLease.status == DockerLeaseStatus.WAITING,
                col(DockerLease.position) < lease.position,
            )
        ).one()
    return Prioritized(
        lease_id=lease.id,
        moved=moved,
        reason=lease.priority_reason,
        status=lease.status,
        ahead=ahead,
    )
