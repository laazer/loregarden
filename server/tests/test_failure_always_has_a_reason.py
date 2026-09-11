"""A failed run must never be recorded with nothing said about why.

Measured: 19 of 179 failed runs in this installation carried an empty `stderr`.
Two had no `stdout` either; the rest had output, one of them 86,814 characters of
it, that nothing looked at. What reached the operator was a blocked ticket whose
stated cause was the empty string — the repository's own *no silent failures*
rule broken in its own data, at the moment of writing the row.
"""

from loregarden.models.domain import AgentRun, RunStatus, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_errors import NO_RECORDED_REASON, failure_reason
from loregarden.services.seed import seed_database
from sqlmodel import Session, select


def test_stderr_wins_when_there_is_one():
    assert failure_reason(stderr="boom", stdout="anything") == "boom"


def test_whitespace_only_stderr_is_not_a_reason():
    reason = failure_reason(stderr="   \n  ", stdout="the last thing it said")
    assert "the last thing it said" in reason


def test_the_last_line_of_output_is_used_when_stderr_is_empty():
    """The CLIs stream JSON; the final envelope is the result line, and it
    carries the terminal reason when the process knew one."""
    stdout = '{"type":"assistant"}\n{"type":"result","subtype":"error_max_turns"}\n\n'
    reason = failure_reason(stderr="", stdout=stdout)
    assert "error_max_turns" in reason
    assert reason != ""


def test_nothing_at_all_still_says_so():
    """ "It failed" and "there is nothing to report" must not collapse into one
    answer — a blank blocking_issues is what a healthy ticket renders."""
    assert failure_reason(stderr="", stdout="") == NO_RECORDED_REASON


def test_a_long_output_tail_is_bounded():
    reason = failure_reason(stderr="", stdout="x" * 50_000)
    assert len(reason) < 1200


def test_complete_run_records_a_reason_for_a_silent_failure(db_session: Session):
    seed_database(db_session)
    ticket = db_session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    run = AgentRun(
        run_code="orch_silent_failure",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="verifier",
        stage_key="testing",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()

    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.FAILED, stdout="", stderr=""
    )

    db_session.refresh(run)
    assert run.status is RunStatus.FAILED
    assert run.stderr == NO_RECORDED_REASON
    db_session.refresh(ticket)
    assert ticket.blocking_issues, "the stage must block with something an operator can read"


def test_a_succeeded_run_keeps_its_empty_stderr(db_session: Session):
    """Only a failure needs a reason. Inventing one for a clean run would put
    prose on the happy path."""
    seed_database(db_session)
    ticket = db_session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    run = AgentRun(
        run_code="orch_clean_run",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="verifier",
        stage_key="testing",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()

    OrchestrationService(db_session).complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=(
            "<<<LOREGARDEN_STAGE_REPORT>>>\n"
            '{"status": "pass", "confidence": 0.9}\n'
            "<<<END_STAGE_REPORT>>>\n"
        ),
        stderr="",
    )

    db_session.refresh(run)
    assert run.stderr == ""
