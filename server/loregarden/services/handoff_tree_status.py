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
from loregarden.services.orchestration_profile import (
    GitAutomationConfig,
    ProfileUnreadableError,
)
from loregarden.services.target_branch import TargetBranchError, target_branch_name
from loregarden.services.ticket_worktree import resolve_ticket_root
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

logger = logging.getLogger(__name__)


class HandoffTreeStatus(BaseModel):
    """The handed-over tree, in the three terms both surfaces report."""

    behind: int = 0
    dirty_files: int = 0
    warning: str = ""


class PreparedTree(BaseModel):
    """The tree a handoff prepared, and the branch it was brought up to.

    One noun, because the two values are two readings of **one** resolution.
    ``resolve_target_branch`` is a write — it creates the integration branch,
    merges the base into it, can dispatch a blocking merge turn and files an
    inbox card on conflict — so a boundary that re-derived the branch beside a
    tree somebody else prepared paid for that write again to answer a question
    already answered. A tuple would have preserved the ability to take
    ``repo_root`` from here and ``target_branch`` from somewhere else, which
    **is** the defect (lg-milestone-that-974).

    Frozen for the same reason: reassigning either field re-creates the mixing.
    """

    model_config = ConfigDict(frozen=True)

    repo_root: Path
    target_branch: str


class HandoffTreePreparationError(ValueError):
    """A handoff failed *while preparing the tree*, and only that earns the tree sentence.

    The single legitimate input to ``describe_handoff_failure``. Before
    lg-milestone-that-975 both handoff boundaries put that sentence in front of
    every ``ValueError`` the renderer raised, so three refusals that happen
    before any tree work — an unknown agent, a missing workspace row, a repo
    path that is gone — arrived as "resolve the conflict with <branch> … the
    worktree is <path>". Because ``.toast__message`` clamps at four lines and an
    error toast auto-dismisses after 9s, that sentence is not a preface to the
    diagnosis, it **is** the diagnosis — so the three were not padded, they were
    replaced.

    Classification is decided at the raise site, by **position**: everything
    raised from inside ``render_stage_prompt``'s tree-preparation ``try``
    becomes this class, and the refusals lexically above it stay plain
    ``ValueError``s. So the default for an unclassified failure is its own text,
    which is what stops the next raise site added to the renderer from
    re-creating the defect.

    The base is ``ValueError`` and that is load-bearing, not incidental. Three
    call sites catch ``ValueError`` around ``_checked_out_run_view``
    (``external_harness.py``) and settle the run they stamped on it; nothing
    else ever settles an external run, so a base those three do not catch
    strands the stage RUNNING with no agent behind it (lg-milestone-that-974
    AC12, and the open lg-milestone-that-977).
    """


def describe_handoff_tree(tree: PreparedTree, *, ticket_external_id: str) -> HandoffTreeStatus:
    """How far the prepared tree is from its target, and what to tell the operator.

    ``behind`` is one-directional, like the refresh it reports on: the ticket's
    own commits reach its target at landing (768), never at handoff, so a tree
    *ahead* of its target is current rather than degraded.
    """
    repo_root, target_branch = tree.repo_root, tree.target_branch
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


def describe_handoff_failure(
    session: Session, ticket: Ticket, exc: HandoffTreePreparationError
) -> str:
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

    The target is **read**, never resolved. This runs *because* a resolution
    raised, so resolving again re-attempts the merge that just failed, can
    dispatch a merge-agent turn synchronously inside a 400 response, and
    re-files the `integration_conflict` card — all to put a branch name in one
    sentence (lg-milestone-that-974 R6). ``target_branch_name`` touches no refs,
    which is not the same as "cannot fail", and it fails in **two** unrelated
    ways, which is why the guard below names two base classes:

    * ``TargetBranchError`` through ``subtree_root`` -> ``_refuse_initiative`` /
      ``_require_initiative_child`` (target_branch.py:85-128), when the ticket
      is an initiative or an initiative's non-child.
    * ``ProfileUnreadableError``, raised by the profile resolver, when the
      workspace's profile file is not a profile at all — truncated,
      hand-edited into a syntax error, a document that is not a mapping, or
      not UTF-8. It subclasses ``RuntimeError``
      (orchestration_profile.py), so ``except ValueError`` never reached it
      and four shapes of a half-written profile escaped as a 500
      (lg-milestone-that-1215).
    * a plain ``ValueError``, which is still named and still wanted:
      ``validate_branch_name`` raises one through ``integration_branch_for``,
      and an unnameable integration branch should fall back to the base rather
      than 500 the route. The tuple is **additive** for that reason — dropping
      ``ValueError`` would trade one regression for another.

    ``TargetBranchError`` and ``ProfileUnreadableError`` both subclass
    ``RuntimeError``, so no one of the three names covers another and dropping
    any of them re-opens a 500 (lg-milestone-that-1214, -1215).

    The fallback inside the guard is the built-in ``GitAutomationConfig``
    default and deliberately **not** a second resolve: reading
    ``resolve_orchestration_profile(workspace).git.base_branch`` there re-calls
    the function that just raised, which is the defect this guard exists to
    stop. The model default has no required fields and performs no I/O, so it
    cannot raise — and because it is a stand-in rather than the workspace's
    configured base, the WARNING says so.

    The tree and the target are still best-effort — this runs on a path where
    one of them may be exactly what failed, and a message that cannot be built
    is worse than one naming a fallback.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    repo_root = Path(workspace.repo_path) if workspace else Path(".")
    target_branch = ""
    if workspace is not None:
        repo_root = resolve_ticket_root(session, ticket, workspace)
        try:
            target_branch = target_branch_name(session, ticket, workspace)
        except (TargetBranchError, ProfileUnreadableError, ValueError) as resolve_exc:
            # The target itself is what failed. Name the branch it would have
            # landed on rather than dropping the fact from the sentence — but
            # name it as the stand-in it is: `main` matches the configured base
            # today only by accident, so a record saying only that a lookup
            # failed reads exactly like a resolved answer. The record says what
            # the branch *is* rather than which arm fired, because both arms
            # reach the same fallback and the caught exception's own text —
            # carried verbatim — is the only text naming the real cause.
            target_branch = GitAutomationConfig().base_branch
            logger.warning(
                "Could not read %s's target branch for its failed handoff "
                "(workspace %r): %s. Naming the built-in default branch %r instead "
                "of one read from the workspace's orchestration profile.",
                ticket.external_id,
                workspace.slug,
                resolve_exc,
                target_branch,
            )
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
