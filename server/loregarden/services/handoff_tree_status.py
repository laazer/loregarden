"""What a handoff boundary can say about the tree it is handing over.

Two surfaces hand a tree to something this process does not supervise: the
terminal-handoff route, which returns a command for a person to paste, and the
external-harness checkout, which returns a prompt for an agent to run. Both
answer the same operator question — *can I trust the tree this will run in?* —
and both answer it **after** the refresh, here, rather than having it plumbed
out of ``ensure_ticket_branch``: the third dispatch caller does not want it, and
"this tree is behind its target" is true whatever left it behind.

The one rule the two counts exist to serve: "it is current" and "we could not
tell" must never collapse into one answer (see CLAUDE.md, *No silent
failures*).
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import TargetBranchError, resolve_target_branch
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session

logger = logging.getLogger(__name__)


class HandoffTreeStatus(BaseModel):
    """The handed-over tree, in the three terms both surfaces report."""

    behind: int = 0
    dirty_files: int = 0
    warning: str = ""


def describe_handoff_tree(
    repo_root: Path, *, target_branch: str, ticket_external_id: str
) -> HandoffTreeStatus:
    """How far ``repo_root`` is from ``target_branch``, and what to tell the operator.

    ``behind`` is one-directional, like the refresh it reports on: the ticket's
    own commits reach its target at landing (768), never at handoff, so a tree
    *ahead* of its target is current rather than degraded.
    """
    if not target_branch:
        # Nothing to measure against. `rev-list --count HEAD..` exits 0 with 0,
        # which would report a current tree for a question nobody could answer.
        return _unreadable(repo_root, target_branch, ticket_external_id, "no target branch")

    behind = _count_lines(
        repo_root, ["rev-list", "--count", f"HEAD..{target_branch}"], single_number=True
    )
    dirty = _count_lines(repo_root, ["status", "--porcelain"], single_number=False)
    if behind is None or dirty is None:
        return _unreadable(repo_root, target_branch, ticket_external_id, "git refused the read")

    if behind == 0:
        # Nothing to refresh, so nothing to warn about — dirty or not.
        return HandoffTreeStatus(behind=0, dirty_files=dirty)
    if dirty:
        # Action first, then the two short facts, then the path — the ordering
        # `describe_handoff_failure` explains. `.toast__message` clamps at
        # `max-height: 5.8em` (69.6px, measured at 1600px), and a path in the
        # middle of the sentence pushes the branch name off the end, where the
        # operator has nowhere else on this surface to read it.
        warning = (
            f"Commit or stash the {dirty} uncommitted changes, then copy again: it is "
            f"{behind} commits behind {target_branch} — the tree is {repo_root}."
        )
    else:
        warning = (
            f"Merge {target_branch} by hand, or run the stage supervised: the tree is still "
            f"{behind} commits behind its target ({repo_root})."
        )
    return HandoffTreeStatus(behind=behind, dirty_files=dirty, warning=warning)


def describe_handoff_failure(session: Session, ticket: Ticket, exc: Exception) -> str:
    """The text an operator or a harness is given when the tree could not be prepared.

    Leads with one short sentence carrying the four facts they act on — which
    ticket, which tree, which branch, what to do next — because
    ``.toast__message`` clamps at four lines (measured: 69.6px, about 188
    characters at 1600px) and an error toast auto-dismisses after 9s, so
    anything past that is unreadable *and* unrecoverable. Git's own merge
    output follows the lead sentence, and the whole string goes to the failed
    run's ``stderr``, where the run view already shows it.

    The order inside the sentence is load-bearing, not style. An absolute
    worktree path is 70–130 characters on its own, so the four facts plus the
    path do not fit in four lines in *any* order — measured on the real toast.
    So the action and the two short identifiers go first and the path goes
    last: the fact the clamp eats is the one recoverable from the ticket, and
    the branch to resolve against survives on screen.

    The tree and the target are resolved best-effort: this runs on a path where
    one of them may be exactly what failed, and a message that cannot be built
    is worse than one naming a fallback.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    repo_root = Path(workspace.repo_path) if workspace else Path(".")
    target_branch = ""
    if workspace is not None:
        repo_root = resolve_ticket_root(session, ticket, workspace)
        try:
            target_branch = resolve_target_branch(
                session, ticket, workspace, repo_root=resolve_workspace_root(workspace)
            )
        except TargetBranchError as resolve_exc:
            # The target itself is what failed. Name the branch it would have
            # landed on rather than dropping the fact from the sentence.
            logger.warning(
                "Could not name the target branch for %s's failed handoff: %s",
                ticket.external_id,
                resolve_exc,
            )
            target_branch = resolve_orchestration_profile(workspace).git.base_branch
    # Every word in the prose is paid for twice: once against AC12's 160-character
    # cap, and once against the clamp, which eats the tail. Measured on this
    # ticket's own identifiers (a 21-character external id, a 33-character
    # integration branch), the longer phrasing this replaced came to 161 prose
    # characters — one over — so the sentence says where the tree is the way
    # `describe_handoff_tree` does and lets the verb come from "resolve the
    # conflict".
    lead = (
        f"{ticket.external_id}: resolve the conflict with "
        f"{target_branch or 'its target branch'}, or run the stage supervised — "
        f"the worktree is {repo_root}."
    )
    return f"{lead} {exc}".strip()


def _unreadable(
    repo_root: Path, target_branch: str, ticket_external_id: str, reason: str
) -> HandoffTreeStatus:
    logger.warning(
        "Could not check the handoff tree for %s: %s (%s against %r)",
        ticket_external_id,
        reason,
        repo_root,
        target_branch,
    )
    return HandoffTreeStatus(
        warning=(
            # Same clamp, same ordering rule as `describe_handoff_failure`: the
            # action and the branch first, the long path where it can be clipped.
            f"Make sure it is still a checkout, or run the stage supervised: the tree could "
            f"not be checked against {target_branch or 'its target branch'} ({repo_root})."
        )
    )


def _count_lines(repo_root: Path, args: list[str], *, single_number: bool) -> int | None:
    """``git <args>`` in ``repo_root`` as a count, or None when the read failed."""
    try:
        result = run_git(args, cwd=repo_root, capture_output=True, text=True, check=False)
    except OSError as exc:
        logger.warning("git %s failed in %s: %s", " ".join(args), repo_root, exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "git %s failed in %s: %s",
            " ".join(args),
            repo_root,
            (result.stderr or result.stdout or "").strip(),
        )
        return None
    if single_number:
        text = result.stdout.strip()
        if not text.isdigit():
            logger.warning("git %s returned %r in %s", " ".join(args), text, repo_root)
            return None
        return int(text)
    return len([line for line in result.stdout.splitlines() if line.strip()])
