"""Park a stage until a provider's usage limit resets, instead of waiting for a person.

A run killed by a usage limit says nothing about the work, and no agent turn can
clear it, so it blocks without a repair (`run_completion._block_for_usage_limit`).
It then sat blocked until somebody noticed the window had passed and requeued it:
15 such escalations over the 30 days to 2026-10-14, every one of them carrying
the provider's own reset time, which `detect_usage_limit` already resolves into
`UsageLimit.reset_at`.

So when the reset time is known, the stage is parked behind a `USAGE_LIMIT_WAIT`
marker holding it, and the reconcile pass resumes the stage once it has passed
(`parked_stage_resume`). When the provider named no reset time there is nothing
to wait for, and the block stays with a person, as before.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from loregarden.models.domain import Artifact, StageBudgetArtifactKind
from loregarden.services.usage_limits import UsageLimit
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_KIND = StageBudgetArtifactKind.USAGE_LIMIT_WAIT


class UsageLimitWait(BaseModel):
    """What a marker records: the stage, the run the limit killed, when it lifts."""

    stage_key: str
    run_id: str
    reset_at: datetime


def usage_limit_wait_title(stage_key: str) -> str:
    return f"stage-usage-limit-wait:{stage_key}"


def resume_note(stage_key: str, limit: UsageLimit) -> str:
    """The settlement's reason, in the operator's words."""
    when = limit.reset_text or (limit.reset_at.isoformat() if limit.reset_at else "")
    return (
        f"The block on '{stage_key}' is a provider usage limit; the stage resumes "
        f"automatically once it resets ({when}), so no repair turn and no person is needed."
    )


def record_usage_limit_wait(
    session: Session, ticket_id: str, stage_key: str, *, run_id: str, reset_at: datetime
) -> None:
    """Park `stage_key` until `reset_at`. One marker per (ticket, stage): a later
    limit on the same stage replaces the earlier wait rather than adding another."""
    for stale in _markers_for(session, ticket_id, stage_key):
        session.delete(stale)
    wait = UsageLimitWait(stage_key=stage_key, run_id=run_id, reset_at=reset_at)
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_KIND,
            title=usage_limit_wait_title(stage_key),
            content_json=wait.model_dump_json(),
        )
    )
    session.commit()


def _markers_for(session: Session, ticket_id: str, stage_key: str) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                Artifact.kind == _KIND,
                Artifact.ticket_id == ticket_id,
                Artifact.title == usage_limit_wait_title(stage_key),
            )
        ).all()
    )


def pending_usage_limit_waits(session: Session) -> list[Artifact]:
    return list(session.exec(select(Artifact).where(Artifact.kind == _KIND)).all())


def read_wait(marker: Artifact) -> UsageLimitWait | None:
    """The marker's contents, or None when the row is unreadable (and so unresumable)."""
    try:
        return UsageLimitWait.model_validate_json(marker.content_json or "{}")
    except ValidationError:
        logger.warning(
            "Usage-limit wait %s on ticket %s is unreadable; leaving the stage blocked for a "
            "person",
            marker.id,
            marker.ticket_id,
            exc_info=True,
        )
        return None


def has_reset(wait: UsageLimitWait, *, now: datetime) -> bool:
    reset = wait.reset_at if wait.reset_at.tzinfo else wait.reset_at.replace(tzinfo=timezone.utc)
    return reset <= now


__all__ = [
    "UsageLimitWait",
    "has_reset",
    "pending_usage_limit_waits",
    "read_wait",
    "record_usage_limit_wait",
    "resume_note",
    "usage_limit_wait_title",
]
