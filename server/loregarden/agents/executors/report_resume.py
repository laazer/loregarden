"""Resume a stage run's session once when it exits 0 without a stage report (848).

A Claude stage run cannot end its turn without a report: its `Stop` hook
refuses (`agents/stage_report_stop_hook`). cursor-agent, which runs most stages,
takes no hook this process can install, and its runs end the same way: the final
text announces a next step ("Attaching the seams plan artifact next.") and the
session is over. Over 2026-09-04..09-30, 10 cursor runs on 9 tickets did, and 9
of those orchestrations blocked for a person.

So the refusal is made from outside instead: when a run exits 0 with a `result`
envelope and no parseable report, its session is resumed once with a follow-up
asking it to finish the step it announced and report. A report from the resumed
turn settles the stage as if the first turn had written it. Anything else (no
report again, the resume failed, timed out) leaves the run exactly as it was, so
`run_completion` blocks it the way it always has, now with a note on the run
saying a resume was tried and how it went.

The resumed turn runs under its own file stem (`<run_code>-resume`): a second
process may not write the first one's `.out`/`.rc`. A server restart during it
re-attaches to the first process's files and settles from those, which is the
missing-report block again: it fails closed.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from loregarden.agents.cli_adapters import CliInvocation, cursor_resume_invocation
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.dot_line import SYS
from loregarden.models.domain import AgentRun, CliAdapter, RunStatus
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.stage_report import (
    REPORT_BLOCK_REMINDER,
    parse_stage_report,
    result_envelope,
)

logger = logging.getLogger(__name__)

#: Adapters whose session this process can continue in print mode. Claude is
#: absent on purpose: its Stop hook keeps the turn open in-session instead.
RESUMABLE_ADAPTERS = frozenset({CliAdapter.CURSOR})

RESUME_STEM_SUFFIX = "-resume"

FOLLOW_UP = (
    "Your previous turn ended without the Loregarden stage report, so this stage "
    "cannot advance. If you announced a next step, finish it now. A command you left "
    "running in the background is gone. Then " + REPORT_BLOCK_REMINDER
)


def _note(stderr: str, note: str) -> str:
    return f"{stderr.rstrip()}\n{note}".lstrip()


def _why_no_resume(invocation: CliInvocation, session_id: str) -> str:
    """Why this run cannot be resumed, or "" when it can."""
    if invocation.adapter not in RESUMABLE_ADAPTERS:
        return f"the {invocation.adapter} adapter cannot be resumed from here"
    if invocation.interactive:
        return "it ran interactively, not in print mode"
    if not session_id:
        return "its output carried no result envelope with a session id"
    return ""


def resume_for_missing_report(
    *,
    invocation: CliInvocation,
    run: AgentRun,
    stdout: str,
    stderr: str,
    status: RunStatus,
    repo_root: Path,
    timeout: int,
    streamer: RunLogStreamer,
) -> tuple[str, str, RunStatus]:
    """`(stdout, stderr, status)` for completion, after at most one resume.

    Untouched unless the run exited 0 with no parseable report. The resumed
    turn's output is appended to the first turn's, so the transcript stays whole
    and the report parser, which takes the last report, reads the resumed one.
    """
    if status is not RunStatus.SUCCEEDED or parse_stage_report(stdout) is not None:
        return stdout, stderr, status
    envelope = result_envelope(stdout)
    session_id = envelope.session_id if envelope is not None else ""
    why_not = _why_no_resume(invocation, session_id)
    if why_not:
        # Claude runs land here routinely (their hook already refused the stop);
        # the note is for the operator reading a blocked cursor or codex run.
        return stdout, _note(stderr, f"No stage report; no resume attempted: {why_not}."), status

    streamer.append(
        SYS.name,
        f"No stage report: resuming session {session_id} once to ask for it.",
        force=True,
    )
    resumed = cursor_resume_invocation(invocation, session_id=session_id, prompt=FOLLOW_UP)
    try:
        r_stdout, r_stderr, r_status = run_print_mode(
            invocation=resumed,
            repo_root=repo_root,
            timeout=timeout,
            streamer=streamer,
            run_id=run.id,
            run_code=f"{run.run_code}{RESUME_STEM_SUFFIX}",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Resuming run %s for its stage report failed: %s", run.run_code, exc)
        note = f"Resumed session {session_id} once for the missing stage report; it failed: {exc}"
        return stdout, _note(stderr, note), status

    combined = f"{stdout.rstrip()}\n{r_stdout}"
    if r_status is RunStatus.CANCELLED:
        note = f"Resumed session {session_id} for the missing stage report; cancelled."
        return combined, _note(stderr, note), RunStatus.CANCELLED
    if r_status is RunStatus.SUCCEEDED and parse_stage_report(r_stdout) is not None:
        note = f"Resumed session {session_id} once; the resumed turn reported."
        return combined, _note(stderr, note), RunStatus.SUCCEEDED
    tail = (r_stderr or "").strip()[-400:]
    note = (
        f"Resumed session {session_id} once for the missing stage report; the resumed "
        f"turn ({r_status.value}) produced none either.{f' Its stderr: {tail}' if tail else ''}"
    )
    logger.warning("Run %s: %s", run.run_code, note)
    return combined, _note(stderr, note), RunStatus.SUCCEEDED
