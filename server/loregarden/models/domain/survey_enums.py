"""Closed vocabularies of the reference survey (`services.reference_survey`).

The survey dissects someone else's project and lands what it finds on our
tickets. Its record used to be prose in a scratch file, where "I did not find X"
and "X is not there" read the same and a retracted claim was an edit nobody
could see. Each enum here is a distinction that prose lost.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "SurveyCardKind",
    "SurveyEvidenceStance",
    "SurveyFindingClaim",
    "SurveyFindingStatus",
    "LicenceBasis",
    "LicenceKind",
    "SurveyPassMode",
    "SurveyPassOutcome",
    "SurveyPassStatus",
    "SurveyTicketAction",
    "SurveyReadState",
    "SurveySourceKind",
    "SurveySourceStatus",
    "LicenceUse",
    "SurveyEpistemic",
    "SurveyUnknownStatus",
    "SurveyQueueAction",
    "SurveyPassAction",
    "SurveyRecordAction",
    "LibraryState",
    "LearningsAvailability",
]


class SurveySourceKind(StrEnum):
    """What the survey is pointed at, which decides how a pass reads it."""

    REPO = "repo"
    SITE = "site"
    #: Shipped artefacts with no source. Read what it publishes; never run it.
    RELEASED_APP = "released_app"


class SurveySourceStatus(StrEnum):
    """Where a source stands in the queue."""

    #: Waiting on a person: a new source is approved before anything reads it.
    PROPOSED = "proposed"
    #: Approved, with passes waiting or running.
    ACTIVE = "active"
    #: Nothing waiting, and the record says why it stopped.
    DONE = "done"
    #: A person declined it, or the licence gate refused it.
    REFUSED = "refused"


class LicenceKind(StrEnum):
    """A licence classification, ordered so the stricter reading wins.

    `CUSTOM` is its own answer: licence words the classifier cannot read are not
    "nothing here" and never resolve toward permission (lg-reference-sources-1376).
    """

    OPEN = "open"
    LIMITED = "limited"
    CUSTOM = "custom"
    NONE = "none"


class LicenceBasis(StrEnum):
    """Where a licence classification came from. A file read at a pinned commit
    outranks a manifest field, which outranks a person's note."""

    FILE_AT_COMMIT = "file_at_commit"
    MANIFEST = "manifest"
    RECORDED_BY_HAND = "recorded_by_hand"


class LicenceUse(StrEnum):
    """What the licence lets a survey do with the source."""

    #: Code may be quoted and reimplemented closely.
    FULL = "full"
    #: Copyleft, unlicensed or manifest-only: read for patterns, copy nothing.
    PATTERNS_ONLY = "patterns_only"
    #: Not read at all (non-commercial, source-available, or unreadable terms).
    EXCLUDED = "excluded"


class SurveyPassMode(StrEnum):
    """Why a pass exists."""

    #: The first read of a source.
    INITIAL = "initial"
    #: Pick up the recorded unread list and keep going.
    CONTINUATION = "continuation"
    #: Re-read an already-surveyed source against one new question.
    SCOPED = "scoped"


class SurveyPassStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    FINISHED = "finished"
    #: The licence gate or a person stopped it before anything was read.
    REFUSED = "refused"
    #: Its carrier ticket finished without the pass being finished: nothing
    #: recorded where it stopped, so the remainder is unknown.
    ABANDONED = "abandoned"


class SurveyPassOutcome(StrEnum):
    """The answer to the pass's own question."""

    ANSWERED = "answered"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"


class SurveyReadState(StrEnum):
    """What a pass did with one entry of the directory map."""

    READ = "read"
    PARTIAL = "partial"
    #: Looked at and deliberately left, with the reason recorded.
    SKIPPED = "skipped"


class SurveyTicketAction(StrEnum):
    """What a pass did to a ticket."""

    CREATED = "created"
    APPENDED = "appended"
    #: The target was at the criteria cap, so the finding went to a new sibling.
    SPLIT = "split"
    #: The target was done, so its criteria are frozen: a new related ticket.
    ROUTED_FROM_DONE = "routed_from_done"
    #: A defect in our own tooling, hit while surveying.
    SELF_DEFECT = "self_defect"
    #: Named in an imported legacy record, action not recoverable from the prose.
    MENTIONED = "mentioned"


class SurveyEpistemic(StrEnum):
    """How a piece of evidence or a finding is known."""

    #: Read directly: a file count, a quoted line.
    OBSERVED = "observed"
    #: Reasoned from observations: "this directory is where the rules live".
    INFERRED = "inferred"


class SurveyFindingClaim(StrEnum):
    """What a finding asserts. The three must never render the same."""

    #: Something is so.
    ASSERTION = "assertion"
    #: X is not there. Needs evidence whose corpus is complete for the question.
    ABSENCE = "absence"
    #: I did not find X in this corpus. Says nothing about what else exists.
    NOT_FOUND = "not_found"


class SurveyFindingStatus(StrEnum):
    ACTIVE = "active"
    #: Proved false. The row stays, with why and what replaced it.
    RETRACTED = "retracted"


class SurveyUnknownStatus(StrEnum):
    OPEN = "open"
    #: Closed by observed evidence, never by reasoning.
    RESOLVED = "resolved"


class SurveyEvidenceStance(StrEnum):
    """How one piece of evidence bears on an unknown. Both are kept."""

    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"


class BulkAddOutcome(StrEnum):
    """What adding one URL of a bulk add did."""

    #: A new source (or a listed candidate), now in the review queue.
    QUEUED = "queued"
    #: A new source, waiting for its admission card in the inbox.
    PROPOSED = "proposed"
    #: A known source that now serves the workspace too.
    ATTACHED = "attached"
    #: A surveyed source that gained the workspace, and a scoped pass for it.
    RESCOPED = "rescoped"
    #: Already in the library for these workspaces; nothing changed.
    ALREADY = "already"
    #: Nothing was added; `detail` says why.
    FAILED = "failed"


class SurveyQueueAction(StrEnum):
    """`loregarden_survey_queue` actions."""

    ENQUEUE = "enqueue"
    LIST = "list"
    REQUEST_PASS = "request_pass"
    STATUS = "status"
    COVERAGE = "coverage"
    LINK_INITIATIVE = "link_initiative"
    UNLINK_INITIATIVE = "unlink_initiative"
    CHECK_UPSTREAM = "check_upstream"
    REVIEW_NOW = "review_now"


class SurveyPassAction(StrEnum):
    """`loregarden_survey_pass` actions."""

    BRIEF = "brief"
    READS = "reads"
    FINISH = "finish"


class SurveyRecordAction(StrEnum):
    """`loregarden_survey_record` actions."""

    EVIDENCE = "evidence"
    FINDING = "finding"
    RETRACT = "retract"
    UNKNOWN_OPEN = "unknown_open"
    UNKNOWN_UPDATE = "unknown_update"
    UNKNOWNS = "unknowns"


class LibraryState(StrEnum):
    """Where a source stands, in the words the reference library shows.

    Finer than `SurveySourceStatus`: "proposed" splits into waiting on a person
    and an imported candidate nobody has proposed, and "active" into queued and
    being read now, because those lead the operator to different actions.
    """

    CANDIDATE = "candidate"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    READING = "reading"
    SURVEYED = "surveyed"
    REFUSED = "refused"


class LearningsAvailability(StrEnum):
    """Whether a source's linked learnings could be read. "None found" and
    "could not look" must not render the same."""

    FOUND = "found"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class SurveyTopic(StrEnum):
    """What people commonly survey for; the catalog is `reference_survey.topics`."""

    MEMORY_LAYER = "memory_layer"
    UI_UX = "ui_ux"
    SIMILAR_TOOLS = "similar_tools"
    AGENT_ORCHESTRATION = "agent_orchestration"
    TESTING_VERIFICATION = "testing_verification"


class SurveyCardKind(StrEnum):
    """Which inbox card the survey filed (`reference_survey.inbox`).

    The card is keyed by this and its source, not by its title: a title is copy,
    and copy changes.
    """

    #: A proposed source waiting to be admitted. Approve admits it, Reject refuses it.
    ADMISSION = "admission"
    CLONE_FAILED = "clone_failed"
    LICENCE_REFUSED = "licence_refused"
    LICENCE_DISAGREEMENT = "licence_disagreement"
    PASS_ABANDONED = "pass_abandoned"
    PASS_NOT_QUEUED = "pass_not_queued"
    NO_CARRIER_PARENT = "no_carrier_parent"
    DISCOVERY_NOT_QUEUED = "discovery_not_queued"
    WRITE_DID_NOT_LAND = "write_did_not_land"
