"""The stage-report `Stop` hook keeps a settling stage's pass open until its criteria are settled.

A builtin-driver run reports through the sentinel, so `complete_stage`'s own
refusal never reaches it; the hook refuses the stop instead, reading the ledger
read-only from the database the server named on its command line. Every way
the check cannot run lets the stop through and says why on stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import stage_report_stop_hook as hook
from loregarden.agents.cli_adapters import stage_report_hook_settings
from loregarden.config import settings
from loregarden.mcp.caller import RUN_ID_ENV
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageReportStatus,
    StageStatus,
    Ticket,
    utcnow,
)
from loregarden.services.path_resolve import resolve_sqlite_path
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from loregarden.testing.factories import findings_fragment
from sqlmodel import Session
from tests.test_criterion_settle_check import CRITERIA, _settle_all, ticket_fixture  # noqa: F401

PASS = (
    "Verified.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.9}\n<<<END_STAGE_REPORT>>>'
)
REWORK = (
    "A test fails.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "needs_rework", "confidence": 0.9, "reroute_to_stage": "implement", '
    '"reroute_context": "Errors do not name the arm", '
    f'"unmet_criteria": ["Errors name the arm"]{findings_fragment(StageReportStatus.NEEDS_REWORK)}}}'
    "\n<<<END_STAGE_REPORT>>>"
)


@pytest.fixture(name="db_path")
def db_path_fixture(db_session: Session) -> Path:
    """The test's own database file, the one the server would name with --db."""
    return Path(db_session.get_bind().url.database)


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path):
    with mock.patch.object(hook, "_STATE_DIR", tmp_path / "state"):
        yield


def _run_at(session: Session, ticket: Ticket, stage_key: str) -> AgentRun:
    run = AgentRun(
        run_code=f"r-{ticket.external_id}-{stage_key}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="verifier",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
        command="claude",
        started_at=utcnow(),
    )
    session.add(run)
    session.commit()
    return run


def _decide(last: str, db_path: Path | None, run_id: str) -> str | None:
    hook_input = hook.StopHookInput.model_validate(
        {"session_id": "s-1", "last_assistant_message": last}
    )
    with mock.patch.dict(os.environ, {RUN_ID_ENV: run_id}):
        return hook.decide(hook_input, [], db_path=db_path)


def test_a_verify_pass_with_an_unsettled_criterion_is_refused_and_names_it(
    db_session, ticket, db_path
):
    run = _run_at(db_session, ticket, "verify")

    reason = _decide(PASS, db_path, run.id)

    assert reason is not None
    for criterion in CRITERIA:
        assert f"- [unsettled] {criterion}" in reason
    assert "loregarden_settle_criterion" in reason
    assert "write your stage report again" in reason


def test_once_settled_the_same_pass_ends_the_turn(db_session, ticket, db_path):
    run = _run_at(db_session, ticket, "verify")
    assert _decide(PASS, db_path, run.id) is not None

    _settle_all(db_session, ticket)

    assert _decide(PASS, db_path, run.id) is None


def test_a_rework_report_is_never_refused_for_unsettled_criteria(db_session, ticket, db_path):
    run = _run_at(db_session, ticket, "verify")

    assert _decide(REWORK, db_path, run.id) is None


def test_a_pass_from_a_stage_that_settles_nothing_ends_the_turn(db_session, ticket, db_path):
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    set_stage_status(ticket, instance, stages, "verify", StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()
    run = _run_at(db_session, ticket, "review")

    assert _decide(PASS, db_path, run.id) is None


def test_the_refusals_stop_at_max_blocks(db_session, ticket, db_path):
    run = _run_at(db_session, ticket, "verify")

    refusals = [_decide(PASS, db_path, run.id) for _ in range(hook.MAX_BLOCKS + 1)]

    assert all(r is not None for r in refusals[: hook.MAX_BLOCKS])
    assert refusals[-1] is None


def test_an_unreachable_database_lets_the_stop_through_and_says_why(
    db_session, ticket, tmp_path, capsys
):
    run = _run_at(db_session, ticket, "verify")
    missing = tmp_path / "nowhere" / "loregarden.db"

    assert _decide(PASS, missing, run.id) is None

    assert "report not checked" in capsys.readouterr().err
    # Opened read-only: the hook never creates a database it was pointed at.
    assert not missing.exists()


def test_a_run_the_database_does_not_hold_lets_the_stop_through_and_says_why(
    ticket, db_path, capsys
):
    assert _decide(PASS, db_path, "no-such-run") is None

    err = capsys.readouterr().err
    assert "report not checked" in err
    assert "no-such-run" in err


def test_without_a_database_named_a_pass_ends_the_turn(db_session, ticket):
    run = _run_at(db_session, ticket, "verify")

    assert _decide(PASS, None, run.id) is None


def test_the_server_names_its_own_database_on_the_hook_command():
    hook_entry = json.loads(stage_report_hook_settings())["hooks"]["Stop"][0]["hooks"][0]
    command = hook_entry["command"]
    # Room to run a reviewer's tests (`stage_report_reject`); Claude Code's default is 60s.
    assert hook_entry["timeout"] >= 300

    expected = resolve_sqlite_path(settings.database_url, settings.repo_root)
    assert command.endswith(f"{hook.DB_FLAG} {expected}")


def test_the_hook_process_reads_db_from_its_command_line(db_session, ticket, db_path):
    run = _run_at(db_session, ticket, "verify")
    stdin = json.dumps({"session_id": "s-e2e", "last_assistant_message": PASS})

    result = subprocess.run(
        [sys.executable, "-m", "loregarden.agents.stage_report_stop_hook", "--db", str(db_path)],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, RUN_ID_ENV: run.id},
    )

    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert f"- [unsettled] {CRITERIA[0]}" in decision["reason"]
