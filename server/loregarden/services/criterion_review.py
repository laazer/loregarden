"""A ticket's criteria as the person at its gate should read them.

The review page listed every criterion with a checkbox beside it: 15 to 46 per
ticket on 2026-11-25, nearly all of them facts the agents build and test to. A
person is asked only about the ones settled as theirs; the rest say who
answered for them, or that nobody recorded an answer.
"""

from __future__ import annotations

from loregarden.models.domain import CriterionDisposition, PersonJudgment, Ticket
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.criterion_ledger import settlements_of
from pydantic import BaseModel
from sqlmodel import Session


class ReviewCriterion(BaseModel):
    text: str
    #: None when no one recorded how it was answered for.
    disposition: CriterionDisposition | None = None
    #: PERSON: the question, and the kind of judgment it needs.
    ask: str = ""
    judges: PersonJudgment | None = None
    #: How it was answered for, or why it is a person's.
    reason: str = ""


def review_criteria(session: Session, ticket: Ticket) -> list[ReviewCriterion]:
    settled = settlements_of(session, ticket)
    criteria: list[ReviewCriterion] = []
    for text in load_criteria(ticket.acceptance_criteria_json):
        row = settled.get(text)
        if row is None:
            criteria.append(ReviewCriterion(text=text))
            continue
        criteria.append(
            ReviewCriterion(
                text=text,
                disposition=row.disposition,
                ask=row.person_ask,
                judges=row.judges,
                reason=row.reason,
            )
        )
    return criteria


def gate_criteria_lines(session: Session, ticket: Ticket) -> list[str]:
    """The criteria section of a gate's brief.

    Before any criterion is settled (a plan sign-off) the criteria are the
    contract being approved, so all are listed. After, only what is a person's
    or still unanswered is; the rest is a count.
    """
    criteria = review_criteria(session, ticket)
    if not criteria:
        return []
    if all(c.disposition is None for c in criteria):
        return ["Acceptance criteria:", *(f"- {c.text}" for c in criteria)]
    asks = [f"- For you to judge: {c.ask}" for c in criteria if _is_persons(c)]
    unanswered = [f"- No proof recorded: {c.text}" for c in criteria if c.disposition is None]
    answered = len(criteria) - len(asks) - len(unanswered)
    count = [f"- {answered} of {len(criteria)} answered for by agents"] if answered else []
    return ["Acceptance criteria:", *asks, *unanswered, *count]


def _is_persons(criterion: ReviewCriterion) -> bool:
    return criterion.disposition is CriterionDisposition.PERSON


__all__ = ["ReviewCriterion", "gate_criteria_lines", "review_criteria"]
