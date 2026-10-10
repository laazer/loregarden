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


class DirtyCheckoutCause(StrEnum):
    #: Uncommitted changes no run of this owner made.
    DIRTY = "dirty"
    #: `git status` could not answer — refused, never read as clean.
    UNREADABLE = "unreadable"


__all__ = ["DirtyCheckoutCause", "PrimaryCheckoutUse"]
