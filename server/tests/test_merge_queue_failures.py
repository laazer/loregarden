"""A check whose runner died is re-run once; a check that failed still ejects."""

from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.models.domain import MergeQueueEntry, MergeQueueState, MergeQueueWait, utcnow
from loregarden.services import github_pr_listing
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.merge_queue_failures import ActionsJob, failed_tests
from loregarden.services.merge_queue_github import GhQueueGitHub
from loregarden.services.ticket_pull_request import GhCheck
from tests.test_merge_queue_loop import (  # noqa: F401 - pytest fixtures
    REQUIRED,
    _check,
    _cycle,
    entry_fixture,
    github_fixture,
    origin_fixture,
    repo_fixture,
    session_fixture,
    workspace_fixture,
)

RUN = 45
OTHER_RUN = 46


def _job(*, died: bool, attempt: int = 1, run: int = RUN) -> ActionsJob:
    return ActionsJob.model_validate(
        {
            "run_id": run,
            "run_attempt": attempt,
            "steps": [
                {"name": "Ruff", "status": "completed"},
                {"name": "Pytest", "status": "in_progress" if died else "completed"},
            ],
        }
    )


def _fail(github, entry, **jobs: ActionsJob | None) -> None:
    """Each required check in `jobs` failed, behind that Actions job; the rest passed."""
    names = {name.split(" ")[0].lower(): name for name in REQUIRED}
    checks = []
    for short, name in names.items():
        if short not in jobs:
            checks.append(_check(name, "SUCCESS"))
            continue
        url = f"https://github.com/o/r/actions/runs/{RUN}/job/{short}"
        job = jobs[short]
        if job is not None:
            github.jobs[url] = job
            github.attempts.setdefault(job.run_id, job.run_attempt)
        checks.append(GhCheck(name=name, status="COMPLETED", conclusion="FAILURE", detailsUrl=url))
    github.checks[entry.staged_sha] = checks


@pytest.fixture(name="testing")
def testing_fixture(session, workspace, github, entry) -> MergeQueueEntry:
    _cycle(session, workspace, github)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    return entry


def _advance(session, workspace, github, entry):
    report = _cycle(session, workspace, github)
    session.refresh(entry)
    return report


def test_a_dead_runner_is_run_again_and_the_entry_keeps_its_place(
    session, workspace, github, testing
):
    _fail(github, testing, server=_job(died=True))
    staged = testing.staged_sha

    report = _advance(session, workspace, github, testing)

    assert github.reruns == [RUN]
    assert testing.state is MergeQueueState.TESTING
    assert testing.wait is MergeQueueWait.CHECKS_RUNNING
    assert testing.staged_sha == staged
    assert "Server (Python)" in testing.detail and "died" in testing.detail
    assert any("died" in action for action in report.actions)


def test_a_rerun_that_has_not_reported_is_waited_for_not_asked_again(
    session, workspace, github, testing
):
    _fail(github, testing, server=_job(died=True))
    _advance(session, workspace, github, testing)

    report = _advance(session, workspace, github, testing)

    assert github.reruns == [RUN]
    assert testing.state is MergeQueueState.TESTING
    assert not any("died" in action for action in report.actions)


def test_the_rerun_lands_the_entry_when_it_passes(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=True))
    _advance(session, workspace, github, testing)
    github.pass_everything = True

    _advance(session, workspace, github, testing)

    assert testing.state is MergeQueueState.MERGED


def test_a_runner_that_dies_twice_stops_the_entry_and_says_so(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=True, attempt=2))

    _advance(session, workspace, github, testing)

    assert github.reruns == []
    assert testing.state is MergeQueueState.FAILED
    assert "attempt 2" in testing.detail


def test_a_rerun_that_fails_for_real_stops_the_entry(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=True))
    _advance(session, workspace, github, testing)
    _fail(github, testing, server=_job(died=False, attempt=2))

    _advance(session, workspace, github, testing)

    assert github.reruns == [RUN]
    assert testing.state is MergeQueueState.FAILED
    assert "died" not in testing.detail


def test_a_failure_with_a_verdict_is_never_run_again(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=False))
    _advance(session, workspace, github, testing)
    assert github.reruns == []
    assert testing.state is MergeQueueState.FAILED


@pytest.mark.parametrize("real_first", [True, False])
def test_a_real_failure_beside_a_dead_runner_stops_the_entry(
    session, workspace, github, testing, real_first
):
    _fail(github, testing, server=_job(died=not real_first), client=_job(died=real_first))
    _advance(session, workspace, github, testing)
    assert github.reruns == []
    assert testing.state is MergeQueueState.FAILED


def test_a_check_that_is_not_an_actions_job_is_judged_as_it_stands(
    session, workspace, github, testing
):
    _fail(github, testing, server=None)
    _advance(session, workspace, github, testing)
    assert github.reruns == []
    assert testing.state is MergeQueueState.FAILED


def test_two_dead_jobs_of_one_run_are_run_again_together(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=True), client=_job(died=True))
    _advance(session, workspace, github, testing)
    assert github.reruns == [RUN]


def test_dead_jobs_of_two_runs_are_each_run_again(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=True), client=_job(died=True, run=OTHER_RUN))
    _advance(session, workspace, github, testing)
    assert github.reruns == [RUN, OTHER_RUN]


def test_a_refused_rerun_stops_the_entry_with_githubs_words(session, workspace, github, testing):
    _fail(github, testing, server=_job(died=True))
    github.rerun_refused = "Must have admin rights to Repository"
    _advance(session, workspace, github, testing)
    assert testing.state is MergeQueueState.FAILED
    assert "Must have admin rights" in testing.detail


def test_github_not_answering_about_the_job_is_a_wait_not_a_verdict(
    session, workspace, github, testing
):
    _fail(github, testing, server=_job(died=True))
    github.jobs_unreachable = True

    report = _advance(session, workspace, github, testing)

    assert testing.state is MergeQueueState.TESTING
    assert testing.wait is MergeQueueWait.GITHUB_UNREACHABLE
    assert report.errors


def test_the_rerun_gets_its_own_wait_and_only_when_it_is_asked_for(
    session, workspace, github, testing
):
    """The dead attempt spent up to an hour of the limit; the re-run must not inherit that."""
    long_ago = utcnow() - timedelta(seconds=settings.merge_queue_check_timeout_seconds - 60)
    testing.testing_started_at = long_ago
    session.add(testing)
    session.commit()
    _fail(github, testing, server=_job(died=True))

    _advance(session, workspace, github, testing)
    restarted = testing.testing_started_at
    assert restarted.replace(tzinfo=None) > long_ago.replace(tzinfo=None)

    _advance(session, workspace, github, testing)
    assert testing.testing_started_at == restarted


def test_a_job_with_no_steps_died_before_its_first():
    assert ActionsJob(run_id=1, run_attempt=1, steps=[]).died


# -- tests that failed elsewhere ----------------------------------------------

LOG = (
    "Server (Python)\tPytest\t2026-11-20T21:34:40.9525359Z FAILED "
    "tests/test_a.py::test_one[a b] - AssertionError: assert 'x'\n"
    "Server (Python)\tPytest\t2026-11-20T21:34:40.9525360Z ERROR "
    "tests/test_b.py::test_two - Failed: Timeout\n"
    "Server (Python)\tPytest\t2026-11-20T21:34:40.9525361Z FAILED tests/test_c.py::test_three\n"
    "Server (Python)\tPytest\t2026-11-20T21:34:40.9525362Z 3 failed\n"
)


def test_the_failed_tests_are_read_from_githubs_log_lines():
    assert failed_tests(LOG) == {
        "tests/test_a.py::test_one[a b]",
        "tests/test_b.py::test_two",
        "tests/test_c.py::test_three",
    }


def _failed_entry(session, workspace, pr: int, log: str, *, position: int, **fields) -> None:
    session.add(
        MergeQueueEntry(
            workspace_id=workspace.id,
            pr_number=pr,
            head_sha=f"{pr:040d}",
            position=position,
            log_tail=log,
            **{"state": MergeQueueState.FAILED, **fields},
        )
    )
    session.commit()


def _stop_on(session, workspace, github, testing, log: str = LOG) -> str:
    github.log = log
    _fail(github, testing, server=_job(died=False))
    _advance(session, workspace, github, testing)
    assert testing.state is MergeQueueState.FAILED
    return testing.detail


def test_a_stop_names_the_tests_that_also_failed_other_prs(session, workspace, github, testing):
    _failed_entry(session, workspace, 90, LOG.splitlines()[0], position=900)
    _failed_entry(session, workspace, 91, LOG.splitlines()[0], position=901)
    _failed_entry(session, workspace, 91, LOG.splitlines()[0], position=902)
    detail = _stop_on(session, workspace, github, testing)
    assert "tests/test_a.py::test_one[a b] (#90, #91)" in detail
    assert "test_two" not in detail


def test_a_test_that_failed_only_elsewhere_is_not_named(session, workspace, github, testing):
    _failed_entry(session, workspace, 90, LOG, position=900)
    detail = _stop_on(session, workspace, github, testing, log=LOG.splitlines()[0])
    assert "test_one" in detail
    assert "test_two" not in detail and "test_three" not in detail


def test_a_long_list_of_tests_is_counted_not_spelled_out(session, workspace, github, testing):
    log = "\n".join(f"FAILED tests/test_x.py::test_{n}" for n in range(7))
    _failed_entry(session, workspace, 90, log, position=900)
    detail = _stop_on(session, workspace, github, testing, log=log)
    assert detail.count("(#90)") == 5
    assert "and 2 more" in detail


@pytest.mark.parametrize(
    "fields",
    [
        {"state": MergeQueueState.MERGED},
        {"state_changed_at": utcnow() - timedelta(days=15)},
    ],
)
def test_a_landed_or_old_failure_elsewhere_is_not_named(
    session, workspace, github, testing, fields
):
    _failed_entry(session, workspace, 90, LOG, position=900, **fields)
    assert "also failed" not in _stop_on(session, workspace, github, testing)


def test_the_same_prs_earlier_failure_is_not_elsewhere(session, workspace, github, testing):
    _failed_entry(session, workspace, testing.pr_number, LOG, position=900)
    assert "also failed" not in _stop_on(session, workspace, github, testing)


def test_a_log_that_names_no_test_says_nothing_about_elsewhere(session, workspace, github, testing):
    _failed_entry(session, workspace, 90, LOG, position=900)
    assert "also failed" not in _stop_on(session, workspace, github, testing, log="no summary")


# -- what is asked of gh ------------------------------------------------------


def _gh(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return mock.patch.object(
        github_pr_listing,
        "run_gh",
        return_value=subprocess.CompletedProcess([], returncode, stdout, stderr),
    )


def test_the_job_is_read_by_the_id_in_the_checks_url():
    raw = '{"run_id": 45, "run_attempt": 1, "steps": [{"name": "x", "status": "in_progress"}]}'
    with _gh(raw) as run:
        job = GhQueueGitHub(Path(".")).failed_job("https://github.com/o/r/actions/runs/45/job/77")
    assert job.died and job.run_id == 45
    assert run.call_args.args[0][-1].endswith("/actions/jobs/77")


def test_a_check_with_no_job_url_is_not_read_as_a_job():
    with _gh() as run:
        assert GhQueueGitHub(Path(".")).failed_job("https://ci.example.com/1") is None
    run.assert_not_called()


@pytest.mark.parametrize("read", ["failed_job", "run_attempt"])
def test_an_unreadable_answer_raises(read):
    argument = "https://github.com/o/r/actions/runs/1/job/2" if read == "failed_job" else 1
    with _gh("{}"), pytest.raises(GitHubReadError):
        getattr(GhQueueGitHub(Path(".")), read)(argument)


def test_the_run_attempt_is_the_runs_own():
    with _gh('{"run_attempt": 2}') as run:
        assert GhQueueGitHub(Path(".")).run_attempt(45) == 2
    assert run.call_args.args[0][-1].endswith("/actions/runs/45")


def test_a_rerun_asks_for_the_failed_jobs_only_and_reports_a_refusal():
    with _gh() as run:
        assert GhQueueGitHub(Path(".")).rerun_failed_jobs(45) == ""
    assert run.call_args.args[0] == ["run", "rerun", "45", "--failed"]
    with _gh(returncode=1, stderr="cannot be rerun"):
        assert GhQueueGitHub(Path(".")).rerun_failed_jobs(45) == "cannot be rerun"
