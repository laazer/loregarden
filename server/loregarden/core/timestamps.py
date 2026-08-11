"""Serialize stored datetimes so a browser can place them on a timeline.

Every timestamp in this control plane is written as UTC-aware
(``datetime.now(timezone.utc)``), but the columns are plain ``DateTime`` on
SQLite, which drops the zone on write and hands back a *naive* value on read.
``naive.isoformat()`` therefore emits ``2026-08-08T14:19:57.465660`` — no ``Z``,
no offset — and ECMAScript parses an offset-less date-time as **local** time.
The UI was showing a UTC instant as if it were the viewer's wall clock, off by
their whole UTC offset, which is precisely the error that makes "which run
failed, and when?" unanswerable.

So the zone is re-attached at the serialization boundary, where the value is
known to be UTC, rather than guessed at by each reader.
"""

from __future__ import annotations

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as UTC-aware, tagging a naive value as the UTC it is."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset, or ``None`` — safe for JS ``Date``."""
    if value is None:
        return None
    return as_utc(value).isoformat()
