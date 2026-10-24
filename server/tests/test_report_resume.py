"""A cursor stage run that exits 0 with no stage report is resumed once (848).

cursor-agent takes no Stop hook this process can install, and its runs ended
announcing a next step with no report: 10 runs on 9 tickets over 2026-09-04..30,
9 of them blocking their orchestration for a person.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import cli_adapters
from loregarden.agents.cli_adapters import CliInvocation, cursor_resume_invocation
from loregarden.agents.executors import cli as cli_module
from loregarden.agents.executors import report_resume
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.report_resume import (
    FOLLOW_UP,
    RESUME_STEM_SUFFIX,
    resume_for_missing_report,
)
from loregarden.models.domain import AgentRun, RunStatus
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.stage_report import parse_stage_report
from tests.factories import make_workspace_ticket

REPORT = (
    "<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.9, "reroute_to_stage": null, "reroute_context": ""}\n'
    "<<<END_STAGE_REPORT>>>"
)
SESSION = "f2d4d949-df34-4910-80ad-2167c60d7528"


def _stream(final_text: str, *, session_id: str = SESSION) -> str:
    envelope = {"type": "result", "subtype": "success", "result": final_text}
    if session_id:
        envelope["session_id"] = session_id
    return "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
            json.dumps({"type": "result", **envelope}),
        ]
    )


@pytest.fixture
def cursor_invocation(tmp_path: Path) -> CliInvocation:
    with mock.patch.dict("os.environ", {"LOREGARDEN_CURSOR_OUTPUT_FORMAT": "stream-json"}):
        return cli_adapters._cursor_print_invocation(
            prompt="the stage prompt", workspace_root=tmp_path, orchestrated=True
        )


@pytest.fixture
def run() -> AgentRun:
    return AgentRun(id="11111111-2222", run_code="run_abc123", ticket_id="t", workspace_id="w")


@pytest.fixture
def spawn():
    with mock.patch.object(report_resume, "run_print_mode") as spawned:
        yield spawned


def _resume(invocation, run, stdout, *, status=RunStatus.SUCCEEDED, tmp_path=Path(".")):
    return resume_for_missing_report(
        invocation=invocation,
        run=run,
        stdout=stdout,
        stderr="",
        status=status,
        repo_root=tmp_path,
        timeout=60,
        streamer=mock.MagicMock(spec=RunLogStreamer),
    )


# ---- the resume command --------------------------------------------------------


def test_the_resume_keeps_every_flag_and_swaps_only_the_prompt(cursor_invocation):
    resumed = cursor_resume_invocation(cursor_invocation, session_id=SESSION, prompt="report now")
    original = cursor_invocation.argv
    assert resumed.argv[: original.index("-p") + 1] == original[: original.index("-p") + 1]
    assert resumed.argv[original.index("-p") + 1 : original.index("-p") + 3] == [
        "--resume",
        SESSION,
    ]
    # The prompt rides on stdin on both turns, so `--resume <id>` is the only
    # argv difference and the follow-up text appears nowhere in it.
    assert resumed.stdin_prompt == "report now"
    assert not any("report now" in arg for arg in resumed.argv)
    assert not any("the stage prompt" in arg for arg in resumed.argv)
    assert len(resumed.argv) == len(original) + 2
    assert resumed.resume_session_id == SESSION


# ---- when it resumes, and what comes of it ----------------------------------------------


def test_a_run_that_reported_is_left_alone(cursor_invocation, run, spawn):
    stdout = _stream(f"Done.\n{REPORT}")
    assert _resume(cursor_invocation, run, stdout) == (stdout, "", RunStatus.SUCCEEDED)
    spawn.assert_not_called()


def test_a_failed_run_is_left_to_its_own_handling(cursor_invocation, run, spawn):
    out = _resume(cursor_invocation, run, _stream("crashed"), status=RunStatus.FAILED)
    assert out[2] is RunStatus.FAILED
    spawn.assert_not_called()


def test_a_report_from_the_resumed_turn_settles_the_run(cursor_invocation, run, spawn):
    spawn.return_value = (_stream(f"Attached it.\n{REPORT}"), "", RunStatus.SUCCEEDED)
    stdout, stderr, status = _resume(cursor_invocation, run, _stream("Attaching the plan next."))

    assert status is RunStatus.SUCCEEDED
    assert parse_stage_report(stdout) is not None
    assert "Attaching the plan next." in stdout  # the transcript stays whole
    assert "reported" in stderr
    spawn.assert_called_once()
    kwargs = spawn.call_args.kwargs
    assert kwargs["run_code"] == f"run_abc123{RESUME_STEM_SUFFIX}"
    assert kwargs["run_id"] == run.id
    assert kwargs["invocation"].argv[kwargs["invocation"].argv.index("--resume") + 1] == SESSION
    assert kwargs["invocation"].stdin_prompt == FOLLOW_UP


def test_no_report_again_blocks_as_before_and_says_a_resume_was_tried(
    cursor_invocation, run, spawn
):
    spawn.return_value = (_stream("Still working on it."), "", RunStatus.SUCCEEDED)
    stdout, stderr, status = _resume(cursor_invocation, run, _stream("Attaching next."))

    assert status is RunStatus.SUCCEEDED
    assert parse_stage_report(stdout) is None
    assert SESSION in stderr
    assert "produced none" in stderr
    spawn.assert_called_once()  # once, never twice


def test_a_resumed_turn_that_fails_still_settles_as_the_first_turn_did(
    cursor_invocation, run, spawn
):
    # If cursor-agent refuses `--resume` in print mode, the run must still block
    # as a missing report, not read as a crashed run.
    spawn.return_value = ("", "error: unknown option '--resume'", RunStatus.FAILED)
    _, stderr, status = _resume(cursor_invocation, run, _stream("Attaching next."))

    assert status is RunStatus.SUCCEEDED
    assert "unknown option '--resume'" in stderr


def test_a_resume_that_cannot_run_leaves_the_first_turn_as_it_was(cursor_invocation, run, spawn):
    spawn.side_effect = subprocess.TimeoutExpired(cmd="cursor-agent", timeout=60)
    first = _stream("Attaching next.")
    stdout, stderr, status = _resume(cursor_invocation, run, first)

    assert (stdout, status) == (first, RunStatus.SUCCEEDED)
    assert "failed" in stderr


def test_an_operator_cancel_during_the_resume_is_a_cancel(cursor_invocation, run, spawn):
    spawn.return_value = ("", "", RunStatus.CANCELLED)
    assert _resume(cursor_invocation, run, _stream("Attaching next."))[2] is RunStatus.CANCELLED


# ---- runs it cannot resume say why --------------------------------------------------


def test_a_claude_run_is_not_resumed_from_here(run, spawn, tmp_path):
    claude = CliInvocation(argv=["claude", "-p"], adapter="claude", cwd=str(tmp_path))
    _, stderr, status = _resume(claude, run, _stream("waiting"))
    assert status is RunStatus.SUCCEEDED
    assert "claude adapter cannot be resumed" in stderr
    spawn.assert_not_called()


def test_a_run_with_no_session_id_says_so(cursor_invocation, run, spawn):
    _, stderr, _ = _resume(cursor_invocation, run, _stream("Attaching next.", session_id=""))
    assert "no result envelope with a session id" in stderr
    spawn.assert_not_called()


# ---- wired into the executor ------------------------------------------------------


def test_the_executor_offers_every_print_mode_run_a_resume(db_session):
    """The test environment runs the `local` adapter, which cannot be resumed, so
    the note on the completed run is the proof the executor asked."""
    ticket = make_workspace_ticket(db_session, "resume-wiring")
    run = AgentRun(
        run_code="run_wired",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()

    no_report = (_stream("Attaching the plan next."), "", RunStatus.SUCCEEDED)
    with mock.patch.object(cli_module, "run_print_mode", return_value=no_report):
        completed = CliAgentExecutor(db_session).execute(run, ticket)

    assert "no resume attempted: the local adapter" in completed.stderr
