"""The full-suite evidence note injected into agent prompts by CliAgentExecutor.

The suite-runner stage (`run_tests` skill) is told to record a green run as
commit-scoped evidence; the reviewer stage (`ac_gate` skill) is told it may skip
re-running the suite, but only when that evidence already covers the exact tree
it is about to test. Every other stage gets nothing.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from loregarden.agents.executors.cli import CliAgentExecutor
from sqlmodel import Session


def _note(db_session: Session, skill: str, *, green: bool = False) -> str:
    executor = CliAgentExecutor(db_session)
    with (
        patch("loregarden.agents.executors.cli.full_suite_green_at_head", return_value=green),
        patch("loregarden.agents.executors.cli.resolve_workspace_root", return_value=Path(".")),
    ):
        return executor._full_suite_evidence_note(skill, SimpleNamespace(), SimpleNamespace())


def test_suite_runner_is_told_to_record_green_evidence(db_session: Session):
    note = _note(db_session, "run_tests")
    assert "loregarden_attach_evidence" in note
    assert "full_suite_green" in note


def test_reviewer_is_told_to_skip_when_suite_green_at_head(db_session: Session):
    note = _note(db_session, "ac_gate", green=True)
    assert "Do not re-run the full suite" in note
    assert "full_suite_green" in note


def test_reviewer_gets_nothing_when_suite_not_proven_green(db_session: Session):
    assert _note(db_session, "ac_gate", green=False) == ""


def test_unrelated_stage_gets_no_full_suite_note(db_session: Session):
    assert _note(db_session, "apply_patch", green=True) == ""
