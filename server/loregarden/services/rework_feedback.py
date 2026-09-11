"""Durable rework-feedback ledger for stage reroutes.

When a stage rejects work and reroutes it upstream, the *full* fix direction
must reach the re-run agent. It could not before: the single-stage reroute path
funnelled the agent's ``reroute_context`` through ``record_blocking_issue``,
which caps ``ticket.blocking_issues`` at 200 chars and files the overflow as an
error artifact the re-run agent never reads (``build_orchestration_context``
reads only ``ticket.blocking_issues``). So a verifier could hand back a precise
fix direction and the implementer would receive only
``"Stage 'verify' hit a blocking issue — see the Errors tab for details."`` —
and re-guess the fix every round.

This ledger stores each reroute's *full* context as its own artifact so:

* the re-run agent sees every prior round's feedback in full — no truncation,
  and the union across rounds rather than only the latest framing;
* a runaway reroute loop can be capped durably. The count is artifact-backed,
  so it survives a server reload or a fresh orchestration run — unlike a
  function-local counter, which resets every ``execute()`` and lets a stuck
  stage cycle forever (the same trap ``_persisted_gate_fix_attempts`` documents
  for the gate path).

``ticket.blocking_issues`` is left untouched: it stays the short pointer the
workflow pane renders. This ledger is a separate, agent-facing channel.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ReworkArtifactKind,
    ReworkStopReason,
    Ticket,
    Workspace,
)
from loregarden.services.git_commit_push_service import head_commit_sha
from loregarden.services.workspace_paths import resolve_run_root, resolve_workspace_root
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

# Feedback context, not a failure — kept off the Errors tab. The re-run agent's
# context is assembled from these; the human-facing error artifact that
# ``record_blocking_issue`` already files is a separate, unchanged concern.
#
# A dedicated kind, not the shared ``context`` bucket it used until migration
# 0103 backfilled these rows. Both readers below filter on it, so it is the one
# seam between what this module writes and what it counts.
REWORK_FEEDBACK_KIND = ReworkArtifactKind.FEEDBACK

# The marker a deliberate operator decision writes to give a target stage its
# reroute budget back. Its own kind so the FEEDBACK rows survive: those are what
# the re-run agent reads, and a reset that deleted them would clear the budget by
# throwing away the findings the next round is supposed to act on.
REWORK_BUDGET_RESET_KIND = ReworkArtifactKind.BUDGET_RESET

# Reroute a single target stage this many times without the work sticking, and
# the next rejection blocks for a human instead of bouncing again. Mirrors the
# gate autofix budget (``GatesConfig.autofix_max_agent_attempts``) but for the
# review/verify rework loop, which previously had no cap at all.
MAX_REWORK_REROUTES = 3


def _ledger_title(target_stage: str) -> str:
    """Deterministic title so the ledger for one target is countable and
    distinct from unrelated ``context`` artifacts."""
    return f"Rework feedback — {target_stage}"


def _tree_sha(session: Session, ticket: Ticket, run_id: str | None) -> str:
    """HEAD of the checkout the run actually executed in.

    Resolved through the RUN, not the workspace. `resolve_head_sha` answers for
    `workspace.repo_path` — the shared checkout — and `GitAutomationConfig`
    defaults `worktree` to True, so a ticket's commits normally land in a
    per-ticket worktree that the shared checkout never sees. Stamping the shared
    HEAD would have compared a repository the run never wrote to: two rounds
    would read as "the same tree" while the ticket's worktree advanced between
    them, which is a false STUCK, and the signal would be answering a question
    about the wrong repo entirely.

    `resolve_run_root` already falls back to the workspace checkout for a run
    with no worktree, or one whose directory has been cleaned up.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        return ""
    root = resolve_workspace_root(workspace)
    if run_id:
        run = session.get(AgentRun, run_id)
        if run:
            root = resolve_run_root(session, run, root)
    return head_commit_sha(root)


def record_rework_feedback(
    session: Session,
    ticket: Ticket,
    *,
    target_stage: str,
    from_stage: str,
    context: str,
    run_id: str | None = None,
) -> None:
    """Append one round of rework feedback for ``target_stage``.

    ``context`` is stored verbatim (no truncation) — this is what the re-run
    agent reads. One artifact per reroute, so the row count doubles as the
    durable reroute budget for the loop cap.

    ``commit_sha`` records the tree the finding was raised against, which is what
    makes convergence answerable: a round that repeats the same finding against
    the same tree cannot differ from the one before it. The field already existed
    on ``Artifact``; the ledger simply left it empty. It is resolved through the
    RUN rather than the workspace — see ``_tree_sha``, and note that a ticket
    normally executes in its own worktree.
    """
    session.add(
        Artifact(
            ticket_id=ticket.id,
            run_id=run_id,
            kind=REWORK_FEEDBACK_KIND,
            title=_ledger_title(target_stage),
            commit_sha=_tree_sha(session, ticket, run_id),
            content_json=json.dumps(
                {"from_stage": from_stage, "target_stage": target_stage, "context": context}
            ),
        )
    )
    session.commit()


def _entries(session: Session, ticket: Ticket, target_stage: str) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket.id)
            .where(Artifact.kind == REWORK_FEEDBACK_KIND)
            .where(Artifact.title == _ledger_title(target_stage))
            .order_by(Artifact.created_at)
        ).all()
    )


def _newest_reset_at(session: Session, ticket: Ticket, target_stage: str) -> datetime | None:
    """When this target's reroute budget was last given back, if it ever was."""
    marker = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id)
        .where(Artifact.kind == REWORK_BUDGET_RESET_KIND)
        .where(Artifact.title == _ledger_title(target_stage))
        .order_by(Artifact.created_at.desc())
    ).first()
    return marker.created_at if marker else None


def reset_rework_budget(session: Session, ticket: Ticket, *, target_stage: str, reason: str) -> int:
    """Give ``target_stage`` its reroute budget back, and say why.

    For the one case that earns it: a person read the accumulated feedback at the
    loop cap and decided the work should go back anyway. Without this, resolving
    that pause buys exactly one more round before the same cap fires again, so
    the operator is asked the identical question after every round — which is
    noise, not a safeguard. The same principle `loregarden_requeue_ticket`
    already states for the dispatch budget: it persists so that only a deliberate
    decision clears it, and the reason is recorded next to the decision.

    Resets the COUNT only. `rework_is_stuck` still compares the two most recent
    rounds and is deliberately untouched: it answers "can re-running differ from
    last time", which a person's decision does not change. An implement round
    that produced nothing should re-pause immediately, reset or no reset.

    Returns the number of rounds the reset forgives, so a caller can say what it
    did rather than asserting it did something.
    """
    if not target_stage:
        return 0
    forgiven = rework_reroute_count(session, ticket, target_stage)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=REWORK_BUDGET_RESET_KIND,
            title=_ledger_title(target_stage),
            content_json=json.dumps(
                {"target_stage": target_stage, "reason": reason, "rounds_forgiven": forgiven}
            ),
        )
    )
    session.commit()
    return forgiven


def rework_reroute_count(session: Session, ticket: Ticket, target_stage: str) -> int:
    """How many times ``target_stage`` has been rerouted to since its budget was
    last reset (durable).

    Counts forward from the newest `BUDGET_RESET` marker rather than over the
    whole ledger, so a deliberate reset is visible to the cap without deleting
    the feedback rows the re-run agent reads.
    """
    entries = _entries(session, ticket, target_stage)
    reset_at = _newest_reset_at(session, ticket, target_stage)
    if reset_at is None:
        return len(entries)
    return len([entry for entry in entries if entry.created_at > reset_at])


def rework_is_stuck(session: Session, ticket: Ticket, target_stage: str) -> bool:
    """Whether the last two rounds asked for the same thing against the same tree.

    A retry cap treats a loop that is converging and one that is not as the same
    thing: it cuts off work that was making progress, and still spends several
    full cycles on work that was not. Ticket 23 of the blobert milestone 14 run
    cycled `implementation` <-> `script_review` six times on one finding.

    The test is cheap because both halves are already on the row. If the newest
    round repeats the previous round's finding verbatim AND was raised against
    the same commit, the next round cannot differ from the last — nothing the
    agent did changed either the request or the code it was made about.

    Deliberately compares only the two most recent rounds. A loop that alternates
    between two findings is doing something, even if what it is doing is
    unproductive, and stopping it belongs to the retry cap rather than here.

    ONE KNOWN GAP, stated rather than papered over: `commit_sha` is HEAD, so it
    answers for COMMITTED work. An agent that edited without committing looks
    identical to one that did nothing. The orchestrator commits between stages,
    so the window is narrow, and the case this exists for — an agent that
    produced nothing at all — is exactly the one it catches.
    """
    entries = _entries(session, ticket, target_stage)
    if len(entries) < 2:
        return False

    previous, newest = entries[-2], entries[-1]
    if not newest.commit_sha or newest.commit_sha != previous.commit_sha:
        return False
    return _context_of(newest) == _context_of(previous) != ""


def _context_of(artifact: Artifact) -> str:
    try:
        payload = json.loads(artifact.content_json or "{}")
    except json.JSONDecodeError:
        # "" also means "no context recorded", so a corrupt artifact silently
        # reads as a fresh one and the loop-detection below stops matching.
        logger.warning("rework artifact %s has unparseable content", artifact.id, exc_info=True)
        return ""
    return (payload.get("context") or "").strip()


def record_reroute_exhausts_budget(
    session: Session,
    ticket: Ticket,
    *,
    target_stage: str,
    from_stage: str,
    context: str,
    run_id: str | None = None,
    check_convergence: bool = True,
) -> ReworkStopReason:
    """Record this reroute and report whether the loop should stop, and why.

    Returns ``True`` when the loop should stop, for either of two reasons.

    THE COUNT: ``target_stage`` had already been rerouted to
    ``MAX_REWORK_REROUTES`` times before this one. Read *before* recording, so
    the budget is "how many reroutes already happened". An empty
    ``target_stage`` records nothing and never exhausts.

    THE CONVERGENCE TEST: this round repeats the previous round's finding
    against the same tree. A count alone treats a converging loop and a stuck one
    identically — it cuts off work that was progressing while still spending
    several cycles on work that was not. When nothing changed between two
    rounds, the next one cannot differ, so waiting for the count to run out buys
    nothing. Checked *after* recording, because it compares this round against
    the one before it.

    ``check_convergence`` exists because repetition does not mean the same thing
    on every path that shares this ledger. A scope-denial reroute
    (`permission_bridge`) writes the SAME denial message every round by
    construction — it is the same denial, bouncing between implementers — so
    identical rounds are its normal shape rather than evidence of a stuck loop,
    and only the count should stop it. Rework findings are the opposite: an
    unchanged finding against an unchanged tree is exactly the signal.
    """
    if not target_stage:
        return ReworkStopReason.NONE
    prior = rework_reroute_count(session, ticket, target_stage)
    record_rework_feedback(
        session,
        ticket,
        target_stage=target_stage,
        from_stage=from_stage,
        context=context,
        run_id=run_id,
    )
    if prior >= MAX_REWORK_REROUTES:
        return ReworkStopReason.BUDGET
    if check_convergence and rework_is_stuck(session, ticket, target_stage):
        return ReworkStopReason.STUCK
    return ReworkStopReason.NONE


def render_rework_feedback(session: Session, ticket: Ticket, target_stage: str) -> str:
    """The full accumulated feedback for ``target_stage``, oldest round first,
    or ``""`` when there is none.

    Exact-duplicate rounds are collapsed (a recurring identical finding reads
    once, not N times) while distinct rounds are all kept — so the agent sees
    the union of everything asked of it, not just the latest framing.
    """
    entries = _entries(session, ticket, target_stage)
    if not entries:
        return ""

    rounds: list[tuple[str, str]] = []
    seen: set[str] = set()
    for artifact in entries:
        try:
            payload = json.loads(artifact.content_json or "{}")
        except json.JSONDecodeError:
            logger.warning(
                "Unparseable rework artifact %s for stage %s; its feedback is dropped",
                artifact.id,
                target_stage,
                exc_info=True,
            )
            continue
        context = (payload.get("context") or "").strip()
        if not context or context in seen:
            continue
        seen.add(context)
        rounds.append((payload.get("from_stage") or "", context))

    if not rounds:
        return ""
    if len(rounds) == 1:
        return rounds[0][1]

    blocks = []
    for index, (from_stage, context) in enumerate(rounds, start=1):
        source = f" (from `{from_stage}`)" if from_stage else ""
        blocks.append(f"### Round {index}{source}\n{context}")
    return "\n\n".join(blocks)
