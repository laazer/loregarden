"""The plan a ticket is being built to, carried forward into later stages.

The plan stage produced no retrievable output. Its reasoning ended up inside a
run-log transcript, which nothing downstream reads, so spec, test-design and
implement each rebuilt the approach from the ticket text — and reached
different answers to questions the planner had already settled.

The plan is now attached as an artifact (see the `plan` skill) and injected
here, so a later stage reads the decision instead of re-deriving it.
"""

from __future__ import annotations

import json
import logging

from loregarden.models.domain import Artifact, Ticket
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

ARTIFACT_KIND = "plan"
MAX_PLAN_CHARS = 6000
#: The stage writing the plan is not shown it. A verifier is withheld too, for
#: the stronger reason that it must not inherit the reasoning it is checking —
#: that exclusion lives with the other verifier starvation in `_build_prompt`,
#: which knows the stage's type rather than just its key.
_EXCLUDED_STAGE_KEYS = frozenset({"plan"})


def latest_plan(session: Session, ticket: Ticket) -> dict | None:
    """The most recent plan artifact for this ticket, or None.

    Newest wins: a reroute back through planning supersedes the plan that was
    rejected, and showing both would leave a later stage to guess which holds.
    """
    rows = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ARTIFACT_KIND)
        .order_by(Artifact.created_at.desc())
    ).all()
    for row in rows:
        try:
            content = json.loads(row.content_json or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(content, dict) and content:
            return content
    return None


def _render(content: dict) -> str:
    """Flatten the plan into prose, whatever shape the planner attached.

    The artifact is free-form JSON, so this cannot assume a schema. Scalars are
    labelled, lists become bullets, and anything nested falls back to its JSON —
    a plan is worth carrying even when it does not match the expected shape.
    """
    lines: list[str] = []
    for key, value in content.items():
        label = str(key).replace("_", " ").strip().capitalize()
        if isinstance(value, str):
            lines += [f"**{label}:** {value}"]
        elif isinstance(value, (int, float, bool)):
            lines += [f"**{label}:** {value}"]
        elif isinstance(value, list):
            lines += [f"**{label}:**"]
            lines += [f"- {item if isinstance(item, str) else json.dumps(item)}" for item in value]
        else:
            lines += [f"**{label}:**", json.dumps(value, indent=2)]
        lines.append("")
    return "\n".join(lines).strip()


def build_plan_context(session: Session, ticket: Ticket, stage_key: str) -> str:
    """The ticket's plan as a prompt block, or "" when there is nothing to show.

    Never raises: a stage that cannot read the plan should proceed on the ticket
    text, exactly as it did before, rather than fail the run.
    """
    if stage_key in _EXCLUDED_STAGE_KEYS:
        return ""
    try:
        content = latest_plan(session, ticket)
    except Exception:  # noqa: BLE001 - a broken read must not fail the run
        logger.warning("Plan context unavailable for ticket %s", ticket.id, exc_info=True)
        return ""
    if not content:
        return ""
    return _render(content)[:MAX_PLAN_CHARS]
