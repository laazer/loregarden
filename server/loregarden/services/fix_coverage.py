"""How a bug ticket's fix covers the whole bad state, recorded before the work starts.

A fix has four jobs: repair the rows already in the state, catch rows that
enter it anyway, prevent entry, and say why the state was reachable
(`services.invariants`). The PR's "Fix coverage" section asks for all four
(`.github/scripts/pr_fix_coverage_check.py`), but a check that runs on the PR
finds out after the work is done, which is when an answer costs the most to change.

So the spec stage decides them for a bug ticket, as acceptance criteria and as
this record, and its pass is sent back without one (`missing_fix_coverage`).
The record is validated by the PR check's own shape rules when it is written.
Two of the PR check's rules are *not* applied here, on purpose: at spec time the
invariant and the prevention test are planned, not written, so the record names
them and the PR check later proves they exist.

The learning is written to the memory graph by the same call
(`mcp.fix_coverage_tools`), so a recorded fix always leaves one.
"""

from __future__ import annotations

import json
import re

from loregarden.agents.registry import SPEC_AGENT_ID
from loregarden.models.domain import Artifact, ArtifactKind, Ticket, WorkItemType
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sqlmodel import Session, col, select

# Mirrors `.github/scripts/pr_fix_coverage_check.py`, which cannot be imported
# from here (it runs from the repository in CI, with no loregarden install); a
# test runs both on the same cases so the two cannot drift.
MIN_ANSWER = 10
MIN_WAIVER = 20
NO_DETECTOR = "no detector:"
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")
_TEST_REF = re.compile(r"(?P<path>[\w./-]+\.py)::(?P<name>test_\w+)")

RECORD_TOOL = "loregarden_record_fix_coverage"


def _answered(value: str, minimum: int) -> bool:
    return len(value) >= minimum and not value.startswith("<")


class FixCoverage(BaseModel):
    """The four answers, or why the fix leaves no state behind. One line each:
    the PR section reads a field to the end of its line."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    existing: str = ""
    reentry: str = ""
    prevention: str = ""
    learning: str = ""
    not_a_state_bug: str = ""

    @model_validator(mode="after")
    def _as_the_check_reads_them(self) -> FixCoverage:
        answers = {
            "existing": self.existing,
            "reentry": self.reentry,
            "prevention": self.prevention,
            "learning": self.learning,
        }
        if any("\n" in value for value in (*answers.values(), self.not_a_state_bug)):
            raise ValueError(
                "each answer is one line; the PR section reads a field to its line end"
            )
        if self.not_a_state_bug:
            if any(answers.values()):
                raise ValueError("give either the four answers or not_a_state_bug, not both")
            if not _answered(self.not_a_state_bug, MIN_WAIVER):
                raise ValueError(
                    f"not_a_state_bug needs a real reason ({MIN_WAIVER}+ characters, "
                    "not a placeholder)"
                )
            return self
        for name, value in answers.items():
            if not _answered(value, MIN_ANSWER):
                raise ValueError(
                    f"{name} is missing or a placeholder ({MIN_ANSWER}+ characters); "
                    "or give not_a_state_bug instead of all four"
                )
        self._check_reentry()
        if not _TEST_REF.search(self.prevention):
            raise ValueError(
                "prevention names no test: give it as `path/to/test_file.py::test_name`"
            )
        return self

    def _check_reentry(self) -> None:
        if self.reentry.lower().startswith(NO_DETECTOR):
            if not _answered(self.reentry[len(NO_DETECTOR) :].strip(), MIN_WAIVER):
                raise ValueError(
                    f"reentry '{NO_DETECTOR}' needs a real reason ({MIN_WAIVER}+ characters)"
                )
            return
        if not _BACKTICKED.search(self.reentry):
            raise ValueError(
                "reentry names no invariant: put its name (as it is or will be registered in "
                f"services/invariant_registry.py) in backticks, or write '{NO_DETECTOR} <reason>'"
            )

    @property
    def invariants(self) -> list[str]:
        """The invariant names the re-entry answer gives."""
        return _BACKTICKED.findall(self.reentry)

    @property
    def lines(self) -> list[str]:
        """The answers as the PR section's field lines."""
        if self.not_a_state_bug:
            return [f"- **Not a state bug:** {self.not_a_state_bug}"]
        return [
            f"- **Existing:** {self.existing}",
            f"- **Re-entry:** {self.reentry}",
            f"- **Prevention:** {self.prevention}",
            f"- **Learning:** {self.learning}",
        ]


def parse_fix_coverage(values: dict[str, str]) -> FixCoverage:
    """Validate answers, raising ``ValueError`` naming every reason they were refused."""
    try:
        return FixCoverage.model_validate(values)
    except ValidationError as exc:
        reasons = "; ".join(str(error["msg"]) for error in exc.errors())
        raise ValueError(reasons) from exc


def needs_fix_coverage(ticket: Ticket) -> bool:
    return ticket.work_item_type is WorkItemType.BUG


def missing_fix_coverage(session: Session, ticket: Ticket, *, agent_id: str) -> str:
    """Why a passing run of ``agent_id`` cannot pass ``ticket`` yet, or "".

    The spec stage is where a bug's acceptance criteria are written, so it is
    where the four answers are decided. A pass that recorded none is sent back
    to the stage with this as the reason.
    """
    if agent_id != SPEC_AGENT_ID or not needs_fix_coverage(ticket):
        return ""
    try:
        if latest_fix_coverage(session, ticket) is not None:
            return ""
        problem = f"No fix coverage is recorded for bug {ticket.external_id}"
    except ValueError as exc:
        problem = str(exc)
    return (
        f"{problem}, and this stage cannot pass without it. Decide how the fix covers the "
        "whole state: existing (how rows already in it are found and repaired, or the query "
        "showing none), reentry (the invariant that will catch it coming back, in backticks), "
        "prevention (the test proving it cannot be entered, as path::test_name) and learning "
        "(why it was reachable). Write each as an acceptance criterion, call "
        f"`{RECORD_TOOL}` with them (or with not_a_state_bug when the fix leaves no state "
        "behind), then report pass again."
    )


def latest_fix_coverage(session: Session, ticket: Ticket) -> FixCoverage | None:
    """The answers last recorded for ``ticket``, or None when it has none.

    A recorded artifact that no longer validates raises ``ValueError`` naming
    it: this module wrote it, so a row that fails is a defect, not an answer.
    """
    artifact = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.FIX_COVERAGE)
        .order_by(col(Artifact.created_at).desc())
    ).first()
    if artifact is None:
        return None
    try:
        return FixCoverage.model_validate(json.loads(artifact.content_json or "{}"))
    except (ValidationError, ValueError) as exc:
        raise ValueError(
            f"{ticket.external_id}'s recorded fix coverage ({artifact.id}) no longer validates: "
            f"{exc}"
        ) from exc
