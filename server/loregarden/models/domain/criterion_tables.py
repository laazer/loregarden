"""How each acceptance criterion of a ticket was answered for.

794 done tickets carried 4292 criteria when this landed, and not one criterion
had ever been recorded as met: a ticket finished when its stages passed, and
nothing asked, criterion by criterion, what had met each one. So a ticket's
criteria now end in one of three settlements before it may enter its gate
(`services.criterion_ledger`):

- ``MET``: proven by named tests, which the gate check reruns at HEAD, or by an
  evidence artifact captured at HEAD.
- ``DELEGATED``: handed to another ticket, which carries the criterion's exact
  text as its own and which the parent waits for.
- ``PERSON``: a judgment no test or capture can make, put to the person at the
  ticket's human gate as a plain question (`services.person_judgment`).

A settlement is keyed by the criterion's text, as ``checked_criteria_json`` is.
Editing a criterion's wording therefore voids its settlement, and the changed
criterion has to be proven again.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class CriterionDisposition(str, Enum):
    """How one acceptance criterion was answered for."""

    MET = "met"
    DELEGATED = "delegated"
    PERSON = "person"


class PersonJudgment(StrEnum):
    """Why a person, and not a test or a capture, has to answer."""

    #: How it feels to operate: timing, weight, fairness, fun.
    FEEL = "feel"
    #: Whether it looks right or good, beyond what a capture can be compared to.
    LOOK = "look"
    #: Whether it sounds right.
    SOUND = "sound"
    #: Whether someone meeting it understands it without being told.
    CLARITY = "clarity"
    #: A choice that is the owner's: taste, scope, wording, policy.
    CHOICE = "choice"
    #: Hardware or an account only a person has.
    PHYSICAL = "physical"


class CriterionSettlement(SQLModel, table=True):
    __tablename__ = "criterion_settlements"
    __table_args__ = (UniqueConstraint("ticket_id", "criterion", name="uq_criterion_settlement"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    #: The criterion's text, exactly as the ticket stores it.
    criterion: str
    disposition: CriterionDisposition = Field(
        sa_column=str_enum_column(CriterionDisposition, CriterionDisposition.MET)
    )
    #: MET: the tests that prove it, as a JSON array of test ids (a pytest node
    #: id, or a jest ``path::"exact name"``). The gate check reruns them at HEAD.
    tests_json: str = "[]"
    #: MET without tests: the evidence artifact that proves it.
    evidence_artifact_id: str | None = Field(default=None, foreign_key="artifacts.id")
    #: The commit the proof was recorded against.
    commit_sha: str = ""
    #: DELEGATED: the ticket that now carries the criterion.
    delegated_to_ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    #: DELEGATED by the control plane splitting off criteria a ticket could not
    #: meet within its rework budget. A criterion is split at most once: failing
    #: again in the ticket it was split into is a block for a person.
    by_split: bool = False
    #: PERSON: the kind of judgment, and the question the person answers.
    judges: PersonJudgment | None = Field(
        default=None, sa_column=str_enum_column(PersonJudgment, None, nullable=True)
    )
    person_ask: str = ""
    #: ``run:<id>``, ``operator`` or ``control_plane``.
    settled_by: str = ""
    settled_at: datetime = Field(default_factory=utcnow)
    reason: str = ""


__all__ = ["CriterionDisposition", "CriterionSettlement", "PersonJudgment"]
