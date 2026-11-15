"""A finished survey pass leaves its carrier done.

Six carriers sat open on 2026-11-03 although their passes had finished: the
runs died after the agent recorded the pass (lease expiry, a transition gate
that could not run), and only a clean run completion ever finished a carrier.
`record_reads` refuses a re-run once the pass is FINISHED, so a requeue could
not finish them either.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import SurveyPassOutcome, SurveyPassStatus
from loregarden.services.invariant_registry import FINISHED_PASSES_CLOSE_THEIR_CARRIER
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.reference_survey import carrier_settlement, feeder
from loregarden.services.ticket_state_service import choose
from loregarden.services.workflow_state import set_stage_status
from loregarden.testing.factories import make_agent_run
from sqlmodel import Session
from tests.survey_support import build_world, running_pass, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(tmp_path / "clone", {"README.md": "r", "src/a.py": "a"})
    source, survey_pass = running_pass(session, world, tree)
    parent = session.get(Ticket, world.carrier_parent_id)
    carrier = feeder._create_carrier(session, source, survey_pass, parent, "brief")
    survey_pass.carrier_ticket_id = carrier.id
    session.add(survey_pass)
    session.commit()
    yield session, survey_pass, carrier
    session.close()


def _finish(session: Session, survey_pass) -> None:
    survey_pass.status = SurveyPassStatus.FINISHED
    survey_pass.outcome = SurveyPassOutcome.PARTIAL
    survey_pass.finished_at = utcnow()
    session.add(survey_pass)
    session.commit()


def _block_survey_stage(session: Session, carrier: Ticket) -> None:
    """What the lease sweep leaves: `survey` BLOCKED with the run gone."""
    instance, stages = OrchestrationService(session)._resolve_stages(carrier)
    set_stage_status(carrier, instance, stages, "survey", StageStatus.BLOCKED)
    choose(session, carrier, TicketState.BLOCKED, actor="orchestrator", emit=False)
    carrier.blocking_issues = "Agent run lease expired"
    session.add(instance)
    session.add(carrier)
    session.commit()


def test_a_carrier_blocked_after_its_pass_finished_is_finished(ctx):
    session, survey_pass, carrier = ctx
    _finish(session, survey_pass)
    _block_survey_stage(session, carrier)

    (violation,) = FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session)
    assert violation.subject_id == carrier.id
    assert violation.entered_at == survey_pass.finished_at

    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.repair(session, [violation]) == 1
    session.refresh(carrier)
    assert carrier.state is TicketState.DONE
    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session) == []


def test_a_carrier_still_on_its_survey_stage_is_finished(ctx):
    """The run ended without completing the stage, and nothing blocked it."""
    session, survey_pass, carrier = ctx
    _finish(session, survey_pass)

    violations = FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session)
    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.repair(session, violations) == 1
    session.refresh(carrier)
    assert carrier.state is TicketState.DONE


def test_a_carrier_whose_run_is_still_finishing_is_left_alone(ctx):
    """The control: a FINISHED pass waits minutes for its orchestration to end."""
    session, survey_pass, carrier = ctx
    _finish(session, survey_pass)
    session.add(
        OrchestrationRun(
            ticket_id=carrier.id,
            workspace_id=carrier.workspace_id,
            run_code="orch_live",
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    session.commit()

    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session) == []


def test_a_pass_still_dispatched_is_not_a_violation(ctx):
    session, _survey_pass, _carrier = ctx

    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session) == []


# --- a carrier whose finish was refused on a record a merge inflated --------


def _git(repo: Path, *args: str) -> str:
    # GIT_DIR beats cwd, and it leaks in from the pre-push hook.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    done = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


_BRANCH = "reference-sources/carrier"


@pytest.fixture(name="branch_repo")
def branch_repo_fixture(tmp_path) -> tuple[Path, str]:
    """A carrier's branch that `main-side` was merged into after the run started.

    Returns the repository and the sha the run started at."""
    repo = tmp_path / "carrier-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", _BRANCH)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "seed")
    start = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "main-side")
    (repo / "theirs.py").write_text("theirs\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "main's work")
    _git(repo, "checkout", "-q", _BRANCH)
    _git(repo, "merge", "--no-ff", "-qm", "Merge main-side", "main-side")
    return repo, start


def _block_on_done(session: Session, carrier: Ticket) -> None:
    """What a refused landing leaves: `survey` done, the cursor on `done`, BLOCKED."""
    instance, stages = OrchestrationService(session)._resolve_stages(carrier)
    set_stage_status(carrier, instance, stages, "survey", StageStatus.DONE)
    carrier.workflow_stage_key = "done"
    set_stage_status(carrier, instance, stages, "done", StageStatus.BLOCKED)
    choose(session, carrier, TicketState.BLOCKED, actor="orchestrator", emit=False)
    carrier.blocking_issues = "Not landed: this ticket's runs changed 1 file(s)"
    session.add(instance)
    session.add(carrier)
    session.commit()


def _recorded_run(
    session: Session, carrier: Ticket, repo: Path, start: str, paths: list[str]
) -> AgentRun:
    return make_agent_run(
        session,
        workspace_id=carrier.workspace_id,
        ticket_id=carrier.id,
        run_code="RUN-SURVEY",
        status="succeeded",
        stage_key="survey",
        start_repo_path=str(repo),
        start_branch=_BRANCH,
        start_head_sha=start,
        changed_paths_json=json.dumps(paths),
        changed_paths_recorded_at=utcnow(),
    )


def test_a_carrier_refused_on_paths_a_merge_brought_in_is_finished(ctx, branch_repo):
    """lg-reference-sources-2160: `origin/main` merged into its branch mid-run put
    main's files on the survey run's record, and every finish was refused."""
    session, survey_pass, carrier = ctx
    repo, start = branch_repo
    run = _recorded_run(session, carrier, repo, start, ["theirs.py"])
    _finish(session, survey_pass)
    _block_on_done(session, carrier)

    violations = FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session)
    assert [v.subject_id for v in violations] == [carrier.id]
    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.repair(session, violations) == 1
    session.refresh(run)
    session.refresh(carrier)
    assert json.loads(run.changed_paths_json) == []
    assert carrier.state is TicketState.DONE


def test_a_carrier_whose_record_stands_stays_blocked_for_a_person(ctx, branch_repo):
    """The control. Nothing a merge brought in is on the record, so the refusal's
    cause has not changed and retrying it would re-land the same tree."""
    session, survey_pass, carrier = ctx
    repo, start = branch_repo
    (repo / "mine.py").write_text("mine\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "the run's own commit")
    run = _recorded_run(session, carrier, repo, start, ["mine.py"])
    _finish(session, survey_pass)
    _block_on_done(session, carrier)

    violations = FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session)
    with mock.patch.object(
        carrier_settlement, "unblock_for_rerun", wraps=carrier_settlement.unblock_for_rerun
    ) as rearm:
        assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.repair(session, violations) == 0
    rearm.assert_not_called()
    session.refresh(run)
    session.refresh(carrier)
    assert json.loads(run.changed_paths_json) == ["mine.py"]
    assert carrier.state is TicketState.BLOCKED
