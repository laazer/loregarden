"""Vocabulary for the asserted-not-proven debt class.

A claim we believe without proof is a recorded state, distinct from true and
from unrecorded (`services.asserted_not_proven`). Three closed sets describe
one: what kind of member it is, how a member was settled, and — for the reader
— which slice of the class is being asked for. A fourth names the write
operations, because the write tool selects one by name and a bare `str` there
is the stringly-typed vocabulary the py-organization gate exists to refuse.

Its own module rather than `enums`, following the 17 sibling `*_enums.py`
modules. `ClaimStatus` was the spec's name for the reader's filter and could
not be used: `survey_enums.ClaimStatus` already means how far a capability
claim about an OUTSIDE project was checked, and two enums of that name in one
package is the stale-authority-pointer problem this class exists to answer.
"""

from __future__ import annotations

from enum import StrEnum


class AssertedNotProvenKind(StrEnum):
    """What kind of thing is believed without proof.

    Each kind carries a different required set, because each is read for a
    different reason: the kinds are the grouping the enumeration answers by.
    """

    #: Something the system is believed to do, observed but not covered by a test.
    BEHAVIOUR = "behaviour"
    #: A code site that is knowingly insufficient, marked at the site with a
    #: `ceiling:` comment naming the quantity at which it stops holding.
    CODE_SITE = "code_site"
    #: A number drafted rather than derived: frozen, and challenged by a named
    #: artifact class rather than by a role.
    DRAFTED_DEFAULT = "drafted_default"
    #: A defect found in a document this one depends on, routed to a ticket
    #: rather than silently absorbed.
    DOCUMENT_DEFECT = "document_defect"


class ClaimOutcome(StrEnum):
    """How a member left the class. A member with no outcome is still open."""

    #: The owed artifact landed and the claim held.
    PROVEN = "proven"
    #: The claim turned out false, so the named fallback now applies. Recording
    #: this builds nothing: the fallback stays named and unbuilt.
    FALSIFIED = "falsified"
    #: A recorded measurement exceeded the member's `ceiling_quantity`.
    CEILING_REACHED = "ceiling_reached"


class AssertedClaimStatus(StrEnum):
    """Which slice of the class the reader is asking for.

    `OPEN` is the default and admits `closable` members too: the question the
    enumeration answers is "what do we currently believe without proof, **and
    which of those can I close now?**", and a default that hid the closable
    half would answer only the part nobody came for.
    """

    #: Everything still believed without proof — open and closable both.
    OPEN = "open"
    #: Only the members whose owed artifact now exists, or whose ceiling was
    #: exceeded by a recorded measurement.
    CLOSABLE = "closable"
    #: Only the members that left the class, with their outcome.
    SETTLED = "settled"
    #: Every member, whatever its status.
    ALL = "all"


class ClaimOperation(StrEnum):
    """The write operations, selected by name through the one write tool."""

    #: File a new claim. Identical to the stored one: a no-op.
    RECORD = "record"
    #: Change a filed claim, keeping the previous row and pointing at it.
    AMEND = "amend"
    #: Attach a measurement to a CODE_SITE claim's ceiling.
    MEASURE = "measure"
    #: Settle a claim with an outcome.
    CLOSE = "close"
