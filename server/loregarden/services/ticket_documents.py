"""Documents on a ticket: planning artifacts that are written outside any run.

An artifact could only be attached through an orchestration run, and the run's
ticket was the one it landed on. Initiatives never orchestrate and a milestone
with children never runs its own stages, so the architecture, domain model and
decision records a brief is decomposed into had nowhere to live — the plan for a
product spanning three workspaces could only be kept as repo markdown, which the
rest of this control plane cannot see. Nor could an agent read any artifact
back: the feed was REST-only.

A document is an ordinary `artifacts` row with ``run_id`` unset and a markdown
body. Rewriting one inserts a new row under the same ticket, kind and title, so
every version survives and the newest wins — the convention `plan_context`
already applies to plans, made explicit here with a version number and a
pointer to the row it replaced.

Readers walk up the hierarchy: a task's agent sees what its capability, feature,
milestone and initiative carry, because that is where the documents describing
its context are written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import Artifact, EventType, Ticket, Workspace
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

#: The kind a document is filed under when the writer does not name one.
DEFAULT_DOCUMENT_KIND = "document"

#: Largest markdown body one document version may carry. The column has no cap
#: and the largest live row is a 320 KB log; a planning document is prose an
#: agent reads into its context, so one that outgrows this should be split.
MAX_DOCUMENT_CHARS = 200_000

#: Hierarchy depth is initiative → milestone → feature → capability → task, so
#: a walk longer than this means a parent cycle, not a deep tree.
_MAX_ANCESTOR_DEPTH = 16

#: Rows `document_index` looks through before filtering to documents. Run
#: artifacts on the chain are counted against it, so it is generous.
_MAX_INDEX_SCAN = 500


@dataclass(frozen=True)
class DocumentWrite:
    artifact: Artifact
    version: int
    supersedes: str | None


def write_document(
    session: Session,
    ticket: Ticket,
    *,
    title: str,
    body: str,
    kind: str = DEFAULT_DOCUMENT_KIND,  # py-org: allow-string - artifact kinds are agent-supplied and open-ended (see ArtifactKind)
    summary: str = "",
) -> DocumentWrite:
    """Store a new version of the (ticket, kind, title) document."""
    title = title.strip()
    kind = kind.strip() or DEFAULT_DOCUMENT_KIND
    if not title:
        raise ValueError("A document needs a title; it is the key later versions are filed under.")
    if not body.strip():
        raise ValueError("A document needs a body. To retire one, write a version saying so.")
    if len(body) > MAX_DOCUMENT_CHARS:
        raise ValueError(
            f"Document body is {len(body)} characters; the limit is {MAX_DOCUMENT_CHARS}. "
            "Split it into several documents under the same ticket."
        )

    previous = _versions(session, ticket.id, kind, title)
    supersedes = previous[0].id if previous else None
    version = len(previous) + 1
    content = {
        "format": "markdown",
        "body": body,
        "summary": summary.strip(),
        "version": version,
        "supersedes": supersedes,
    }
    artifact = Artifact(
        ticket_id=ticket.id,
        run_id=None,
        kind=kind,
        title=title,
        content_json=json.dumps(content),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    # The artifacts panel refreshes on this event; without it a document written
    # while the panel is open would not appear until the operator reloads.
    event_bus.publish(
        session,
        EventType.ARTIFACT_CREATED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        artifact_id=artifact.id,
        payload={"kind": kind},
    )
    return DocumentWrite(artifact=artifact, version=version, supersedes=supersedes)


def _versions(
    session: Session,
    ticket_id: str,
    kind: str,  # py-org: allow-string - artifact kinds are agent-supplied and open-ended (see ArtifactKind)
    title: str,
) -> list[Artifact]:
    """Every row filed under (ticket, kind, title), newest first."""
    return list(
        session.exec(
            select(Artifact)
            .where(
                Artifact.ticket_id == ticket_id,
                Artifact.kind == kind,
                Artifact.title == title,
            )
            .order_by(col(Artifact.created_at).desc(), col(Artifact.id).desc())
        ).all()
    )


def ancestor_chain(session: Session, ticket: Ticket) -> list[Ticket]:
    """``ticket`` and each parent above it, nearest first."""
    chain = [ticket]
    seen = {ticket.id}
    current = ticket
    while current.parent_ticket_id and len(chain) <= _MAX_ANCESTOR_DEPTH:
        parent = session.get(Ticket, current.parent_ticket_id)
        if parent is None or parent.id in seen:
            break
        chain.append(parent)
        seen.add(parent.id)
        current = parent
    return chain


class _DocumentFields(BaseModel):
    """The two fields a listing reads out of any artifact's content.

    Run artifacts carry arbitrary JSON, so anything that is not an object with
    these keys reads as their defaults rather than failing the listing.
    """

    model_config = ConfigDict(extra="ignore")

    format: str = ""
    summary: str = ""


def _fields(artifact: Artifact) -> _DocumentFields:
    try:
        return _DocumentFields.model_validate_json(artifact.content_json or "{}")
    except ValidationError:
        # silent-ok: a run artifact that is not a document has neither field; defaults are the answer
        return _DocumentFields()


def _content(artifact: Artifact) -> Any:
    raw = artifact.content_json or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # silent-ok: unparseable content is returned verbatim under _raw, as the feed does
        return {"_raw": raw}


def _is_document(artifact: Artifact, fields: _DocumentFields) -> bool:
    """Written by `write_document`, as against produced by a run."""
    return artifact.run_id is None and fields.format == "markdown"


def _ticket_label(session: Session, ticket: Ticket) -> dict[str, str]:
    workspace = session.get(Workspace, ticket.workspace_id) if ticket.workspace_id else None
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "workspace": workspace.slug if workspace is not None else "",
        "work_item_type": ticket.work_item_type.value,
        "ticket_title": ticket.title,
    }


def list_artifacts(
    session: Session,
    ticket: Ticket,
    *,
    kind: str = "",  # py-org: allow-string - artifact kinds are agent-supplied and open-ended (see ArtifactKind)
    include_ancestors: bool = True,
    latest_only: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Artifact metadata on ``ticket`` (and its ancestors), without bodies.

    ``latest_only`` collapses each (ticket, kind, title) to its newest row and
    reports how many versions it has. Bodies are left out on purpose: a listing
    that inlined them would put every log a ticket ever produced into the
    caller's context. Read one with `read_artifact`.
    """
    chain = ancestor_chain(session, ticket) if include_ancestors else [ticket]
    items: list[dict[str, Any]] = []
    for depth, holder in enumerate(chain):
        query = select(Artifact).where(Artifact.ticket_id == holder.id)
        if kind:
            query = query.where(Artifact.kind == kind)
        rows = session.exec(
            query.order_by(col(Artifact.created_at).desc(), col(Artifact.id).desc())
        ).all()
        counts: dict[tuple[str, str], int] = {}
        for row in rows:
            counts[(row.kind, row.title)] = counts.get((row.kind, row.title), 0) + 1
        emitted: set[tuple[str, str]] = set()
        label = _ticket_label(session, holder)
        for row in rows:
            key = (row.kind, row.title)
            if latest_only and key in emitted:
                continue
            emitted.add(key)
            fields = _fields(row)
            items.append(
                {
                    "artifact_id": row.id,
                    "depth": depth,
                    **label,
                    "kind": row.kind,
                    "title": row.title,
                    "run_id": row.run_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "content_bytes": len((row.content_json or "").encode("utf-8")),
                    "summary": fields.summary,
                    "versions": counts[key],
                    "is_document": _is_document(row, fields),
                }
            )
    total = len(items)
    return {
        "ticket": _ticket_label(session, ticket),
        "searched": [holder.external_id for holder in chain],
        "total": total,
        "truncated": total > limit,
        "items": items[:limit],
    }


def document_index(session: Session, ticket: Ticket, *, limit: int = 20) -> list[dict[str, Any]]:
    """The documents on ``ticket`` and its ancestors, as `get_ticket` reports them.

    Compact on purpose — id, where it sits, what it is — because `get_ticket` is
    read on every stage. The listing tool carries the rest.
    """
    listed = list_artifacts(session, ticket, limit=_MAX_INDEX_SCAN)
    return [
        {
            "artifact_id": item["artifact_id"],
            "external_id": item["external_id"],
            "depth": item["depth"],
            "kind": item["kind"],
            "title": item["title"],
            "summary": item["summary"],
        }
        for item in listed["items"]
        if item["is_document"]
    ][:limit]


def read_artifact(session: Session, artifact_id: str) -> dict[str, Any]:
    """One artifact in full, and whether a newer version of it exists."""
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact not found: {artifact_id}")
    ticket = session.get(Ticket, artifact.ticket_id)
    if ticket is None:
        raise ValueError(f"Artifact {artifact_id} names a ticket that no longer exists.")
    latest = _versions(session, artifact.ticket_id, artifact.kind, artifact.title)[0]
    return {
        "artifact_id": artifact.id,
        **_ticket_label(session, ticket),
        "kind": artifact.kind,
        "title": artifact.title,
        "run_id": artifact.run_id,
        "evidence_kind": artifact.evidence_kind,
        "commit_sha": artifact.commit_sha,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        # A reader handed an old id must be told, or it will act on a superseded plan.
        "is_latest": latest.id == artifact.id,
        "latest_artifact_id": latest.id,
        "content": _content(artifact),
    }
