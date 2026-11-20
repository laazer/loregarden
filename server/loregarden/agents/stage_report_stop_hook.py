"""Claude Code ``Stop`` hook for stage runs: the turn may not end without a stage report.

A stage run is one headless turn, and when the turn ends the process ends. An
agent that backgrounds a long command and ends its turn "waiting for the
notification" never gets one: the run exits 0 with no
``<<<LOREGARDEN_STAGE_REPORT>>>`` block and the stage blocks as outcome-unknown.
In the 30 days to 2026-10-14 that was 15 of 17 such blocks, nearly all a
backgrounded full test suite.

Claude Code calls this before it lets the turn end. Two facts make the check
exact rather than heuristic: the hook input carries ``background_tasks`` (what is
still running) and ``last_assistant_message`` (the final text, which is not yet
in the transcript file when the hook fires). A run that has reported may stop. One
that has not is told why it cannot, and resumes in the same session with its
context, so it can wait for the task in the foreground and then report.

``MAX_BLOCKS`` bounds the refusals per run, so a model that will not report
cannot loop forever. Past it the stop goes through and ``run_completion``'s
missing-report block surfaces the run as it always has.

A rework round may resume the session of the run it reworks (`rework_resume`),
so the transcript can hold an earlier turn's report. Only the current turn
counts: what follows the newest prompt. The refusal count is keyed by the run
(its ``LOREGARDEN_MCP_RUN_ID``) as well as the session, so the resumed turn gets
its own ``MAX_BLOCKS`` rather than the remainder of the first one's.

A report is not always enough. A pass from the stage that settles criteria,
while one is unsettled, is refused too (`stage_report_criteria`), so the agent
settles it in this turn rather than the gate reopening the stage as a new run.
So is a judging stage's reject whose named tests all pass
(`stage_report_reject`), so the agent fixes its test rather than the reject
settling as a pass after the run. Both read the database the server names
with ``--db``, read-only, and let the stop through, saying why on stderr,
whenever they cannot run.

Wired in by ``cli_adapters.stage_report_hook_settings``; run as
``python -m loregarden.agents.stage_report_stop_hook --db <path>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

from loregarden.agents.stage_report_criteria import unsettled_refusal
from loregarden.agents.stage_report_reject import unshown_reject_refusal
from loregarden.mcp.caller import RUN_ID_ENV
from loregarden.models.domain import StageReportStatus
from loregarden.services.stage_report import (
    REPORT_BLOCK_REMINDER,
    StageReport,
    parse_stage_report,
)
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.exc import SQLAlchemyError

#: Refusals per session before the stop is let through. Each one lets the agent
#: wait up to the Bash ceiling (10 minutes) in a single foreground call, and the
#: run's own timeout bounds the total anyway.
MAX_BLOCKS = 5

#: The tool a run may report through instead of the sentinel; matched as the
#: MCP tool's suffix and inside a Bash command (the CLI transport).
COMPLETE_STAGE_TOOL = "loregarden_complete_stage"

#: The flag the server names its database with (`cli_adapters`): read-only, for
#: the criteria check on a pass (`stage_report_criteria`).
DB_FLAG = "--db"

_RUNNING = "running"  # py-org: allow-string - Claude Code's background-task status
_ASSISTANT = "assistant"  # py-org: allow-string - Claude Code transcript entry type
_USER = "user"  # py-org: allow-string - Claude Code transcript entry type
_TEXT = "text"  # py-org: allow-string - Claude Code content block type
_TOOL_USE = "tool_use"  # py-org: allow-string - Claude Code content block type
_TOOL_RESULT = "tool_result"  # py-org: allow-string - Claude Code content block type
_BASH = "Bash"  # py-org: allow-string - Claude Code tool name

_STATE_DIR = Path(tempfile.gettempdir()) / "loregarden-stage-report-stop-hook"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BackgroundTask(_Lenient):
    id: str = ""
    status: str = ""
    description: str = ""
    command: str = ""


class StopHookInput(_Lenient):
    session_id: str
    transcript_path: Path | None = None
    last_assistant_message: str | None = ""
    background_tasks: list[BackgroundTask] = []


class _Block(_Lenient):
    """One content block. Only fields this hook reads are typed, and each one
    a model writes freely (a tool's arguments) is kept as raw JSON, so one odd
    value cannot fail the whole line and drop the text beside it."""

    type: str = ""
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, object] = {}
    tool_use_id: str = ""
    is_error: bool | None = False


class _Message(_Lenient):
    #: Only the block-list form. A plain-string body is a prompt, read by
    #: ``_PromptEntry`` instead: it carries no report, but marks where the
    #: current turn begins.
    content: list[_Block] = []


class _Entry(_Lenient):
    type: str = ""
    message: _Message | None = None
    #: Set by ``_entries`` for a prompt line, never read from the transcript.
    is_prompt: bool = False


class _PromptBody(_Lenient):
    content: str


class _PromptEntry(_Lenient):
    """A prompt: a user line whose body is a plain string. The bridge sends the
    stage prompt this way, and a resumed run's follow-up the same."""

    type: str
    message: _PromptBody


def _blocks(entry: _Entry) -> list[_Block]:
    return entry.message.content if entry.message is not None else []


def _current_turn(entries: list[_Entry]) -> list[_Entry]:
    """The entries after the newest prompt: what this turn has said and done."""
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].is_prompt:
            return entries[index + 1 :]
    return entries


def _prompt_marker(line: str) -> _Entry | None:
    try:
        prompt = _PromptEntry.model_validate_json(line)
    except ValidationError:  # silent-ok: not a prompt either; the line carries no report
        return None
    return _Entry(type=_USER, is_prompt=True) if prompt.type == _USER else None


def _entries(lines: Iterable[str]) -> list[_Entry]:
    entries = []
    for line in lines:
        try:
            entries.append(_Entry.model_validate_json(line))
        except ValidationError:
            # silent-ok: a line we do not model (summaries, metadata) carries no
            # report; a prompt line is kept only as the turn's boundary.
            marker = _prompt_marker(line)
            if marker is not None:
                entries.append(marker)
    return entries


def _calls_complete_stage(block: _Block) -> bool:
    if block.type != _TOOL_USE:
        return False
    if block.name.endswith(COMPLETE_STAGE_TOOL):
        return True
    return block.name == _BASH and COMPLETE_STAGE_TOOL in json.dumps(block.input)


def turn_report(hook_input: StopHookInput, transcript_lines: list[str]) -> StageReport | None:
    """The stage report this turn wrote in the agent's own text, if any.

    Read by the same parser completion uses, from the agent's own text only: a
    tool result that printed the contract's example is not a report. Read from
    the current turn only: a resumed session's earlier report answered an
    earlier run.
    """
    entries = _current_turn(_entries(transcript_lines))
    texts = [
        block.text
        for entry in entries
        if entry.type == _ASSISTANT
        for block in _blocks(entry)
        if block.type == _TEXT
    ]
    texts.append(hook_input.last_assistant_message or "")
    return parse_stage_report("\n".join(texts))


def _completed_by_tool(transcript_lines: list[str]) -> bool:
    """Whether this turn made a ``complete_stage`` call that did not error."""
    entries = _current_turn(_entries(transcript_lines))
    calls = {
        block.id
        for entry in entries
        if entry.type == _ASSISTANT
        for block in _blocks(entry)
        if _calls_complete_stage(block)
    }
    return any(
        block.type == _TOOL_RESULT and block.tool_use_id in calls and not block.is_error
        for entry in entries
        for block in _blocks(entry)
    )


def has_reported(hook_input: StopHookInput, transcript_lines: Iterable[str]) -> bool:
    """Whether this session has already delivered its stage outcome.

    The same two channels ``run_completion`` accepts: the sentinel block
    (`turn_report`), or a ``complete_stage`` call that did not error.
    """
    lines = list(transcript_lines)
    return turn_report(hook_input, lines) is not None or _completed_by_tool(lines)


def refusal_reason(hook_input: StopHookInput) -> str:
    """What the agent is told when its stop is refused."""
    running = [task for task in hook_input.background_tasks if task.status == _RUNNING]
    lead = (
        "This stage run has not reported its outcome, and ending your turn ends the "
        "run: the process exits and nothing will notify you afterwards."
    )
    if running:
        names = "; ".join(f"{task.id} ({task.command or task.description})" for task in running)
        lead += (
            f" Still running in the background: {names}. Wait for it now in the "
            "foreground and read its output, or stop it if it is a full-suite sweep "
            "and run only the tests your change reaches "
            "(agent_context/agents/common_assets/test_selection_v1.md)."
        )
    return f"{lead} Then {REPORT_BLOCK_REMINDER}"


def _counter_path(session_id: str) -> Path:
    key = f"{os.environ.get(RUN_ID_ENV, '')}:{session_id}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _STATE_DIR / f"{digest}.count"


def _record_block(session_id: str) -> int:
    """Count one more refusal for this run's session; return the new total."""
    path = _counter_path(session_id)
    try:
        count = int(path.read_text(encoding="utf-8")) + 1
    except (
        FileNotFoundError,
        ValueError,
    ):  # silent-ok: first refusal, or a torn write that restarts the count
        count = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(count), encoding="utf-8")
    return count


def _read_transcript(path: Path | None) -> list[str]:
    if path is None:
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"stage-report stop hook: cannot read {path}: {exc}", file=sys.stderr)
        return []


def _ledger_refusal(report: StageReport, db_path: Path | None) -> str | None:
    """Why this report cannot end the turn, by what the database says, or None.

    A pass from the settling stage waits for its criteria
    (`stage_report_criteria`); a reject waits for a test that fails
    (`stage_report_reject`). Fails open, saying why on stderr: a check that
    cannot run must never trap the run, and completion's own checks still
    catch what this one missed.
    """
    if db_path is None:
        return None
    run_id = os.environ.get(RUN_ID_ENV, "")
    if not run_id:
        print(f"stage-report stop hook: no {RUN_ID_ENV}; report not checked", file=sys.stderr)
        return None
    try:
        if report.status == StageReportStatus.PASS:
            return unsettled_refusal(db_path, run_id)
        return unshown_reject_refusal(db_path, run_id, report)
    except (SQLAlchemyError, OSError, LookupError, ValueError) as exc:
        print(f"stage-report stop hook: report not checked: {exc}", file=sys.stderr)
        return None


def decide(
    hook_input: StopHookInput, transcript_lines: Iterable[str], *, db_path: Path | None = None
) -> str | None:
    """The refusal reason, or None to let the turn end.

    A turn with no report is refused (`refusal_reason`). When the server named
    its database (``db_path``), so is a pass from the stage that settles
    criteria while one is unsettled, and a reject whose tests all pass
    (`_ledger_refusal`). All count toward ``MAX_BLOCKS``.
    """
    lines = list(transcript_lines)
    report = turn_report(hook_input, lines)
    if report is not None:
        reason = _ledger_refusal(report, db_path)
    elif _completed_by_tool(lines):
        reason = None
    else:
        reason = refusal_reason(hook_input)
    if reason is None or _record_block(hook_input.session_id) > MAX_BLOCKS:
        return None
    return reason


def _db_path(argv: list[str]) -> Path | None:
    """The ``--db <path>`` the server passed, or None when it passed none."""
    if DB_FLAG in argv[:-1]:
        return Path(argv[argv.index(DB_FLAG) + 1])
    return None


def main(stdin_text: str, argv: list[str] | None = None) -> str:
    """The hook's stdout for ``stdin_text``: a block decision, or "" to allow."""
    try:
        hook_input = StopHookInput.model_validate_json(stdin_text)
    except ValidationError as exc:
        # Fail open: refusing on input we cannot read could trap the run until
        # its timeout. The missing report still blocks the stage visibly.
        print(f"stage-report stop hook: unreadable hook input: {exc}", file=sys.stderr)
        return ""
    reason = decide(
        hook_input,
        _read_transcript(hook_input.transcript_path),
        db_path=_db_path(argv or []),
    )
    if reason is None:
        return ""
    return json.dumps({"decision": "block", "reason": reason})


if __name__ == "__main__":
    sys.stdout.write(main(sys.stdin.read(), sys.argv[1:]))
