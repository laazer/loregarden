"""Exit-action vocabularies — split from enums.py for the size gate."""

from enum import StrEnum


class ExitActionRequirementKind(StrEnum):
    RUNTIME_CAPABILITY = "runtime_capability"
    CREDENTIAL = "credential"
    AUTHORITY = "authority"
    OPERATOR_JUDGMENT = "operator_judgment"


class ExitActionReasonCode(StrEnum):
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    CAPABILITY_STATUS_UNKNOWN = "capability_status_unknown"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    CREDENTIAL_STATUS_UNKNOWN = "credential_status_unknown"
    AUTHORITY_DENIED = "authority_denied"
    AUTHORITY_STATUS_UNKNOWN = "authority_status_unknown"
    AUTHORITY_GRANT_REQUIRED = "authority_grant_required"
    OPERATOR_JUDGMENT_REQUIRED = "operator_judgment_required"


class ExitActionResolutionMode(StrEnum):
    APPROVE = "approve"
    RECHECK = "recheck"


class ApprovalResolutionAction(StrEnum):
    APPROVE = "approve"
    RECHECK = "recheck"
    REJECT = "reject"


class RuntimeAvailability(StrEnum):
    """Whether a runtime resource was observed present for the dispatched run.

    Absence of a key is a third state — unknown — and is deliberately not a
    member here: it must never be spellable as "we checked and it is fine".
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class AuthorityStatus(StrEnum):
    """Policy standing for an authority scope on the dispatched run.

    ``GRANTABLE`` is the only status an operator can clear by approving: the
    policy allows the grant and only a person has to make it. ``DENIED`` means
    policy refuses it; omission is unknown.
    """

    GRANTED = "granted"
    GRANTABLE = "grantable"
    DENIED = "denied"


class StageReportStatus(StrEnum):
    """Outcome of a stage agent's structured report sentinel."""

    PASS = "pass"
    FAIL = "fail"
    NEEDS_REWORK = "needs_rework"
    BLOCKED = "blocked"
