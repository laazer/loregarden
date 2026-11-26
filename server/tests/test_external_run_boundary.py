from datetime import datetime

import pytest
from loregarden.models.domain import AgentRun, ExternalHarness, RunStatus, Workspace
from loregarden.services.external_run_boundary import EXTERNAL_RUNS_RECORD_THEIR_START
from sqlmodel import Session, select
from tests.worktree_helpers import make_run, make_ticket

AFTER = datetime(2026, 11, 28)
BEFORE = datetime(2026, 11, 20)


@pytest.fixture
def make(db_session: Session):
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = make_ticket(db_session, workspace, external_id="LG-BOUNDARY-1")
    count = iter(range(100))

    def build(*, harness, status, sha, created) -> AgentRun:
        run = make_run(db_session, workspace, ticket, f"r_boundary_{next(count)}")
        run.external_harness = harness
        run.status = status
        run.start_head_sha = sha
        run.created_at = created
        db_session.add(run)
        db_session.commit()
        return run

    return build


def test_only_a_finished_external_run_opened_since_the_guard_with_no_start_is_reported(
    make, db_session
):
    reported = make(
        harness=ExternalHarness.CLAUDE_CODE, status=RunStatus.SUCCEEDED, sha="", created=AFTER
    )
    make(
        harness=ExternalHarness.CLAUDE_CODE, status=RunStatus.SUCCEEDED, sha="abc123", created=AFTER
    )
    make(harness=ExternalHarness.CLAUDE_CODE, status=RunStatus.SUCCEEDED, sha="", created=BEFORE)
    make(harness=ExternalHarness.CLAUDE_CODE, status=RunStatus.FAILED, sha="", created=AFTER)
    make(harness=None, status=RunStatus.SUCCEEDED, sha="", created=AFTER)

    violations = EXTERNAL_RUNS_RECORD_THEIR_START.detect(db_session)

    assert [v.subject_id for v in violations] == [reported.id]
