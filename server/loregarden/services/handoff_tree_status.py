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
from loregarden.services.orchestration_profile import ProfileUnreadableError
from loregarden.services.target_branch import TargetBranchError, target_branch_name
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
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

    Leads with one short sentence carrying the facts they act on — which
    ticket, what failed, what to do next, and where — because
    ``.toast__message`` clamps at four lines (measured: 69.6px, about 188
    characters at 1600px) and an error toast auto-dismisses after 9s, so
    anything past that is unreadable *and* unrecoverable. The caught failure's
    own text follows the lead, and the whole string goes to the failed run's
    ``stderr``, where the run view already shows it.

    There are **four** leads, selected by which arm produced — or failed to
    produce — the target, and by nothing else (lg-milestone-that-1237):

    * the profile is not a profile: say so and name the file to edit. Nothing
      in a worktree fixes malformed YAML, and a second absolute path pushes the
      actionable one off the end of the clamp, so this is the one lead that
      names ``ProfileUnreadableError.path`` and not the tree.
    * there is no target branch to be brought up to: say *that*, and that
      nothing was merged. This is the fact the one old lead got wrong — it told
      the operator to "resolve the conflict with ``main``" on inputs where no
      merge was attempted, no conflict existed and nobody had resolved
      ``main``. There is deliberately no fix verb: the arm covers an
      initiative's non-child, an unnameable integration branch and a real
      initiative, and every candidate verb is wrong for at least one of them.
      Which rule refused is in ``exc``'s own text behind the lead.
    * the same sentence with its worktree clause omitted, when there is no
      workspace row at all. ``repo_root`` would be the bare ``Path(".")`` — the
      server's working directory rather than a tree the operator can open, so
      it is a path-shaped non-answer. Keyed on ``workspace is None`` and not on
      ``work_item_type``, because two inputs reach it: a real initiative
      (``workspace_id`` is NULL iff INITIATIVE, ticket_workspace_binding.py)
      and a ticket whose workspace row is gone.
    * the read succeeded, which is the real conflicting-refresh case
      lg-milestone-that-917 wrote this composer for, and it reads exactly as it
      always has.

    The order inside each sentence is load-bearing, not style. An absolute
    worktree path is 70–130 characters on its own, so the facts plus the path
    do not fit in four lines in *any* order — measured on the real toast. So
    the action and the short identifiers go first and the path goes last: the
    fact the clamp eats is the one recoverable from the ticket.

    The target is **read**, never resolved. This runs *because* a resolution
    raised, so resolving again re-attempts the merge that just failed, can
    dispatch a merge-agent turn synchronously inside a 400 response, and
    re-files the `integration_conflict` card — all to put a branch name in one
    sentence (lg-milestone-that-974 R6). ``target_branch_name`` touches no refs,
    which is not the same as "cannot fail", and it fails in **three** unrelated
    ways, split across the two handlers below:

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
      and a ticket whose integration branch cannot be named has no target
      branch rather than a 500. The arm is **additive** for that reason —
      dropping ``ValueError`` would trade one regression for another.

    ``TargetBranchError`` and ``ProfileUnreadableError`` both subclass
    ``RuntimeError``, so no one of the three names covers another and dropping
    any of them re-opens a 500 (lg-milestone-that-1214, -1215).

    The two handlers are two ``except`` clauses rather than one with a type
    check: they compose different leads, and arm-specific prose out of a single
    handler is only reachable through ``isinstance``, which the
    ``py-organization`` gate forbids. ``except`` is Python's own class dispatch.

    No arm substitutes a branch nobody resolved. The guard used to fall back to
    the built-in default base — ``main``, a stand-in — and then tell the
    operator to resolve a conflict with it; that is the defect this composer's
    four leads replace. The resolve failure stays observable (CLAUDE.md, *No
    silent failures*): each firing handler logs exactly one WARNING carrying the
    ticket, the workspace and the caught exception's own text, which is the only
    text anywhere naming which field of which YAML is wrong.

    The tree is still best-effort — this runs on a path where it may be exactly
    what failed, and a message that cannot be built is worse than one naming a
    tree that has moved.
    """
    # An initiative's `workspace_id` is NULL by construction, not by accident:
    # `ck_tickets_workspace_binding` (tables.py) pins
    # `(workspace_id IS NULL) = (work_item_type == 'initiative')`. So the
    # question this arm means is whether there is a workspace id at all, asked
    # before the database is asked to load a row by one.
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        # No tree to name and no target to read: the guard below is never
        # entered, so this arm composes its own lead rather than falling
        # through with `Path(".")` in hand.
        return f"{_no_target_lead(ticket.external_id, None)} {exc}".strip()

    repo_root = resolve_ticket_root(session, ticket, workspace)
    try:
        target_branch = target_branch_name(session, ticket, workspace)
    except ProfileUnreadableError as resolve_exc:
        _log_unreadable_target(ticket, workspace, resolve_exc)
        # The file, absolutely, and nothing else. `.path` is already absolute
        # and already `.resolve()`d (orchestration_profile.py:60-63), and the
        # resolver tries three candidates — so the file that failed to parse is
        # routinely `default.yaml` rather than anything derivable from the slug.
        lead = (
            f"{ticket.external_id}: no target branch — the workspace's orchestration "
            f"profile will not parse. Fix {resolve_exc.path}."
        )
    except (TargetBranchError, ValueError) as resolve_exc:
        _log_unreadable_target(ticket, workspace, resolve_exc)
        lead = _no_target_lead(ticket.external_id, repo_root)
    else:
        # Every word in the prose is paid for twice: once against the
        # 160-character cap, and once against the clamp, which eats the tail.
        lead = (
            f"{ticket.external_id}: resolve the conflict with "
            f"{target_branch or 'its target branch'}, or run the stage supervised — "
            f"the worktree is {repo_root}."
        )
    return f"{lead} {exc}".strip()


def _no_target_lead(external_id: str, repo_root: Path | None) -> str:
    """There is no branch this ticket could be brought up to, so nothing merged.

    Shared by the three inputs that have no target: an initiative's non-child,
    an unnameable integration branch, and the ``workspace is None`` arm — which
    passes ``None`` and gets the same sentence without its worktree clause.
    """
    lead = (
        f"{external_id}: this ticket has no target branch to be brought up to, so "
        f"nothing was merged — run the stage supervised."
    )
    if repo_root is None:
        return lead
    return f"{lead} The worktree is {repo_root}."


def _log_unreadable_target(ticket: Ticket, workspace: Workspace, resolve_exc: Exception) -> None:
    """One WARNING per firing handler, naming no branch.

    The record used to name the stand-in branch the guard substituted, which
    read exactly like a resolved answer. With no stand-in there is nothing to
    label — but the log itself must not go with it: a resolve failure on a real
    path is a real failure, and the caught exception's own text is the only
    place the cause survives, because the toast clamp eats everything past the
    lead.
    """
    logger.warning(
        "Could not read %s's target branch for its failed handoff (workspace %r): %s. "
        "The failure sentence says the ticket has no target branch rather than naming "
        "one nobody resolved.",
        ticket.external_id,
        workspace.slug,
        resolve_exc,
    )


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
