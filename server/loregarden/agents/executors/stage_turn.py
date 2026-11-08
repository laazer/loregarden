"""One stage-run agent turn: through the permission bridge, or detached in print mode."""

from __future__ import annotations

from pathlib import Path

from loregarden.agents.cli_adapters import CliInvocation
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.agents.executors.report_resume import resume_for_missing_report
from loregarden.models.domain import AgentRun, RunStatus, Ticket
from loregarden.services.run_log_stream import RunLogStreamer
from sqlmodel import Session


def spawn_stage_turn(
    session: Session,
    run: AgentRun,
    ticket: Ticket,
    invocation: CliInvocation,
    *,
    prompt: str,
    stem: str,
    repo_root: Path,
    timeout: int,
    streamer: RunLogStreamer,
) -> tuple[str, str, RunStatus]:
    """Spawn `invocation` for `run` and return its stdout, stderr and status.

    `stem` names a print-mode turn's `.out`/`.rc` files: the run's code, or a
    suffixed one for a second process in the same run (`rework_round`).
    """
    if invocation.interactive:
        result = PermissionBridgeRunner(session).run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt=prompt,
            timeout_seconds=timeout,
            streamer=streamer,
        )
        return result.stdout, result.stderr, result.status
    # The transport is resolved by whoever performs the spawn, and announced
    # from the answer that spawn returned — see `print_mode.run_print_mode`.
    # Resolved here as well it was two independent readings of one fact, and
    # the SYS line was written BEFORE the spawn, so a tmux spawn that raised
    # left a line in the feed naming a session that never existed.
    stdout, stderr, status = run_print_mode(
        invocation=invocation,
        repo_root=repo_root,
        timeout=timeout,
        streamer=streamer,
        run_id=run.id,
        run_code=stem,
    )
    # One resume when the turn ended with no report (848).
    return resume_for_missing_report(
        invocation=invocation,
        run=run,
        stdout=stdout,
        stderr=stderr,
        status=status,
        repo_root=repo_root,
        timeout=timeout,
        streamer=streamer,
    )
