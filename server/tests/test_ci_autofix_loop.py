"""The CI auto-fix loop: observe a PR's checks, reopen a done ticket, settle the attempt.

`_trigger_implementer_agent` used to log "Would trigger…" at INFO and leave
every attempt `running` forever, and no CI result had ever been recorded,
because the only input was a webhook GitHub cannot deliver to localhost. These
tests drive `CIService.observe` with what `gh pr list` reports, against a real
repository and a real workflow, and check what the operator would see: the
result row, the attempt history, the ticket's state, and the decision on its
history rail.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.config import settings
from loregarden.models.domain import (
    AutoFixStatus,
    CIRunResult,
    CIStatus,
    StageStatus,
    TicketState,
    Workspace,
)
from loregarden.services import ci_autofix_dispatch, ci_checks, ci_poll_scheduler
from loregarden.services.ci_checks import GitHubReadError, OpenPullRequest, rollup
from loregarden.services.ci_poll_scheduler import poll_workspace
from loregarden.services.ci_service import AutoFixRefusal, CIService
from loregarden.services.ticket_pull_request import GhCheck
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds
from tests.worktree_helpers import at_terminal_stage, commit_on, git, make_repo, make_ticket

JOB_URL = "https://github.com/o/r/actions/runs/11/job/22"
FAILING = {
    "name": "server-tests",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "detailsUrl": JOB_URL,
}
PASSING = {"name": "server-tests", "status": "COMPLETED", "conclusion": "SUCCESS"}
RUNNING = {"name": "server-tests", "status": "IN_PROGRESS", "conclusion": ""}
LOG = "FAILED tests/test_x.py::test_y - assert 1 == 2\n"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="ci", name="ci", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="done_ticket")
def done_ticket_fixture(session, workspace, repo):
    """A finished top-level ticket whose published branch has one commit.

    Built here so a broken setup is an ERROR rather than a passing assertion.
    """
    ticket = make_ticket(session, workspace, external_id="lg-ci-1", title="Ship it")
    git(repo, "branch", ticket.branch, "main")
    commit_on(repo, ticket.branch, "feature.txt", "v1\n")
    at_terminal_stage(session, ticket)
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    set_stage_status(ticket, instance, stages, "done", StageStatus.DONE)
    session.add(ticket)
    session.add(instance)
    session.commit()
    session.refresh(ticket)
    assert ticket.state is TicketState.DONE, "fixture: the ticket must start done"
    return ticket


@pytest.fixture(name="gh_logs", autouse=True)
def gh_logs_fixture():
    """`gh run view --log-failed` answers with a pytest failure."""
    done = subprocess.CompletedProcess(["gh"], 0, stdout=LOG, stderr="")
    with mock.patch.object(ci_checks, "run_gh", return_value=done) as run_gh:
        yield run_gh


@pytest.fixture(name="scheduled", autouse=True)
def scheduled_fixture():
    """Admission and scheduling stubbed: no agent runs, and no slot is held between steps."""
    with (
        mock.patch.object(
            ci_autofix_dispatch, "admit_resumes", side_effect=lambda _s, plans: ([plans[0][1]], [])
        ) as admit,
        mock.patch.object(ci_autofix_dispatch, "schedule_interrupted_resumes") as schedule,
    ):
        yield admit, schedule


def _tip(repo, branch: str) -> str:
    return git(repo, "rev-parse", branch).stdout.strip()


def _pr(branch: str, sha: str, *checks: dict) -> OpenPullRequest:
    return OpenPullRequest.model_validate(
        {
            "number": 7,
            "url": "https://github.com/o/r/pull/7",
            "headRefName": branch,
            "headRefOid": sha,
            "statusCheckRollup": list(checks),
        }
    )


def _observe(session, ticket, workspace, pr):
    session.refresh(ticket)
    return CIService(session).observe(ticket, workspace, pr)


def _finish_fix_turn(session, ticket):
    """What the scheduled orchestration does when the repair turn passes: done again."""
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    set_stage_status(ticket, instance, stages, "done", StageStatus.DONE)
    session.add(ticket)
    session.add(instance)
    session.commit()
    session.refresh(ticket)


# -- Reading GitHub ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("checks", "expected"),
    [
        ([], CIStatus.PENDING),
        ([FAILING, RUNNING], CIStatus.PENDING),
        ([FAILING, PASSING], CIStatus.FAILING),
        ([PASSING], CIStatus.PASSING),
        ([{"name": "x", "status": "COMPLETED", "conclusion": "SKIPPED"}], CIStatus.SKIPPED),
    ],
)
def test_rollup_settles_only_when_nothing_is_running(checks, expected):
    assert rollup([GhCheck.model_validate(c) for c in checks]) is expected


def test_a_failed_pr_listing_raises_rather_than_reading_as_no_prs(tmp_path, gh_logs):
    gh_logs.return_value = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="HTTP 401")
    with pytest.raises(GitHubReadError, match="HTTP 401"):
        ci_checks.list_open_pull_requests(tmp_path)


def test_a_check_with_no_actions_job_is_named_not_dropped(tmp_path):
    status_check = {
        "context": "ci/jenkins",
        "state": "FAILURE",
        "targetUrl": "https://ci.example/1",
    }
    logs = ci_checks.failure_logs(tmp_path, [GhCheck.model_validate(status_check)])
    assert logs.text == ""
    assert logs.errors and "ci/jenkins" in logs.errors[0]


# -- The loop ------------------------------------------------------------------


def test_a_failing_pr_reopens_the_done_ticket_for_a_repair_turn(
    session, workspace, repo, done_ticket, scheduled
):
    branch = done_ticket.branch
    result, outcome = _observe(
        session, done_ticket, workspace, _pr(branch, _tip(repo, branch), FAILING)
    )

    assert result.status is CIStatus.FAILING
    assert "server-tests" in (result.failure_summary or "")
    assert "test_x.py::test_y" in (result.full_logs or "")
    assert outcome is not None and outcome.dispatched, outcome
    assert outcome.attempt.status is AutoFixStatus.RUNNING

    session.refresh(done_ticket)
    assert done_ticket.state is TicketState.IN_PROGRESS, "reopened: the fix is in flight"
    assert done_ticket.workflow_stage_key == "done"
    assert done_ticket.workflow_stage_status is StageStatus.PENDING
    assert done_ticket.next_agent == REPAIR_AGENT_ID
    assert "test_x.py::test_y" in done_ticket.blocking_issues, "the brief carries the log"
    assert "dispatched_ci_fix" in decision_kinds(session, done_ticket.id)
    _admit, schedule = scheduled
    (plan,) = schedule.call_args.args[0]
    assert plan.stop_at_stage_key == "done"
    assert plan.timeout_seconds == settings.ci_auto_fix_timeout


def test_the_same_commit_is_one_result_and_one_attempt(session, workspace, repo, done_ticket):
    pr = _pr(done_ticket.branch, _tip(repo, done_ticket.branch), FAILING)
    _observe(session, done_ticket, workspace, pr)
    _, second = _observe(session, done_ticket, workspace, pr)

    assert second is not None and second.refusal is AutoFixRefusal.ALREADY_ATTEMPTED
    rows = session.exec(select(CIRunResult).where(CIRunResult.ticket_id == done_ticket.id)).all()
    assert len(rows) == 1
    assert len(CIService(session).attempts(done_ticket.id)) == 1


def test_a_passing_fix_commit_settles_the_attempt_succeeded(session, workspace, repo, done_ticket):
    branch = done_ticket.branch
    _observe(session, done_ticket, workspace, _pr(branch, _tip(repo, branch), FAILING))
    fix = commit_on(repo, branch, "feature.txt", "v2\n")
    _finish_fix_turn(session, done_ticket)

    _observe(session, done_ticket, workspace, _pr(branch, fix, RUNNING))
    (attempt,) = CIService(session).attempts(done_ticket.id)
    assert attempt.status is AutoFixStatus.RUNNING, "pending checks on the fix decide nothing"

    _observe(session, done_ticket, workspace, _pr(branch, fix, PASSING))
    session.refresh(attempt)
    assert attempt.status is AutoFixStatus.SUCCEEDED
    assert attempt.completed_at is not None


def test_a_still_failing_fix_fails_the_attempt_and_spends_the_next(
    session, workspace, repo, done_ticket
):
    branch = done_ticket.branch
    _observe(session, done_ticket, workspace, _pr(branch, _tip(repo, branch), FAILING))
    fix = commit_on(repo, branch, "feature.txt", "v2\n")
    _finish_fix_turn(session, done_ticket)

    _, outcome = _observe(session, done_ticket, workspace, _pr(branch, fix, FAILING))

    first, second = CIService(session).attempts(done_ticket.id)
    assert first.status is AutoFixStatus.FAILED
    assert outcome is not None and outcome.dispatched
    assert (second.attempt_number, second.status) == (2, AutoFixStatus.RUNNING)


def test_a_spent_budget_blocks_the_ticket_for_a_person(session, workspace, repo, done_ticket):
    branch = done_ticket.branch
    with mock.patch.object(settings, "ci_retry_limit", 1):
        _observe(session, done_ticket, workspace, _pr(branch, _tip(repo, branch), FAILING))
        fix = commit_on(repo, branch, "feature.txt", "v2\n")
        _finish_fix_turn(session, done_ticket)
        _, outcome = _observe(session, done_ticket, workspace, _pr(branch, fix, FAILING))

    assert outcome is not None and outcome.refusal is AutoFixRefusal.BUDGET_SPENT
    assert outcome.attempt is not None and outcome.attempt.status is AutoFixStatus.REFUSED
    session.refresh(done_ticket)
    assert done_ticket.state is TicketState.BLOCKED
    assert "server-tests" in done_ticket.blocking_issues, "the block names the failing check"


def test_a_fix_turn_with_no_new_commit_fails_its_attempt(session, workspace, repo, done_ticket):
    pr = _pr(done_ticket.branch, _tip(repo, done_ticket.branch), FAILING)
    _observe(session, done_ticket, workspace, pr)
    _finish_fix_turn(session, done_ticket)

    _observe(session, done_ticket, workspace, pr)

    (attempt,) = CIService(session).attempts(done_ticket.id)
    assert attempt.status is AutoFixStatus.FAILED
    assert attempt.result_summary, "a failed attempt says why"


def test_a_ticket_still_in_flight_is_left_to_its_own_workflow(
    session, workspace, repo, done_ticket
):
    done_ticket.state = TicketState.IN_PROGRESS
    session.add(done_ticket)
    session.commit()

    _, outcome = _observe(
        session,
        done_ticket,
        workspace,
        _pr(done_ticket.branch, _tip(repo, done_ticket.branch), FAILING),
    )

    assert outcome is not None and outcome.refusal is AutoFixRefusal.TICKET_NOT_DONE
    assert CIService(session).attempts(done_ticket.id) == [], "an expected refusal records nothing"


def test_a_moved_pr_head_is_refused_and_recorded(session, workspace, repo, done_ticket):
    _, outcome = _observe(
        session, done_ticket, workspace, _pr(done_ticket.branch, "f" * 40, FAILING)
    )

    assert outcome is not None and outcome.refusal is AutoFixRefusal.HEAD_MOVED
    (attempt,) = CIService(session).attempts(done_ticket.id)
    assert attempt.status is AutoFixStatus.REFUSED
    session.refresh(done_ticket)
    assert done_ticket.state is TicketState.DONE, "a refusal does not reopen the ticket"


def test_a_tree_integration_branch_is_refused_with_the_reason(
    session, workspace, repo, done_ticket
):
    integration = f"integration/{done_ticket.external_id}"
    git(repo, "branch", integration, done_ticket.branch)

    _, outcome = _observe(
        session, done_ticket, workspace, _pr(integration, _tip(repo, integration), FAILING)
    )

    assert outcome is not None and outcome.refusal is AutoFixRefusal.TREE_BRANCH
    assert outcome.detail


def test_a_skipped_commit_stays_skipped(session, workspace, repo, done_ticket):
    pr = _pr(done_ticket.branch, _tip(repo, done_ticket.branch), RUNNING)
    _observe(session, done_ticket, workspace, pr)
    CIService(session).skip_ci_check(done_ticket.id)

    result, outcome = _observe(
        session, done_ticket, workspace, _pr(done_ticket.branch, pr.head_sha, FAILING)
    )

    assert result.status is CIStatus.SKIPPED
    assert outcome is None


def test_ci_disabled_dispatches_nothing(session, workspace, repo, done_ticket):
    with mock.patch.object(settings, "ci_enabled", False):
        _, outcome = _observe(
            session,
            done_ticket,
            workspace,
            _pr(done_ticket.branch, _tip(repo, done_ticket.branch), FAILING),
        )
    assert outcome is not None and outcome.refusal is AutoFixRefusal.DISABLED
    session.refresh(done_ticket)
    assert done_ticket.state is TicketState.DONE


def test_old_logs_are_pruned(session, workspace, repo, done_ticket):
    from datetime import datetime, timedelta, timezone

    _observe(
        session,
        done_ticket,
        workspace,
        _pr(done_ticket.branch, _tip(repo, done_ticket.branch), FAILING),
    )
    later = datetime.now(timezone.utc) + timedelta(days=settings.ci_log_retention_days + 1)

    assert CIService(session).prune_logs(now=later) == 1
    (row,) = session.exec(select(CIRunResult)).all()
    assert row.full_logs is None
    assert row.failure_summary, "the summary outlives the log"


# -- Mapping and polling ----------------------------------------------------------


def test_a_pr_maps_only_to_the_ticket_that_publishes_its_branch(session, workspace, done_ticket):
    service = CIService(session)
    assert service.ticket_for_branch(workspace, done_ticket.branch).id == done_ticket.id
    assert service.ticket_for_branch(workspace, f"someone/{done_ticket.external_id}") is None
    assert service.ticket_for_branch(workspace, "claude/unrelated-branch") is None


def test_a_workspace_whose_prs_cannot_be_listed_reports_it(session, workspace, gh_logs):
    gh_logs.return_value = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="no auth")
    with mock.patch.dict(ci_poll_scheduler._last_list_error, clear=True):
        poll = poll_workspace(session, workspace)
    assert poll.errors and "no auth" in poll.errors[0]


def test_a_repeating_listing_failure_warns_once_not_every_tick(session, workspace, gh_logs, caplog):
    gh_logs.return_value = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="gone")
    with mock.patch.dict(ci_poll_scheduler._last_list_error, clear=True):
        polls = [poll_workspace(session, workspace) for _ in range(3)]

    assert all(poll.errors for poll in polls), "every tick still reports the failure"
    warnings = [r for r in caplog.records if "could not list pull requests" in r.getMessage()]
    assert len(warnings) == 1


# -- The API ----------------------------------------------------------------------


def test_a_non_github_delivery_is_refused_not_dropped(client):
    res = client.post("/api/ci/webhook/any", json={"status": "failed"})
    assert res.status_code == 400


def test_a_manual_trigger_with_nothing_failing_says_why(client, session, workspace, done_ticket):
    res = client.post(f"/api/ci/trigger-auto-fix/{done_ticket.id}")
    assert res.status_code == 409
    assert res.json()["detail"]
