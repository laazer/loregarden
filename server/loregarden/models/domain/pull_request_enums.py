"""Vocabulary for a ticket's pull request as GitHub reports it right now.

Split out of `enums` (near its size cap). These are Loregarden's own words for
what the PR tab shows; GitHub's raw vocabularies (`mergeStateStatus`,
`reviewDecision`) are folded into them in `services.ticket_pull_request`.
"""

from __future__ import annotations

from enum import StrEnum


class PullRequestLookup(StrEnum):
    """How a lookup of a ticket branch's pull request ended.

    `NONE` and `FAILED` are kept apart on purpose: the PR tab once said "No pull
    request opened" for a ticket whose PR was open, because the only answer it
    had was "nothing recorded".
    """

    #: GitHub has a pull request for the branch.
    FOUND = "found"
    #: GitHub answered, and there is no pull request for the branch.
    NONE = "none"
    #: The lookup itself failed (no `gh`, no auth, no network); the answer is unknown.
    FAILED = "failed"


class PullRequestState(StrEnum):
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class PullRequestCheckOutcome(StrEnum):
    """One CI check, reduced to what the operator acts on."""

    PASSING = "passing"
    FAILING = "failing"
    PENDING = "pending"
    #: Skipped or neutral: ran, and asks nothing of anyone.
    SKIPPED = "skipped"


class PullRequestReview(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REVIEW_REQUIRED = "review_required"
    #: The base has no review rule, so no review decision exists.
    NOT_REQUIRED = "not_required"
