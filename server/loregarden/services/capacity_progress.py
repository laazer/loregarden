"""How far a command held under a capacity lease has got.

A push spends most of its life in two places nobody could see into: in line,
and inside a test suite. The line printed one sentence and then nothing until
it moved; the suite printed dots. An agent's buffered output, a terminal and
the queue board each showed a different, partial picture of the same run.

The lease is the record all three already read, so progress goes on the lease.
The held command reports it by rewriting one small JSON file, named to it in
`PROGRESS_ENV`:

    {"step": "pytest (5/5)", "done": 412, "total": 1830}

Writing a file costs a shell script or a test-runner plugin nothing — no CLI
start-up per update, no database handle in the child — and the process holding
the lease, which already has both, copies it onto the row every couple of
seconds (`ProgressSync`). Writers replace the file by rename, so a reader never
sees half of one.

A command that never writes the file is simply a lease with no progress: the
board then shows how long it has held, as it always did.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from loregarden.models.domain import DockerLease
from loregarden.models.domain.enums import utcnow
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

#: The file the held command rewrites with its progress. Set only by a holder
#: that copies it onto the lease; a command run some other way never sees it,
#: and its reporters stay quiet.
PROGRESS_ENV = "LOREGARDEN_PROGRESS_FILE"

#: How often the holder looks at the file. A step lasts seconds to minutes and
#: the board polls every few seconds; faster buys nothing but writes.
SYNC_INTERVAL_SECONDS = 2.0

#: Longest step text kept. A label, not a log line.
_STEP_MAX = 200

Report = Callable[[str], None]
SessionFactory = Callable[[], Session]


class LeaseProgress(BaseModel):
    """One report from the held command. Unknown keys are ignored, so a newer
    writer does not break an older holder."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    step: str = Field(min_length=1)
    done: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)


def read_progress(path: Path) -> LeaseProgress | None:
    """The command's last report, or None when it has not written one yet.

    A file that is there but unreadable as a report raises `ValidationError`:
    that is a writer bug, and the caller says so rather than showing nothing.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:  # silent-ok: no report yet is the normal start of every run
        return None
    return LeaseProgress.model_validate_json(raw)


def record_progress(session: Session, lease_id: str, progress: LeaseProgress) -> bool:
    """Copy a report onto the lease. False when the lease is gone."""
    lease = session.get(DockerLease, lease_id)
    if lease is None:
        return False
    lease.progress_step = progress.step[:_STEP_MAX]
    lease.progress_done = progress.done
    lease.progress_total = progress.total
    lease.progress_at = utcnow()
    session.add(lease)
    session.commit()
    return True


def describe_progress(step: str, done: int | None, total: int | None) -> str:
    """ "pytest (5/5) · 412/1830 (22%)" — the step, then the count when there is one."""
    if not step:
        return ""
    if done is None:
        return step
    if total:
        return f"{step} · {done}/{total} ({min(100, done * 100 // total)}%)"
    return f"{step} · {done} done"


def format_seconds(seconds: float) -> str:
    """ "45s", "4m05s", "1h12m" — short enough for one status line."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class ProgressSync:
    """Copies the progress file onto the lease from a thread, while the command runs.

    Syncs once more on exit, so the last step the command reached — the one
    that failed, usually — is what the lease keeps.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        lease_id: str,
        path: Path,
        report: Report,
        *,
        interval: float = SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._lease_id = lease_id
        self._path = path
        self._report = report
        self._interval = interval
        self._seen: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="capacity-progress", daemon=True)

    def __enter__(self) -> ProgressSync:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 5)
        self.sync()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.sync()

    def sync(self) -> None:
        """Copy the file onto the lease if it changed since the last copy."""
        try:
            stamp = self._path.stat().st_mtime_ns
        except FileNotFoundError:  # silent-ok: nothing reported yet; the next tick looks again
            return
        if stamp == self._seen:
            return
        try:
            progress = read_progress(self._path)
        except ValidationError as exc:
            self._seen = stamp  # say it once per bad write, not every tick
            self._report(f"capacity: ignoring a malformed progress report in {self._path}: {exc}")
            return
        if progress is None:
            return
        try:
            with self._session_factory() as session:
                record_progress(session, self._lease_id, progress)
        except SQLAlchemyError as exc:
            # Transient (a locked database) is the common case: `_seen` is left
            # alone, so the next tick copies the same report again.
            self._report(f"capacity: could not record progress on {self._lease_id}: {exc}")
            return
        self._seen = stamp
