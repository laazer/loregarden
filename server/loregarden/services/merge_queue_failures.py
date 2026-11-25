"""What a failed required check says about the change, before the queue ejects it.

Two failures read alike on the board and are not alike. A suite that ran and
failed is evidence about the staged commit. A job whose runner died is not: it
reached no verdict, and #946 was ejected on one (2026-11-23, "The hosted runner
lost communication with the server", 61 minutes in, no test result and no log)
then landed with the same tree. So a check that produced no verdict is re-run
once, on the same staged commit, and said so; a check that failed still ejects.

Nothing is judged while the failed check's run is still going. GitHub gives out
no log until the run ends (five ejections carry "could not read its log: run N
is still in progress" and nothing else) and refuses a re-run of it.

A job that only gathers others' results (`GATHER_STEP`) fails whenever one of
them does, so it says nothing of its own: the jobs it gathers are judged.

A test failure is never re-run here. What it gets is context: the tests in its
log that also failed on other pull requests, which is where to look first.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from loregarden.models.domain import MergeQueueEntry, MergeQueueState, comparable_utc, utcnow
from pydantic import BaseModel
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How far back another pull request's failure still says something about a test.
ELSEWHERE_WINDOW = timedelta(days=14)

#: Tests named in a pytest summary, on the log lines `gh run view --log-failed` prints.
_FAILED_TEST = re.compile(r"\b(?:FAILED|ERROR) (tests/\S+::.+?)(?= - |\s*$)", re.MULTILINE)

#: How many tests a stopped entry names before it counts the rest.
_NAMED_TESTS = 5


#: The step of a job that only gathers other jobs' results, in our workflows.
GATHER_STEP = "All required jobs passed"


class JobStep(BaseModel):
    name: str
    status: str
    #: Null until the step ends.
    conclusion: str | None = None


class ActionsJob(BaseModel):
    """One GitHub Actions job, as far as judging its failure needs it."""

    run_id: int
    run_attempt: int
    steps: list[JobStep]

    @property
    def died(self) -> bool:
        """Whether the job ended with a step still open: nothing reported its end.

        A step that fails, is skipped or is cancelled is COMPLETED either way,
        so an open one in a finished job means the runner went away under it.
        No steps at all is a job that never reached a runner's first step.
        """
        return not self.steps or any(
            step.status != "completed"  # py-org: allow-string
            for step in self.steps
        )

    @property
    def gathers(self) -> bool:
        """Whether all that failed here is the step reporting other jobs' results."""
        failed = {
            step.name
            for step in self.steps
            if step.conclusion == "failure"  # py-org: allow-string
        }
        return failed == {GATHER_STEP}


class ActionsRun(BaseModel):
    """One GitHub Actions run: which attempt it is on, and whether that has ended."""

    run_attempt: int
    status: str

    @property
    def finished(self) -> bool:
        return self.status == "completed"  # py-org: allow-string


class JobReader(Protocol):
    def failed_job(self, url: str) -> ActionsJob | None: ...

    def actions_run(self, run_id: int) -> ActionsRun: ...

    def rerun_failed_jobs(self, run_id: int) -> str: ...


class LostVerdict(StrEnum):
    #: The run a failing check belongs to is still going: not judged yet.
    UNFINISHED = "unfinished"
    #: At least one failing check reached a verdict: the change is judged on it.
    JUDGED = "judged"
    #: Every failing check died, and they are running again.
    RERUNNING = "rerunning"
    #: Every failing check died, and they may not or could not be run again.
    LOST = "lost"


class LostChecks(BaseModel):
    verdict: LostVerdict
    detail: str = ""
    #: Whether this reading asked for the re-run, as against finding it asked for.
    asked: bool = False


_JUDGED = LostChecks(verdict=LostVerdict.JUDGED)


def rerun_lost_checks(github: JobReader, failing: dict[str, str]) -> LostChecks:
    """Re-run `failing` (check name to its URL) once, if none of them reached a verdict.

    Judged only when the runs they belong to have ended.

    Once per run is read from GitHub rather than counted here: the run's own
    attempt number survives a restart and counts a person's re-run too. A job
    from an older attempt than its run's is one whose re-run has been asked for
    and has not reported yet.

    Raises `GitHubReadError` when GitHub cannot be asked.
    """
    checks = {name: github.failed_job(url) for name, url in failing.items()}
    found = {name: job for name, job in checks.items() if job is not None}
    runs = {
        run_id: github.actions_run(run_id)
        for run_id in sorted({job.run_id for job in found.values()})
    }
    unfinished = sorted(
        name
        for name, job in found.items()
        if runs[job.run_id].run_attempt <= job.run_attempt and not runs[job.run_id].finished
    )
    if unfinished:
        return LostChecks(
            verdict=LostVerdict.UNFINISHED,
            detail=f"{', '.join(unfinished)} failed; waiting for the rest of the run to end, "
            "so its log can be read",
        )
    jobs = {name: job for name, job in found.items() if not job.gathers}
    if not jobs or len(found) < len(checks) or not all(job.died for job in jobs.values()):
        return _JUDGED
    names = ", ".join(sorted(jobs))
    died = f"{names}: the runner died with no result"
    asked = False
    for run_id, attempt in sorted({(job.run_id, job.run_attempt) for job in jobs.values()}):
        if runs[run_id].run_attempt > attempt:
            continue
        if attempt > 1:
            return LostChecks(
                verdict=LostVerdict.LOST,
                detail=f"{died}, on its re-run as well (attempt {attempt})",
            )
        refused = github.rerun_failed_jobs(run_id)
        if refused:
            return LostChecks(
                verdict=LostVerdict.LOST, detail=f"{died}, and it could not be re-run: {refused}"
            )
        asked = True
    return LostChecks(
        verdict=LostVerdict.RERUNNING,
        detail=f"{died}; running again on the same commit, once",
        asked=asked,
    )


def failed_tests(log: str) -> set[str]:
    return {match.strip() for match in _FAILED_TEST.findall(log)}


def failed_elsewhere(session: Session, entry: MergeQueueEntry, log: str) -> str:
    """The tests in `log` that also failed other pull requests lately; "" for none.

    Context for whoever reads the stop, not a verdict: a test can fail two
    changes for two real reasons.
    """
    failed = failed_tests(log)
    if not failed:
        return ""
    since = utcnow() - ELSEWHERE_WINDOW
    others = session.exec(
        select(MergeQueueEntry)
        .where(MergeQueueEntry.workspace_id == entry.workspace_id)
        .where(MergeQueueEntry.state == MergeQueueState.FAILED)
        .where(MergeQueueEntry.pr_number != entry.pr_number)
        .where(MergeQueueEntry.log_tail != "")
        .order_by(col(MergeQueueEntry.enqueued_at))
    ).all()
    seen: dict[str, list[str]] = {}
    for other in others:
        if comparable_utc(other.state_changed_at) < since:
            continue
        for test in failed & failed_tests(other.log_tail):
            pulls = seen.setdefault(test, [])
            if f"#{other.pr_number}" not in pulls:
                pulls.append(f"#{other.pr_number}")
    if not seen:
        return ""
    named = [f"{test} ({', '.join(pulls)})" for test, pulls in sorted(seen.items())]
    more = len(named) - _NAMED_TESTS
    return (
        "also failed on other pull requests: "
        + "; ".join(named[:_NAMED_TESTS])
        + (f"; and {more} more" if more > 0 else "")
    )
