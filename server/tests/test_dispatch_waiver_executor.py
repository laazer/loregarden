"""What a waived run does at dispatch, and what it still records.

The waiver exists so a person's "run it anyway" survives the re-dispatch that
their approval triggers. What it must NOT do is make the run look like one that
started in a healthy checkout — `core.bare=true` is still true, and a failure
twenty minutes later has to be traceable back to it.
"""

from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    Ticket,
    Workspace,
)
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="bare_flagged_repo")
def bare_flagged_repo_fixture(tmp_path) -> Path:
    """A real repo carrying the exact misconfiguration that started this: a
    working tree whose config claims it is bare."""
    repo = make_repo(tmp_path)
    git(repo, "config", "--local", "core.bare", "true")
    return repo


@pytest.fixture(name="dispatch")
def dispatch_fixture(db_session: Session, bare_flagged_repo: Path):
    ticket = db_session.exec(select(Ticket)).first()
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.repo_path = str(bare_flagged_repo)
    db_session.add(workspace)
    db_session.commit()

    def make_run(*, waiver: str = "") -> AgentRun:
        run = AgentRun(
            run_code=f"r-{waiver or 'none'}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            stage_key=ticket.workflow_stage_key or "plan",
            status=RunStatus.RUNNING,
            dispatch_waiver_approval_id=waiver,
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        return run

    return CliAgentExecutor(db_session), ticket, workspace, bare_flagged_repo, make_run


def test_a_run_without_a_waiver_parks_on_the_failing_check(db_session, dispatch):
    """The check still bites for everyone who has not been asked."""
    executor, ticket, workspace, repo, make_run = dispatch
    run = make_run()

    parked = executor._record_and_check_boundary(
        run, ticket, workspace, repo_root=repo, dirty_paths=set()
    )

    assert parked is not None
    assert parked.status is RunStatus.CANCELLED
    # By stage key, not "the first approval": the seeded workspace already
    # carries a genuine WORKFLOW_GATE, and picking that one up would have this
    # test pass or fail on row order rather than on the park.
    approval = db_session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == run.stage_key)
    ).first()
    assert approval.kind is ApprovalKind.STAGE_PARK
    assert approval.status is ApprovalStatus.PENDING


def test_a_waived_run_is_allowed_past_the_same_check(db_session, dispatch):
    """The point of the fix: approving re-dispatches, and the re-dispatch gets
    through instead of parking on the identical failure forever."""
    executor, ticket, workspace, repo, make_run = dispatch
    run = make_run(waiver="appr-123")

    parked = executor._record_and_check_boundary(
        run, ticket, workspace, repo_root=repo, dirty_paths=set()
    )

    assert parked is None


def test_a_waived_run_still_records_the_check_it_was_let_past(db_session, dispatch):
    """Skipping the parking is not skipping the checking. "A human waived it"
    and "the environment was fine" must not read the same afterwards."""
    executor, ticket, workspace, repo, make_run = dispatch
    run = make_run(waiver="appr-123")

    executor._record_and_check_boundary(run, ticket, workspace, repo_root=repo, dirty_paths=set())

    db_session.refresh(run)
    assert "git_core_bare" in run.start_preflight_failures_json


def test_a_waived_run_says_so_in_its_own_log(db_session, dispatch):
    """Otherwise the log opens exactly like a healthy run's, and whoever reads
    the failure hunts the agent's output for a cause sitting in an approval."""
    executor, ticket, workspace, repo, make_run = dispatch
    run = make_run(waiver="appr-123")
    executor._record_and_check_boundary(run, ticket, workspace, repo_root=repo, dirty_paths=set())
    db_session.refresh(run)
    streamer = mock.Mock()

    executor._maybe_warn_dispatch_waiver(streamer=streamer, run=run)

    level, message = streamer.append.call_args[0][:2]
    assert level == "WARN"
    assert "git_core_bare" in message
    assert "appr-123" in message


def test_an_ordinary_run_gets_no_waiver_warning(db_session, dispatch):
    """The warning has to stay rare enough to mean something."""
    executor, ticket, workspace, repo, make_run = dispatch
    streamer = mock.Mock()

    executor._maybe_warn_dispatch_waiver(streamer=streamer, run=make_run())

    streamer.append.assert_not_called()
