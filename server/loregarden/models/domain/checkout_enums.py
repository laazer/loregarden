"""Vocabulary for refusing to work in a dirty primary checkout.

Split out of `enums` (near its size cap) beside `process_enums`, the other
small vocabulary about what this control plane may do to a tree it shares with
an operator (lg-workflow-integrity-864).
"""

from __future__ import annotations

from enum import StrEnum


class PrimaryCheckoutUse(StrEnum):
    """What was about to run in, or change, the primary checkout when it was refused."""

    #: A ticket run falling back to the shared checkout (worktree off, or not cut).
    TICKET_FALLBACK = "ticket_fallback"
    #: A chat turn falling back to the shared checkout.
    CHAT_FALLBACK = "chat_fallback"
    #: `checkout -B` of a ticket branch in the shared checkout.
    TICKET_BRANCH_CHECKOUT = "ticket_branch_checkout"
    #: Merging a worktree, which checks the target branch out in the primary first.
    MERGE_WORKTREE = "merge_worktree"
    #: Writing another workspace's hook block into its lefthook config.
    HOOK_INSTALL = "hook_install"
    #: Switching the ticket's own worktree, not the shared checkout, to its branch.
    TICKET_WORKTREE_BRANCH_CHECKOUT = "ticket_worktree_branch_checkout"


class BranchHolder(StrEnum):
    """Which tree is holding the branch a checkout was refused, once classified.

    Three states because the refusal has three shapes, and "git could not say"
    is not the same claim as "it is the main working tree". Classified once per
    `locked_at` and passed to both the removal guard and the message, so the
    two cannot name different trees (lg-milestone-that-916).
    """

    #: The holder is this repository's main working tree — never force-removed.
    MAIN_WORKING_TREE = "main_working_tree"
    #: The holder is some other linked worktree — the stale-scratchpad case.
    LINKED_WORKTREE = "linked_worktree"
    #: git could not say which tree is the main one, so nothing is removed.
    UNIDENTIFIED = "unidentified"


class DirtyCheckoutCause(StrEnum):
    #: Uncommitted changes no run of this owner made.
    DIRTY = "dirty"
    #: `git status` could not answer — refused, never read as clean.
    UNREADABLE = "unreadable"


class FollowMainState(StrEnum):
    """What the dev server did about main moving past the build it serves.

    See `services.follow_main`. The first five are the server keeping up; the
    rest are why it is not, each one shown by `loregarden_doctor`.
    """

    #: The checkout is main's tip and the server runs it.
    CURRENT = "current"
    #: Fast-forwarded onto main; nothing under `server/` changed, so no reload.
    PULLED = "pulled"
    #: Asked the supervisor to reload onto the checkout's new head.
    RELOADING = "reloading"
    #: Main moved, but an agent run or stage is in flight; retried next tick.
    WAITING_FOR_IDLE = "waiting_for_idle"
    #: As above, and no lane starts new work until it finishes (`reload_hold`).
    HOLDING = "holding"
    #: The checkout is on another branch, so main is not its to follow.
    OFF_BASE = "off_base"
    #: The checkout has commits main does not; only a person can reconcile them.
    DIVERGED = "diverged"
    #: `git merge --ff-only` refused (uncommitted changes to files main changed).
    REFUSED = "refused"
    #: Main's tip could not be fetched or the checkout could not be read.
    GIT_FAILED = "git_failed"


__all__ = ["BranchHolder", "DirtyCheckoutCause", "FollowMainState", "PrimaryCheckoutUse"]
