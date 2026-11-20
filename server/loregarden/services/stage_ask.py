"""A running stage asks an earlier stage on its ticket one question.

Each stage run starts cold. One that needs a fact an earlier stage found, or the
reason behind a decision it made, re-derives it: stages make 17 to 44 tool calls
before their first write and read upstream artifacts once or twice. The earlier
stage's session still holds that knowledge, and resuming it is cheap while its
prompt cache is warm. On the live database (stage runs since 2026-10-20), 487
runs had an earlier claude stage they could ask, and 93.8% of them started
within an hour of the nearest one finishing.

So `loregarden_ask_earlier_stage` resumes that session for one turn:

- The target is the latest successful orchestrated run of the named stage on the
  asker's ticket (`stage_digest.earlier_stage_runs`). It must have run on claude
  and recorded its session.
- The turn forks the session (``--fork-session``), so the earlier run's own
  transcript is never appended to. It runs under the earlier run's kept system
  prompt (`session_prompt`) and model, in the directory it ran in: claude finds
  a session by its project directory.
- It may only read (`cli_adapters.claude_stage_ask_invocation`), within a dollar
  budget and a timeout.
- It is recorded as its own run: `resumed_from_run_id` names the target,
  `asked_by_run_id` the asker, and its usage is recorded like any run's. It is
  not orchestrated, and its stage key is no workflow stage's, so the digest,
  rework resume and stage retry budgets never take it for stage work.

Judging stages (the review lenses, the gate, verify; `finding_bar.is_judging`)
are refused outright. Every stage before a judge on a ticket is a producer, and
a judge that asks the implementer why it did something has become its
audience: the reason `stage_digest` withholds producers' notes from judges. A
judge asking another judge would trade the lenses' independence for agreement.
Nothing is left a judge could ask without one of those.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.cli_adapters import (
    CliInvocation,
    claude_stage_ask_invocation,
    invocation_env,
    ran_on_claude,
)
from loregarden.agents.executors.run_evidence import record_usage
from loregarden.agents.stage_digest import earlier_stage_runs
from loregarden.models.domain import AgentRun, RunStatus, Ticket
from loregarden.models.domain.enums import utcnow
from loregarden.services.finding_bar import is_judging
from loregarden.services.rework_resume import record_cli_session, session_id_of
from loregarden.services.run_concurrency import new_run_code
from loregarden.services.run_failure import set_run_status
from loregarden.services.session_prompt import read_session_prompt, session_prompt_path
from loregarden.services.stage_report import result_envelope
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, func, select

logger = logging.getLogger(__name__)

#: The asked turn's wall-clock limit. A turn that answers from memory takes
#: seconds; one that reads a few files, a minute or two.
ASK_TIMEOUT_SECONDS = 300
#: The asked turn's spend limit (claude ``--max-budget-usd``). A cold resume of a
#: long Opus session re-caches its history, which alone can cost a dollar.
ASK_MAX_BUDGET_USD = "2.00"
#: Asks one stage run may make. An agent that keeps asking is exploring by proxy.
MAX_ASKS_PER_RUN = 3
MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 8000
#: Appended to the asked stage's key to name an ask run's stage. No workflow
#: stage key contains it, so no query for a stage's runs returns an ask.
ASK_STAGE_SUFFIX = ":ask"


class StageAskRefused(ValueError):
    """The ask cannot be made, and why, in words the asking agent can act on."""


@dataclass(frozen=True)
class StageAnswer:
    ask_run: AgentRun
    target: AgentRun
    answer: str
    #: False when the target's system prompt was not kept, so the turn ran
    #: without it and re-cached the session's history.
    prompt_kept: bool


def ask_earlier_stage(
    session: Session, asker: AgentRun, ticket: Ticket, stage_key: str, question: str
) -> StageAnswer:
    """Ask `stage_key`'s latest run on `ticket` `question` on `asker`'s behalf."""
    question = question.strip()
    target = resolve_target(session, asker, ticket, stage_key, question)
    session_id = session_id_of(target)
    kept_prompt = bool(read_session_prompt(target))
    invocation = claude_stage_ask_invocation(
        session_id=session_id,
        prompt=_turn_prompt(asker, target, question),
        cwd=Path(target.start_repo_path),
        system_prompt_file=session_prompt_path(target) if kept_prompt else None,
        model=target.model or "",
        max_budget_usd=ASK_MAX_BUDGET_USD,
    )
    ask_run = _open_ask_run(session, asker, target, ticket, invocation)
    answer = _run_turn(session, ask_run, invocation)
    return StageAnswer(ask_run=ask_run, target=target, answer=answer, prompt_kept=kept_prompt)


def resolve_target(
    session: Session, asker: AgentRun, ticket: Ticket, stage_key: str, question: str
) -> AgentRun:
    """The run `asker` may ask, or `StageAskRefused` naming why there is none."""
    if asker.ticket_id != ticket.id:
        raise StageAskRefused(
            f"run {asker.run_code} belongs to another ticket; a stage asks earlier stages "
            "of its own ticket only"
        )
    if not question:
        raise StageAskRefused("the question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise StageAskRefused(f"ask one question, under {MAX_QUESTION_CHARS} characters")
    if stage_key == asker.stage_key:
        raise StageAskRefused(
            f"'{stage_key}' is your own stage; ask an earlier stage, or read your own "
            "stage's artifacts"
        )
    _refuse_judging(session, ticket, asker)
    asked = session.exec(
        select(func.count()).select_from(AgentRun).where(AgentRun.asked_by_run_id == asker.id)
    ).one()
    if asked >= MAX_ASKS_PER_RUN:
        raise StageAskRefused(
            f"this run has asked {asked} questions, the most one run may; work from the "
            "answers and the digest of earlier stages"
        )
    earlier = earlier_stage_runs(session, ticket, asker)
    target = next((run for run in earlier if run.stage_key == stage_key), None)
    if target is None:
        named = ", ".join(sorted(run.stage_key for run in earlier)) or "none"
        raise StageAskRefused(
            f"no earlier stage '{stage_key}' succeeded on {ticket.external_id}; stages you "
            f"can ask: {named}"
        )
    return _refuse_unresumable(target)


def _refuse_judging(session: Session, ticket: Ticket, asker: AgentRun) -> None:
    _, stages = resolve_ticket_stages(session, ticket)
    stage = next((stage for stage in stages if stage.key == asker.stage_key), None)
    if is_judging(asker.agent_id, stage):
        raise StageAskRefused(
            f"'{asker.stage_key}' is a judging stage: it checks the earlier stages' work, "
            "so it does not ask them about it. Judge from the change, the tests and the "
            "artifacts."
        )


def _refuse_unresumable(target: AgentRun) -> AgentRun:
    if not ran_on_claude(target.command):
        raise StageAskRefused(
            f"{target.stage_key} ran as {target.run_code} on another adapter than claude; "
            "only a claude session can be asked. Read its artifacts and notes instead."
        )
    if not session_id_of(target):
        raise StageAskRefused(
            f"{target.stage_key}'s run {target.run_code} recorded no session to resume; read "
            "its artifacts and notes instead"
        )
    if not target.start_repo_path or not Path(target.start_repo_path).is_dir():
        where = target.start_repo_path or "an unrecorded directory"
        raise StageAskRefused(
            f"{target.stage_key}'s run {target.run_code} ran in {where}, which is gone; its "
            "session can only be resumed there"
        )
    return target


def _turn_prompt(asker: AgentRun, target: AgentRun, question: str) -> str:
    return (
        f"A later stage on this ticket ({asker.stage_key}, run {asker.run_code}) asks you, "
        f"the {target.stage_key} stage, one question. Answer it from what you found and "
        "decided in this session. You may read files to check a detail; change nothing, do "
        "not continue your stage's work, and emit no stage report. Name files and symbols "
        "where they help. If you do not know, say so rather than guess. Keep it under 300 "
        f"words.\n\nQuestion: {question}"
    )


def _open_ask_run(
    session: Session,
    asker: AgentRun,
    target: AgentRun,
    ticket: Ticket,
    invocation: CliInvocation,
) -> AgentRun:
    now = utcnow()
    run = AgentRun(
        run_code=new_run_code(),
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=target.agent_id,
        stage_key=f"{target.stage_key}{ASK_STAGE_SUFFIX}",
        status=RunStatus.RUNNING,
        command=" ".join(invocation.argv),
        start_repo_path=target.start_repo_path,
        resumed_from_run_id=target.id,
        asked_by_run_id=asker.id,
        started_at=now,
        last_seen_at=now,
    )
    session.add(run)
    # Committed before the turn: nothing holds the database while it runs.
    session.commit()
    session.refresh(run)
    return run


def _run_turn(session: Session, run: AgentRun, invocation: CliInvocation) -> str:
    """Spawn the asked turn and record it; its answer, or `StageAskRefused` on failure."""
    try:
        done = subprocess.run(
            invocation.argv,
            cwd=invocation.cwd,
            env=invocation_env(invocation),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=ASK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        reason = f"the asked turn timed out after {ASK_TIMEOUT_SECONDS}s"
        _fail(session, run, reason)
        raise StageAskRefused(f"the earlier stage gave no answer ({reason})") from exc
    except OSError as exc:
        _fail(session, run, f"the asked turn could not start: {exc}")
        raise StageAskRefused(f"the earlier stage could not be asked: {exc}") from exc
    run.stdout = done.stdout
    run.stderr = done.stderr
    record_usage(session, run, stdout=done.stdout, invocation=invocation)
    record_cli_session(session, run, done.stdout)
    envelope = result_envelope(done.stdout)
    answer = envelope.result.strip() if envelope is not None else ""
    if done.returncode != 0 or envelope is None or envelope.is_error or not answer:
        detail = answer or done.stderr.strip()[-400:] or f"exit {done.returncode}, no answer"
        _fail(session, run, f"the asked turn failed: {detail}")
        raise StageAskRefused(f"the earlier stage gave no answer ({detail})")
    set_run_status(run, RunStatus.SUCCEEDED)
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    if len(answer) > MAX_ANSWER_CHARS:
        answer = answer[:MAX_ANSWER_CHARS].rstrip() + "\n… (answer truncated)"
    return answer


def _fail(session: Session, run: AgentRun, reason: str) -> None:
    logger.warning("Ask %s of run %s: %s", run.run_code, run.resumed_from_run_id, reason)
    set_run_status(run, RunStatus.FAILED, failure_text=reason)
    run.stderr = "\n".join(part for part in (run.stderr, reason) if part)
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
