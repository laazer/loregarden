"""The receiving half of a handoff: does the tree still match the attestation?

Handoffs here were producer-attested only. An agent wrote what it had done, the
next stage started, and nothing in between asked whether the checkout the second
agent was about to work in was still the one the first had described. That gap is
the shape of this control plane's recurring failures — a concurrent session moving
the shared checkout's branch, a squash-merge landing mid-ticket, a worktree that
was expected and absent — and in every one of them the receiving stage had the
evidence to notice and no code that looked at it.

This module is the look. It answers with a verdict rather than a bool because the
interesting cases are not "same or different": a HEAD that has moved forward is
the *normal* state between stages, since the orchestrator commits, and treating it
as a mismatch would fire on every ticket. Whether a verdict should stop a dispatch
is a separate question from what the verdict is, and lives in `verdict_proceeds`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loregarden.models.domain import (
    AgentRun,
    Approval,
    BoundaryVerdict,
    GitBoundary,
    Ticket,
    Workspace,
)
from loregarden.services.git_boundary import boundary_of_run
from loregarden.services.git_subprocess import run_git
from loregarden.services.handoff_store import boundary_from_doc, latest_handoff_doc
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.stage_parking import park_stage
from sqlmodel import Session

#: Verdicts a stage may start on. ADVANCED is here because the orchestrator
#: commits between stages, so a receiver almost always inherits a descendant of
#: what the sender attested to; UNKNOWN is here because refusing to run on a
#: handoff written before boundaries existed would strand every in-flight ticket.
PROCEEDING_VERDICTS = frozenset(
    {BoundaryVerdict.MATCH, BoundaryVerdict.ADVANCED, BoundaryVerdict.UNKNOWN}
)


def verdict_proceeds(verdict: BoundaryVerdict) -> bool:
    return verdict in PROCEEDING_VERDICTS


def _repo_contains(repo_root: Path, sha: str) -> bool:
    """Whether `sha` names a commit this repository actually has.

    Checked before ancestry because `merge-base --is-ancestor` cannot tell "not
    an ancestor" from "never heard of it" — both are a non-zero exit. A sha the
    receiver does not have is a different repository, not a diverged one, and
    reporting it as divergence would send someone looking for a force-push that
    never happened.
    """
    try:
        proc = run_git(
            ["cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return proc.returncode == 0


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    try:
        proc = run_git(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return proc.returncode == 0


def compare(receiver: GitBoundary, sender: GitBoundary) -> BoundaryVerdict:
    """How `receiver` — the tree a stage is about to run in — relates to
    `sender`, the tree the last handoff attested against.

    Ordered widest-difference-first: a different checkout makes the branch
    comparison meaningless, and a different branch makes ancestry meaningless.
    """
    if not receiver.is_recorded or not sender.is_recorded:
        return BoundaryVerdict.UNKNOWN

    if receiver.repo_path != sender.repo_path:
        return BoundaryVerdict.REPO_CHANGED

    if receiver.branch != sender.branch:
        return BoundaryVerdict.BRANCH_CHANGED

    if receiver.head_sha == sender.head_sha:
        return BoundaryVerdict.MATCH

    repo_root = Path(receiver.repo_path)
    if not _repo_contains(repo_root, sender.head_sha):
        return BoundaryVerdict.REPO_CHANGED

    if _is_ancestor(repo_root, sender.head_sha, receiver.head_sha):
        return BoundaryVerdict.ADVANCED

    return BoundaryVerdict.DIVERGED


def describe(verdict: BoundaryVerdict, *, receiver: GitBoundary, sender: GitBoundary) -> str:
    """Both boundaries and the verdict, for the approval a mismatch raises.

    Written for whoever opens the inbox with no memory of the ticket, so it says
    what was expected and what is there rather than naming the verdict alone.
    """
    lines = [
        f"Boundary check: {verdict.value}.",
        f"  handed off from: {sender.repo_path or '(unrecorded)'} "
        f"on {sender.branch or '(detached)'} at {sender.head_sha[:12] or '(none)'}",
        f"  about to run in: {receiver.repo_path or '(unrecorded)'} "
        f"on {receiver.branch or '(detached)'} at {receiver.head_sha[:12] or '(none)'}",
    ]
    if receiver.dirty_paths:
        lines.append(f"  uncommitted here: {len(receiver.dirty_paths)} path(s)")
    lines.append(
        "Approving lets the stage run against the tree as it is now. It does not "
        "commit, push, merge, or resolve anything."
    )
    return "\n".join(lines)


def safe_compare(receiver: GitBoundary, sender: GitBoundary) -> BoundaryVerdict:
    """`compare`, with git failures answering UNKNOWN instead of raising.

    The check exists to catch a stage running on the wrong tree. A check that can
    itself stop a stage from running would be a worse bug than the one it is
    looking for, so every way this can fail resolves to "we could not tell".
    """
    try:
        return compare(receiver, sender)
    except (OSError, subprocess.SubprocessError):
        return BoundaryVerdict.UNKNOWN


def boundary_enforced(workspace: Workspace) -> bool:
    """Whether a failing verdict stops a stage in this workspace, or is only
    recorded. Off unless the workspace's orchestration profile says otherwise."""
    return resolve_orchestration_profile(workspace).boundary.enforce


def verify_run_boundary(session: Session, run: AgentRun, ticket: Ticket) -> BoundaryVerdict:
    """Compare the tree `run` is about to execute in against the last handoff,
    and record the verdict on the run.

    Recorded unconditionally, matches included: the point of the column is the
    rate, and a table that only holds mismatches cannot say whether one in ten
    dispatches or one in ten thousand hits this.
    """
    sender_doc = latest_handoff_doc(session, ticket.id)
    sender = boundary_from_doc(sender_doc) if sender_doc else GitBoundary()
    verdict = safe_compare(boundary_of_run(run), sender)

    run.start_boundary_verdict = verdict
    session.add(run)
    session.commit()
    return verdict


def park_for_boundary(
    session: Session,
    *,
    run: AgentRun,
    ticket: Ticket,
    verdict: BoundaryVerdict,
) -> Approval:
    """Send a failing verdict to the approval inbox instead of blocking.

    A mismatch is usually someone else's concurrent work, not a broken ticket.
    See `services.stage_parking` for why an approval rather than a block, and why
    the dispatch budget is refunded.
    """
    sender_doc = latest_handoff_doc(session, ticket.id)
    impact = describe(
        verdict,
        receiver=boundary_of_run(run),
        sender=boundary_from_doc(sender_doc) if sender_doc else GitBoundary(),
    )
    return park_stage(
        session,
        run=run,
        ticket=ticket,
        title=f"Boundary check on {ticket.external_id}: {verdict.value}",
        impact=impact,
    )
