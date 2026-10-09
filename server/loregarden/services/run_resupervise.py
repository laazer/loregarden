"""Finish a run whose agent outlived the server that started it.

`run_reattach` adopts a surviving run and spares it from the boot reapers. That
is not a resume: nothing read the detached agent's exit status or its
`<<<LOREGARDEN_STAGE_REPORT>>>`, so the stage never completed, committed or
routed — it settled at the lease boundary as un-supervised. 82 runs in the live
database failed with "interrupted before completion (server reload…)"; this is
what converts that population into survivors.

So this module is the other half of the watcher that used to live in
`run_reattach`: drain the output file from the byte the last process got to,
renew the lease while the agent lives, and on GONE settle through
`complete_run` — the same floor `execute()` and `finish_external_stage` already
converge on. It is here rather than there because `run_reattach` sits on the
boot-reaper path, and putting `OrchestrationService` behind its predicates
would drag orchestration onto that path.

Three invariants, each with a failure mode worse than the one it prevents:

* **It never calls `finish_external_stage`.** An adopted run has
  `external_harness=None` and keeps it. Routing a local run through the
  external protocol would be a second settlement path with its own bugs.
* **Settlement happens once.** The trigger is the original server coming back,
  which *overlaps* two settlers rather than ordering them — so the claim is a
  single conditional UPDATE, not a read followed by a write. Two settlements
  means two commits of the whole working tree into one checkout.
* **An adopted run is exempt from the host capacity lease, and says so.**
  Re-acquiring would queue a run behind work it is already ahead of, which is
  `external_harness`'s existing "no queue" rule. A silent exemption is not
  acceptable, so the exemption is logged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loregarden.agents.cli_adapters import CliInvocation
from loregarden.agents.executors.run_evidence import (
    record_run_evidence,
    run_context_artifacts,
    run_paths_before,
)
from loregarden.agents.registry import get_agent
from loregarden.db import session as db_session_module
from loregarden.dot_line import SYS
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    ProcessState,
    RunStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.parallel_stage import member_runs_and_results, reconcile_parallel_stage
from loregarden.services.process_identity import liveness
from loregarden.services.run_lease import (
    RENEWAL_INTERVAL_SECONDS,
    SUPERVISED,
    renew_agent_run_lease,
)
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.run_output_files import (
    RunOutputPaths,
    RunOutputTail,
    paths_for,
    read_output_text,
    recorded_exit_status,
)
from loregarden.services.studio_routing import is_parallel_stage, ticket_stage_definition
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, update

logger = logging.getLogger(__name__)

#: What an operator reads when the agent left no exit code behind. A run whose
#: `.rc` is absent or unparseable is reported FAILED rather than assumed
#: complete — that is the honest outcome of tmux's own server dying, and the
#: reason tmux is sequenced last in this ticket.
NO_EXIT_CODE_STDERR = (
    "Agent exited without recording an exit code; its run is reported failed "
    "rather than assumed complete."
)


@dataclass(frozen=True)
class _Adopted:
    """What supervising an adopted run needs, read once off its row.

    A value rather than the ORM object, so the loop below holds no session. The
    run it is watching may have hours left — the longest in the live database
    ran 6,451 minutes — and a `Session` kept open across that holds a pooled
    connection and an idle SQLite read transaction the whole time, which is how
    every other writer starts losing "database is locked" races
    (lg-workflow-integrity-687). The streamer opens its own short session per
    flush, and `_settle` opens one at the end.
    """

    run_id: str
    run_code: str
    ticket_id: str | None
    agent_id: str
    skill_name: str
    pid: int | None
    identity: str


def resupervise(run_id: str, *, interval_seconds: float = RENEWAL_INTERVAL_SECONDS) -> None:
    """Tail, renew and finally settle an adopted run. Runs as its daemon thread."""
    adopted = _adopted(run_id)
    if adopted is None:
        return
    logger.info(
        "Resupervising run %s (ticket %s, pid %s): exempt from the host capacity lease, "
        "because it is already ahead of the queue it would be joining",
        adopted.run_code,
        adopted.ticket_id,
        adopted.pid,
    )
    paths = paths_for(adopted.run_code, adopted.run_id)
    streamer = _hydrated_streamer(adopted)
    tail = RunOutputTail(paths.out, start_offset=streamer.tail_offset)
    _announce_resume(streamer, offset=streamer.tail_offset)
    _supervise_until_gone(
        run_id=adopted.run_id,
        pid=adopted.pid,
        identity=adopted.identity,
        tail=tail,
        streamer=streamer,
        interval_seconds=interval_seconds,
    )
    _drain_to_end(tail, streamer)
    _settle(adopted.run_id, paths=paths)


def _adopted(run_id: str) -> _Adopted | None:
    """The run's supervising facts, or None when there is nothing to resume."""
    with Session(db_session_module.engine) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            logger.warning("Cannot resupervise run %s: no such row", run_id)
            return None
        if run.status not in SUPERVISED:
            logger.info(
                "Run %s is already %s; nothing to resupervise", run.run_code, run.status.value
            )
            return None
        return _Adopted(
            run_id=run.id,
            run_code=run.run_code,
            ticket_id=run.ticket_id,
            agent_id=run.agent_id,
            skill_name=run.skill_name or "",
            pid=run.agent_pid,
            identity=run.agent_pid_identity,
        )


def _hydrated_streamer(adopted: _Adopted) -> RunLogStreamer:
    """This run's log as the process that died left it, offset included."""
    streamer = RunLogStreamer(
        run_id=adopted.run_id,
        ticket_id=adopted.ticket_id,
        run_code=adopted.run_code,
        agent_id=adopted.agent_id,
        skill_name=adopted.skill_name,
    )
    streamer._hydrate()
    return streamer


def _announce_resume(streamer: RunLogStreamer, *, offset: int) -> None:
    """One SYS line, before the first drain, at the byte the tailer resumed from.

    A line rather than a banner: a 6,451-minute run can survive two restarts and
    a banner can only describe one. Written once per reattachment — inside the
    beat instead, a run that lives through twenty beats gets twenty markers and
    the operator concludes the control plane restarted twenty times.
    """
    streamer.append(
        SYS.name, f"reattached · control-plane restart, resumed at byte {offset}", force=True
    )


def _supervise_until_gone(
    *,
    run_id: str,
    pid: int | None,
    identity: str,
    tail: RunOutputTail,
    streamer: RunLogStreamer,
    interval_seconds: float,
) -> None:
    """Drain and renew, beat by beat, until `ps` says the process is gone.

    The loop may only leave on GONE. An unanswered `ps` is not a death — reading
    one as GONE is how a slow boot got a live agent's run failed
    (lg-run-durability-862) — so an UNKNOWN beat skips the *renewal*, which would
    otherwise vouch for a process nothing vouched for. It never skips the drain:
    an unanswered `ps` says nothing about what the agent wrote, and a transcript
    with a hole in it is worse than one that is merely late.
    """
    while True:
        _drain_available(tail, streamer)
        state = liveness(pid, identity)
        if state is ProcessState.GONE:
            return
        if state is ProcessState.ALIVE:
            renew_agent_run_lease(run_id)
        _sleep(interval_seconds)


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _drain_available(tail: RunOutputTail, streamer: RunLogStreamer) -> None:
    """Every complete line the file holds right now, and nothing partial."""
    drained = False
    while (line := tail.readline(timeout=0)) is not None:
        # Set before the append — see `RunLogStreamer.tail_offset`: the append
        # can persist, and the offset written in that transaction must be the
        # one just past the row it goes with.
        streamer.tail_offset = tail.offset
        streamer.append_stream_line(line.rstrip("\n"))
        drained = True
    if drained:
        # Persist the offset with the rows it points past, in this beat rather
        # than only at settlement: a restart that lands while the agent is still
        # working is the only case this module exists for, and an offset written
        # once at the end is no use to it.
        streamer.touch()


def _drain_to_end(tail: RunOutputTail, streamer: RunLogStreamer) -> None:
    """The final drain: the writer is gone, so the file is complete.

    `flush_partial` is legal here and only here. An agent killed one line in has
    that unterminated line as the only evidence of what it was doing, and a
    drain that skipped the call whenever nothing terminated was read would lose
    exactly that case.
    """
    _drain_available(tail, streamer)
    remainder = tail.flush_partial()
    if remainder is None:
        return
    streamer.tail_offset = tail.offset
    streamer.append_stream_line(remainder.rstrip("\n"))
    streamer.touch()


def _claim_settlement(session: Session, run_id: str) -> bool:
    """Take sole ownership of settling `run_id`, or report that someone else has.

    One conditional UPDATE, because the race this guards is two *processes* —
    the original server came back and started its own resupervisor. A read
    followed by a write would let both pass. `finished_at` is the claim: a
    supervised run has none, and `complete_run` sets it anyway.
    """
    result = session.execute(
        update(AgentRun)
        .where(
            col(AgentRun.id) == run_id,
            col(AgentRun.finished_at).is_(None),
            col(AgentRun.status).in_(list(SUPERVISED)),
        )
        .values(finished_at=datetime.now(timezone.utc))
    )
    session.commit()
    return bool(result.rowcount == 1)


def _settle(run_id: str, *, paths: RunOutputPaths) -> None:
    """Record how the run ended, route its stage, and delete its output files."""
    with Session(db_session_module.engine) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            return
        if not _claim_settlement(session, run_id):
            logger.info("Run %s was settled by someone else; not settling it again", run.run_code)
            return
        session.refresh(run)
        code = recorded_exit_status(paths.rc)
        status = RunStatus.SUCCEEDED if code == 0 else RunStatus.FAILED
        stderr = _stderr_for(paths, code=code)
        stdout = _transcript(paths)
        _finalize_log(run, status=status, stderr=stderr)
        try:
            _complete(session, run, status=status, stdout=stdout, stderr=stderr)
        except Exception:
            # A settlement that raised has not settled anything: the stage never
            # routed and the worktree was never committed. Reported at
            # `exception` and re-raised rather than swallowed — a row reading
            # SUCCEEDED with none of that done is this control plane's
            # characteristic bug.
            logger.exception("Could not settle reattached run %s", run.run_code)
            raise
    _delete_output_files(paths)


def _stderr_for(paths: RunOutputPaths, *, code: int | None) -> str:
    stderr = read_output_text(paths.err)
    if code is not None:
        return stderr
    return f"{stderr}\n{NO_EXIT_CODE_STDERR}".strip() if stderr else NO_EXIT_CODE_STDERR


def _transcript(paths: RunOutputPaths) -> str:
    """The agent's raw stdout — the whole file, not this process's share of it.

    The whole file because the lines before the restart were read by a process
    that is gone, and the file is the only place they both still exist.

    RAW, not the log rows: `RunLogStreamer` renders output for a reader, and
    rendering is lossy in exactly the way that matters here. A stage report's
    body is a JSON object, so `append_stream_line` parses it as a stream event,
    finds no formatter that claims it, and drops it — the stage then routes as
    if the agent never reported. `parse_stage_report` and the usage parser both
    want what the live path hands them, which is this.
    """
    return read_output_text(paths.out)


def _finalize_log(run: AgentRun, *, status: RunStatus, stderr: str) -> None:
    streamer = RunLogStreamer(
        run_id=run.id,
        ticket_id=run.ticket_id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name=run.skill_name or "",
    )
    streamer._hydrate()
    streamer.finalize(status=status, stderr=stderr)


def _complete(
    session: Session, run: AgentRun, *, status: RunStatus, stdout: str, stderr: str
) -> None:
    ticket = session.get(Ticket, run.ticket_id) if run.ticket_id else None
    if ticket is None:
        # A workspace-scoped run has no stage to route. Its terminal status is
        # still the record that it ended, so it is written rather than left at
        # RUNNING for the lease reaper to guess at.
        run.status = status
        run.stderr = stderr
        session.add(run)
        session.commit()
        return
    _record_evidence(session, run, ticket, stdout=stdout)
    stage_def = ticket_stage_definition(session, ticket, run.stage_key)
    parallel = stage_def is not None and is_parallel_stage(stage_def)
    OrchestrationService(session).complete_run(
        run,
        status=status,
        stdout=stdout,
        stderr=stderr,
        artifacts=run_context_artifacts(ticket, run, status),
        advance_workflow=not parallel,
    )
    if parallel and stage_def is not None:
        _reconcile_member(session, run, ticket, stage_def)


def _reconcile_member(
    session: Session, run: AgentRun, ticket: Ticket, stage_def: WorkflowStageDef
) -> None:
    """Settle the parallel stage once this was its last outstanding member.

    Through the one helper both drivers use (`parallel_stage`), not a third
    copy: a reattach WILL land on a parallel stage — this ticket's own `plan`
    stage has three members.
    """
    outstanding, results = member_runs_and_results(session, ticket, stage_def, run.stage_key)
    if outstanding:
        return
    orch_run = _orchestration_run(session, run)
    if orch_run is None:
        logger.warning(
            "Run %s finished a parallel stage with no orchestration run, so the stage "
            "cannot be settled here; re-run the stage to finalize it",
            run.run_code,
        )
        return
    reconcile_parallel_stage(session, ticket, orch_run, run.stage_key, results)


def _orchestration_run(session: Session, run: AgentRun) -> OrchestrationRun | None:
    if not run.orchestration_run_id:
        return None
    return session.get(OrchestrationRun, run.orchestration_run_id)


def _record_evidence(session: Session, run: AgentRun, ticket: Ticket, *, stdout: str) -> None:
    """What this run touched, read and consumed — from the row, not the tree.

    `run_paths_before` is the one answer to what the run inherited: a process
    reattaching hours later cannot take the live `TreeSnapshot` the dispatch
    path used to take, and the run's own boundary column already holds that set.
    """
    repo_root = _execution_root(session, run, ticket)
    if repo_root is None:
        logger.warning(
            "Could not resolve where run %s executed; its git evidence is not recorded",
            run.run_code,
        )
        return
    record_run_evidence(
        session,
        run,
        repo_root=repo_root,
        paths_before=run_paths_before(run),
        stdout=stdout,
        invocation=_reconstructed_invocation(run),
    )


def _execution_root(session: Session, run: AgentRun, ticket: Ticket) -> Path | None:
    """The tree the run actually executed in, as the run itself recorded it.

    `start_repo_path` is written at dispatch from the same read that stamped the
    git boundary, so it names the worktree the agent saw. Nothing is created
    here: settling a finished run must not cut a worktree.
    """
    if run.start_repo_path:
        return Path(run.start_repo_path)
    workspace = session.get(Workspace, run.workspace_id)
    return resolve_workspace_root(workspace) if workspace else None


def _reconstructed_invocation(run: AgentRun) -> CliInvocation:
    """Enough of the invocation for the usage parser, rebuilt from the row.

    The real one died with the process that built it. Only the adapter, model
    and effort are read downstream, and all three are recoverable: the adapter
    from the agent's registry entry, the other two from what the run already
    recorded. Anything the transcript does not carry stays unmeasured, which is
    what a reattached run honestly is.
    """
    adapter = (get_agent(run.agent_id) or {}).get("adapter", "local")
    return CliInvocation(
        argv=[],
        adapter=adapter,
        model=run.model or "",
        effort=run.effort or "",
    )


def _delete_output_files(paths: RunOutputPaths) -> None:
    """The three files, now that the transcript is durable in `run_log_lines`.

    Safe only in that order: deleting them while the rows were still unwritten
    would destroy the only copy. Orphans — a run whose row was never settled —
    are swept at boot by the existing `worktree_lifecycle.reconcile_worktrees`;
    there is no second deletion policy here.
    """
    for path in (paths.out, paths.err, paths.rc):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete %s; it is now an orphan to sweep", path)
