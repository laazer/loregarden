"""Why a checkout is still on disk: the premise that chose it, or the failure.

Two vocabularies, in their own module because three modules read them and the
two that matter most — `worktree_lifecycle`, which decides, and
`worktree_receipt`, which answers the first premise — would otherwise import
each other.

`KeepReason` is premises: something *chose* to keep this tree, and an operator
can act on which one. `RemovalFailure` means every premise held and the removal
did not work, and it keeps exactly one member: a premise filed here would be
reported as a broken disk, and a failure filed under `KeepReason` would be
grouped by the boot summary as though something had decided it. The two are
disjoint, which is what lets `Retirement.refusal` carry either and still be
read for which kind it is.
"""

from __future__ import annotations

from enum import StrEnum


class KeepReason(StrEnum):
    """Why a finished worktree's checkout stays on disk.

    The values are the wording callers print, which is why this is a
    `StrEnum` — and the order is the order a sweep reports in: the receipt
    first, because it is the premise the operator can always act on, then the
    three shape premises, then the two kinds of not knowing.
    """

    UNCOMMITTED = "uncommitted changes"
    STRANDED_HEAD = "a commit no branch or tag reaches"
    NOTHING_COMMITTED = "no commits beyond its base"
    #: Nothing on record says the work this checkout held ever finished. The
    #: premise this ticket adds, and the one consulted before any premise about
    #: the shape of the tree.
    NO_RECEIPT = "no record that its work landed"
    #: There is a landing, and it does not contain what is in this tree now.
    RECEIPT_DOES_NOT_COVER = "its landing does not cover this tree's tip"
    #: The row records a claim nobody can read, or this machine cannot say
    #: which boot it is on. Either way ownership is unproven, which is not the
    #: same as unowned.
    CLAIM_UNPROVEN = "its claim cannot be read"
    #: The filesystem would not say whether the checkout is there.
    UNREADABLE = "its checkout cannot be read"
    #: The workspace that cut this checkout is no longer on record, so the
    #: worktrees folder cannot be named. A construction failure of the
    #: service, not a tree-shape premise — not consulted by `_keep_reason`.
    WORKSPACE_GONE = "its workspace is gone"
    #: The process that claimed it is still running.
    CLAIMANT_LIVE = "the process that claimed it is still running"
    #: The recorded checkout is gone and `parked_at` still names a live
    #: directory. A census skip, not a tree-shape premise — not consulted by
    #: `_keep_reason`.
    PARKED_CHECKOUT = "its checkout is parked"


class RemovalFailure(StrEnum):
    """Every premise held, the removal was attempted, and it did not happen."""

    #: The value is byte-identical to what lg-external-harness-954 measured
    #: against the four-line toast clamp, and stays so.
    CLEANUP_FAILED = "removing its checkout failed"


#: What a refusal can be. Either kind leaves a directory on disk; which kind it
#: is decides whether a retry can help.
RetirementRefusal = KeepReason | RemovalFailure
