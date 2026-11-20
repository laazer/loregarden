"""What earlier stages on this ticket found, put in front of the stage after them.

Each stage run starts cold. Over runs since 2026-11-12 a stage made 17 to 44 tool
calls (median by stage) before its first write, nearly all of it shell
exploration of code an earlier stage on the same ticket had already found. The
plan artifact (`plan_context`) carries decisions, not locations; the transcripts
that hold the locations are read by nothing downstream.

So each earlier stage's latest successful run contributes:

- its `notes_for_next_stage` (`AgentRun.next_stage_notes`): the files, symbols
  and traps it says the next stage would otherwise rediscover;
- the files it changed (`changed_paths_json`) and read (`read_paths_json`,
  which includes files its shell commands named).

Judging stages (review, gate) get the files but not the notes: an implementer's
account of its own work is what they are there to check, and reading it first
makes them its audience. A verify stage gets nothing, for the stronger reason
`_build_prompt` already withholds inherited context from it.
"""

from __future__ import annotations

import json
import logging

from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkflowStageDef
from loregarden.services.finding_bar import is_judging
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Files listed per stage, per list. Past this the list stops being a map.
MAX_FILES_PER_LIST = 20
#: The whole block. A digest bigger than this is a second prompt.
MAX_DIGEST_CHARS = 6000


def build_stage_digest(
    session: Session, ticket: Ticket, run: AgentRun, *, include_notes: bool
) -> str:
    """The digest of earlier stages for `run`, or "" when there is nothing to show."""
    sections = [
        _section(prior, include_notes=include_notes)
        for prior in earlier_stage_runs(session, ticket, run)
    ]
    text = "\n\n".join(section for section in sections if section)
    if len(text) > MAX_DIGEST_CHARS:
        text = text[:MAX_DIGEST_CHARS].rstrip() + "\n… (digest truncated)"
    return text


def stage_digest_for(
    session: Session,
    ticket: Ticket,
    run: AgentRun,
    stage_def: WorkflowStageDef | None,
    *,
    is_verify: bool,
) -> str:
    """The digest this stage's prompt carries: none for a verifier, no notes for a judge."""
    if is_verify:
        return ""
    return build_stage_digest(
        session, ticket, run, include_notes=not is_judging(run.agent_id, stage_def)
    )


def earlier_stage_runs(session: Session, ticket: Ticket, run: AgentRun) -> list[AgentRun]:
    """The latest successful orchestrated run of each other stage, oldest first.

    Orchestrated only: a chat turn (Baxter) is recorded against the ticket's
    current stage too, and is not stage work.
    """
    rows = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .where(AgentRun.id != run.id)
        .where(AgentRun.stage_key != run.stage_key)
        .where(AgentRun.status == RunStatus.SUCCEEDED)
        .where(col(AgentRun.orchestration_run_id).is_not(None))
        .where(col(AgentRun.created_at) <= run.created_at)
        .order_by(col(AgentRun.created_at).desc())
    ).all()
    latest: dict[str, AgentRun] = {}
    for prior in rows:
        latest.setdefault(prior.stage_key, prior)
    return sorted(latest.values(), key=lambda prior: prior.created_at)


def _section(prior: AgentRun, *, include_notes: bool) -> str:
    changed = _paths(prior, prior.changed_paths_json)
    changed_set = set(changed)
    read = [path for path in _paths(prior, prior.read_paths_json) if path not in changed_set]
    notes = prior.next_stage_notes.strip() if include_notes else ""
    if not (notes or changed or read):
        return ""
    lines = [f"### {prior.stage_key} ({prior.agent_id}, {prior.run_code})"]
    if notes:
        lines.append(notes)
    if changed:
        lines.append(f"- Changed: {_listed(changed)}")
    if read:
        lines.append(f"- Read: {_listed(read)}")
    return "\n".join(lines)


def _paths(prior: AgentRun, raw: str | None) -> list[str]:
    try:
        paths: list[str] = json.loads(raw or "[]")
    except ValueError:
        # The digest is a shortcut, not a source of truth: a stage that loses
        # this list explores as it did before it existed.
        logger.warning(
            "run %s holds an unreadable path list; omitted from the digest", prior.run_code
        )
        return []
    return paths


def _listed(paths: list[str]) -> str:
    shown = ", ".join(f"`{path}`" for path in paths[:MAX_FILES_PER_LIST])
    more = len(paths) - MAX_FILES_PER_LIST
    return shown + (f" (+{more} more)" if more > 0 else "")
