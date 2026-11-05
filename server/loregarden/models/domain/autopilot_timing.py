"""When an autopilot runs on its own: stop conditions and a start schedule.

Two halves, both stored on `InitiativePlan` as JSON:

- **Stops** (`AutopilotStop`) end a run that is on. Each kind answers
  `reached(ctx)` with why, or None, so the engine never switches on kind; a
  spend-based stop (tokens, usage change, usage-cap percent) is a new model in
  the union with its own `reached`, and `StopContext` grows what it needs.
- **Schedule** (`AutopilotSchedule`) turns a run on: once at `start_at`, and/or
  every time a weekly `window` opens. A window it opened it also closes.

Pure: no session, no clock of its own. `services.autopilot_timing` applies them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loregarden.models.domain.plan_enums import AutopilotStopKind
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

#: The longest single run a duration may ask for: a week.
MAX_RUN_MINUTES = 7 * 24 * 60

_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class StopContext:
    """What a stop condition may read. Both instants are aware UTC."""

    now: datetime
    #: When the autopilot was last turned on; None if it never recorded one.
    since: datetime | None


class DeadlineStop(BaseModel):
    """Stop at a fixed instant ("run until 07:00")."""

    kind: Literal[AutopilotStopKind.DEADLINE] = AutopilotStopKind.DEADLINE
    at: AwareDatetime

    #: Spent once it fires: a past deadline would stop every later run at once.
    one_shot: ClassVar[bool] = True

    def reached(self, ctx: StopContext) -> str | None:
        if ctx.now < self.at:
            return None
        return f"reached its stop time ({self.at.astimezone(UTC):%Y-%m-%d %H:%M} UTC)"

    def stops_at(self, _since: datetime | None) -> datetime | None:
        return self.at


class RunForStop(BaseModel):
    """Stop once it has been on this long, counted from each switch-on."""

    kind: Literal[AutopilotStopKind.RUN_FOR] = AutopilotStopKind.RUN_FOR
    minutes: int = Field(ge=1, le=MAX_RUN_MINUTES)

    one_shot: ClassVar[bool] = False

    def reached(self, ctx: StopContext) -> str | None:
        end = self.stops_at(ctx.since)
        if end is None or ctx.now < end:
            return None
        return f"ran for its {_duration(self.minutes)}"

    def stops_at(self, since: datetime | None) -> datetime | None:
        return None if since is None else since + timedelta(minutes=self.minutes)


AutopilotStop = Annotated[DeadlineStop | RunForStop, Field(discriminator="kind")]


class AutopilotWindow(BaseModel):
    """A weekly window it runs in: on when it opens, off when it closes.

    `days` are the weekdays a window *opens* on (0 = Monday). An `end` at or
    before `start` closes the next day, so 22:00-06:00 runs overnight. Times
    are wall-clock in `timezone`, so the window keeps its hours across DST.
    """

    days: list[int] = Field(min_length=1, max_length=7)
    start: time
    end: time
    timezone: str

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(days))

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {name!r}") from exc
        return name

    @model_validator(mode="after")
    def _has_length(self) -> AutopilotWindow:
        if self.start == self.end:
            raise ValueError("a window needs different start and end times")
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("window times are wall-clock in `timezone`; drop the offset")
        return self

    def _span(self, day: datetime) -> tuple[datetime, datetime]:
        """The occurrence opening on `day`'s date, as aware UTC instants."""
        zone = ZoneInfo(self.timezone)
        opens = datetime.combine(day.date(), self.start, tzinfo=zone)
        closes_on = day.date() + timedelta(days=1 if self.end <= self.start else 0)
        closes = datetime.combine(closes_on, self.end, tzinfo=zone)
        return opens.astimezone(UTC), closes.astimezone(UTC)

    def occurrence_at(self, now: datetime) -> tuple[datetime, datetime] | None:
        """The occurrence `now` falls in, or None between occurrences."""
        local = now.astimezone(ZoneInfo(self.timezone))
        # Only today's or yesterday's opening can still be open: a window is
        # shorter than a day.
        for back in (0, 1):
            day = local - timedelta(days=back)
            if day.weekday() not in self.days:
                continue
            opens, closes = self._span(day)
            if opens <= now < closes:
                return opens, closes
        return None

    def next_open(self, now: datetime) -> datetime:
        """The first opening strictly after `now`."""
        local = now.astimezone(ZoneInfo(self.timezone))
        for ahead in range(8):
            day = local + timedelta(days=ahead)
            if day.weekday() in self.days:
                opens, _ = self._span(day)
                if opens > now:
                    return opens
        raise AssertionError("a non-empty weekly window opens within eight days")

    def describe(self) -> str:
        return f"{_days(self.days)} {self.start:%H:%M}-{self.end:%H:%M} ({self.timezone})"


class AutopilotSchedule(BaseModel):
    """When it turns itself on. Empty (both None) is no schedule."""

    #: Turn on once at this instant; cleared once it has.
    start_at: AwareDatetime | None = None
    window: AutopilotWindow | None = None

    @property
    def empty(self) -> bool:
        return self.start_at is None and self.window is None

    def next_start(self, now: datetime) -> datetime | None:
        """The next instant it would turn itself on, or None if never."""
        candidates = [self.start_at] if self.start_at is not None else []
        if self.window is not None:
            candidates.append(self.window.next_open(now))
        return min(candidates) if candidates else None


def _duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h {rest}m"
    return f"{hours}h" if hours else f"{rest}m"


def _days(days: list[int]) -> str:
    if days == list(range(7)):
        return "every day"
    if days == list(range(5)):
        return "Mon-Fri"
    return ", ".join(_DAY_NAMES[d] for d in days)


__all__ = [
    "MAX_RUN_MINUTES",
    "AutopilotSchedule",
    "AutopilotStop",
    "AutopilotWindow",
    "DeadlineStop",
    "RunForStop",
    "StopContext",
]
