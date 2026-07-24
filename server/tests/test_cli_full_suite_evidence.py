"""The full-suite *producer* note injected into agent prompts by CliAgentExecutor.

Only the stage that runs the full suite (`run_tests` skill) is told to record a
green run as reusable, commit-scoped evidence. Consumers reuse it via the general
evidence ledger (see test_evidence_ledger.py), not here.
"""

from loregarden.agents.executors.cli import CliAgentExecutor
from sqlmodel import Session


def _note(db_session: Session, skill: str) -> str:
    return CliAgentExecutor(db_session)._full_suite_producer_note(skill)


def test_suite_runner_is_told_to_record_green_evidence(db_session: Session):
    note = _note(db_session, "run_tests")
    assert "loregarden_attach_evidence" in note
    assert "full_suite_green" in note


def test_unrelated_stage_gets_no_producer_note(db_session: Session):
    assert _note(db_session, "apply_patch") == ""
    assert _note(db_session, "ac_gate") == ""
