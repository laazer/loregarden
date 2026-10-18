"""Vocabulary for the workspace dashboard: review order and the catch-up digest.

Split out of `enums` (near its size cap), the same way `pull_request_enums` was.
"""

from __future__ import annotations

from enum import StrEnum


class PullRequestReviewTier(StrEnum):
    """Where an open PR falls in the review order, most actionable first.

    Members are declared in that order, and the order is the ranking:
    `services.workspace_pull_requests` sorts on a tier's position here.
    """

    #: Approved, no conflicts, checks not failing: what is left is the merge.
    READY_TO_MERGE = "ready_to_merge"
    #: Waiting on a review and nothing else.
    TO_REVIEW = "to_review"
    #: A check is failing or a reviewer asked for changes; the author owes a fix.
    NEEDS_WORK = "needs_work"
    #: Cannot merge until it is rebased onto its base.
    CONFLICTING = "conflicting"
    #: Not asking for review yet.
    DRAFT = "draft"


class StageCompletionOutcome(StrEnum):
    """The `outcome` a `StageCompleted` event records (`complete_stage`'s pass/reject)."""

    PASS = "pass"
    REJECT = "reject"


class ActivityHighlightKind(StrEnum):
    """What a person catching up wants to know happened to a ticket.

    A digest of `domain_events`, not a copy of it: artifacts, run starts and
    passing gates are left out, because 314 raw events over two weeks of one
    workspace read as noise, and these are the ones that change what to do.
    """

    CREATED = "created"
    #: The ticket's state changed; the highlight's detail names the new state.
    MOVED = "moved"
    LANDED = "landed"
    LANDING_FAILED = "landing_failed"
    STAGE_REJECTED = "stage_rejected"
    RUN_FAILED = "run_failed"
    GATE_FAILED = "gate_failed"
    APPROVAL_REQUESTED = "approval_requested"
