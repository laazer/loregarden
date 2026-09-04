"""What the workflow monitor reports, and what it will not."""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain.enums import MonitorCondition
from sqlmodel import Field, SQLModel


class MonitorFinding(SQLModel):
    """One thing the monitor noticed, with the rows that evidence it.

    `evidence` carries the numbers a person needs to judge the finding without
    re-running the query — the observed value, the baseline it was compared
    against, and the ids involved. A finding a reader has to go and verify by
    hand is a finding they will ignore.

    Note what is absent: no severity, no suggested action, no verdict. Whether a
    thrashing stage is a converging rework loop or a stuck one is
    `rework_feedback`'s ledger to say, and it holds the retry cap. The monitor
    reports the signal and stops.
    """

    condition: MonitorCondition
    ticket_id: str = ""
    stage_key: str = ""
    summary: str
    evidence: dict[str, str] = Field(default_factory=dict)

    #: Stable within a scan: the artifact key this finding upserts into.
    def dedupe_key(self) -> str:
        return f"{self.condition.value}:{self.ticket_id}:{self.stage_key}"


class MonitorFindingView(SQLModel):
    """A persisted finding, as the API returns it."""

    condition: MonitorCondition
    ticket_id: str = ""
    stage_key: str = ""
    summary: str
    evidence: dict[str, str] = Field(default_factory=dict)
    occurrences: int = 1
    first_seen: datetime | None = None
    last_seen: datetime | None = None
