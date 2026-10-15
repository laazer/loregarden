"""What a ticket's user-facing change is for, recorded where a publisher can read it.

A PR that changes `client/src/pages` or `client/src/components` must say the
question the surface answers, the action it leads to, and what it showed on
real data, or why nobody can see the change (`.github/scripts/
pr_ux_section_check.py`, the "PR UX section" check). The `ui-design` stage
decides those answers, and wrote them only as free-text acceptance criteria.
So a tree's PR, written by `publish_tree` rather than by a person, had no
answers to give: #622 carried 917's surface changes and failed the check until
someone wrote the section by hand (2026-10-15).

The answers are recorded as a `ux_answers` artifact, validated by the check's
own rules when they are written: a bad answer is refused then, not discovered
in CI. `tree_ux_section` assembles a tree's PR section from them.
"""

from __future__ import annotations

import json
import re

from loregarden.agents.registry import UI_DESIGN_AGENT_ID
from loregarden.models.domain import Artifact, ArtifactKind, Ticket
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sqlmodel import Session, col, select

# Mirrors `.github/scripts/pr_ux_section_check.py`, which cannot be imported from
# here (it runs from the repository in CI, with no loregarden install); a test
# runs both on the same cases so the two cannot drift.
SURFACE_DIRS = ("client/src/pages/", "client/src/components/")
SURFACE_SUFFIXES = (".tsx", ".css")
MIN_ANSWER = 10
MIN_WAIVER = 20


def is_surface(path: str) -> bool:
    """Whether a changed path is one the PR UX check asks about."""
    if not path.startswith(SURFACE_DIRS) or not path.endswith(SURFACE_SUFFIXES):
        return False
    return "/__tests__/" not in path and ".test." not in path


def _answered(value: str, minimum: int) -> bool:
    return len(value) >= minimum and not value.startswith("<")


class UxAnswers(BaseModel):
    """The three answers, or the reason there are none. One line each: the check
    reads a field to the end of its line."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = ""
    action: str = ""
    real_data: str = ""
    no_change_reason: str = ""

    @model_validator(mode="after")
    def _as_the_check_reads_them(self) -> UxAnswers:
        values = {
            "question": self.question,
            "action": self.action,
            "real_data": self.real_data,
            "no_change_reason": self.no_change_reason,
        }
        if any("\n" in value for value in values.values()):
            raise ValueError(
                "each answer is one line; the PR section reads a field to its line end"
            )
        if self.no_change_reason:
            if self.question or self.action or self.real_data:
                raise ValueError(
                    "give either question, action and real_data, or no_change_reason, not both"
                )
            if not _answered(self.no_change_reason, MIN_WAIVER):
                raise ValueError(
                    f"no_change_reason needs a real reason ({MIN_WAIVER}+ characters, "
                    "not a placeholder)"
                )
            return self
        for name in ("question", "action", "real_data"):
            if not _answered(values[name], MIN_ANSWER):
                raise ValueError(
                    f"{name} is missing or a placeholder ({MIN_ANSWER}+ characters); "
                    "or give no_change_reason instead of all three"
                )
        if not re.search(r"\d", self.real_data):
            raise ValueError(
                "real_data has no number in it: say what the sandbox showed (how many rows, the "
                "largest case), not that it looked fine"
            )
        return self

    @property
    def lines(self) -> list[str]:
        """The answers as the PR section's field lines."""
        if self.no_change_reason:
            return [f"- **No user-visible change:** {self.no_change_reason}"]
        return [
            f"- **Question:** {self.question}",
            f"- **Action:** {self.action}",
            f"- **Real data:** {self.real_data}",
        ]


def missing_ux_answers(session: Session, ticket: Ticket, *, agent_id: str) -> str:
    """Why a passing run of ``agent_id`` cannot pass ``ticket`` yet, or "".

    The design stage is the one that decides the answers, and it runs only on a
    ticket whose plan names UI paths. A pass that recorded none left the PR to
    find out in CI; it is sent back to the stage instead, with this as the reason.
    """
    if agent_id != UI_DESIGN_AGENT_ID:
        return ""
    try:
        if latest_ux_answers(session, ticket) is not None:
            return ""
        problem = f"No UX answers are recorded for {ticket.external_id}"
    except ValueError as exc:
        problem = str(exc)
    return (
        f"{problem}, and this stage cannot pass without them: the ticket's PR builds its "
        "'User-facing surfaces' section from them. Call `loregarden_record_ux_answers` with "
        "question, action and real_data, or with no_change_reason when nobody can see the "
        "change, then report pass again."
    )


def latest_ux_answers(session: Session, ticket: Ticket) -> UxAnswers | None:
    """The answers last recorded for ``ticket``, or None when it has none.

    A recorded artifact that no longer validates raises ``ValueError`` naming
    it: this module wrote it, so a row that fails is a defect, not an answer, and
    the caller reports it rather than reading it as "no answers".
    """
    artifact = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.UX_ANSWERS)
        .order_by(col(Artifact.created_at).desc())
    ).first()
    if artifact is None:
        return None
    try:
        return UxAnswers.model_validate(json.loads(artifact.content_json or "{}"))
    except (ValidationError, ValueError) as exc:
        raise ValueError(
            f"{ticket.external_id}'s recorded UX answers ({artifact.id}) no longer validate: {exc}"
        ) from exc
