"""Where a run log's lines live, and how to read them back.

Lines used to be a JSON array inside `artifacts.content_json`, rewritten in
full on every flush. They are now rows in `run_log_lines`, appended
(lg-workflow-integrity-687). Both shapes are readable: artifacts written before
the change carry no `storage` marker and keep their inline `lines`, so historical
runs render without a backfill.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import RunLogLine
from pydantic import BaseModel, Field
from sqlmodel import Session, select

#: Marks a log artifact whose lines are rows in `run_log_lines`.
LOG_STORAGE_ROWS = "rows"

#: The tail a reader is given, matching `RunLogStreamer.MAX_LINES` — the window
#: the streamer itself keeps, so both surfaces agree on how much log exists.
MAX_READ_LINES = 1000


class LogLineBody(BaseModel):
    """One rendered log line, as a reader consumes it."""

    time: str = ""
    tag: str = ""
    text: str = ""


class LogArtifactBody(BaseModel):
    """The parsed `content_json` of a `log` artifact.

    Modelled rather than duck-typed because the value comes off a TEXT column
    written by past versions of this code: `lines` is a JSON array on pre-687
    rows and absent on later ones, and validating at the boundary is what keeps
    that difference from becoming a hand-rolled type check at every reader.
    """

    lines: list[LogLineBody] = Field(default_factory=list)
    live: str | None = None
    storage: str = ""


def stored_as_rows(body: dict[str, Any]) -> bool:
    """Whether this log artifact's lines live in `run_log_lines`."""
    return LogArtifactBody.model_validate(body).storage == LOG_STORAGE_ROWS


def read_log_lines(session: Session, run_id: str, body: dict[str, Any]) -> list[dict[str, str]]:
    """The rendered lines for a run, from whichever store holds them.

    Returns the last `MAX_READ_LINES` in run order.
    """
    parsed = LogArtifactBody.model_validate(body)
    if parsed.storage != LOG_STORAGE_ROWS:
        return [line.model_dump() for line in parsed.lines]
    if not run_id:
        return []
    rows = session.exec(
        select(RunLogLine)
        .where(RunLogLine.run_id == run_id)
        .order_by(RunLogLine.seq.desc())
        .limit(MAX_READ_LINES)
    ).all()
    return [{"time": r.time, "tag": r.tag, "text": r.text} for r in reversed(rows)]
