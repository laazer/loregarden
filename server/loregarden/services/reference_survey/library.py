"""The reference library: the survey record shaped for the operator.

It answers one question from their side of the screen: what is waiting to be
surveyed, what have we already read, and what did each source change in our
backlog and memory? The list is every source serving a workspace with the few
fields a person compares; the detail is one source's passes, the tickets it
touched, the learnings recorded on those tickets, and what it left open.

Learnings are linked through tickets: a learning is stored with the ticket it
was recorded on (`memory_nodes.ticket_id`, an external id or a UUID), so a
source's learnings are the ones recorded on the tickets its passes touched.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from loregarden.models.domain import Approval, ApprovalStatus, Ticket, Workspace
from loregarden.models.domain.memory_enums import MemoryNodeType
from loregarden.models.domain.survey_enums import (
    LearningsAvailability,
    LibraryState,
    SurveyPassStatus,
    SurveySourceStatus,
    SurveyUnknownStatus,
)
from loregarden.models.domain.survey_tables import (
    SurveyCoverage,
    SurveyFinding,
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
    SurveySourceWorkspace,
    SurveyUnknown,
)
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.reference_survey.errors import SurveyNotFound
from loregarden.services.reference_survey.initiatives import LinkedInitiative, linked_initiatives
from loregarden.services.reference_survey.passes import coverage_report, pass_counts
from loregarden.services.reference_survey.queue import target_workspaces
from loregarden.services.reference_survey.record import finding_evidence, render_finding
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

#: Enough of a learning's body to name it by its lead sentence.
EXCERPT_CHARS = 300


class LibraryRow(BaseModel):
    id: str
    name: str
    url: str
    kind: str
    state: LibraryState
    licence_name: str
    licence_use: str | None
    workspaces: list[str]
    question: str
    passes: int
    last_read_at: datetime | None
    tickets: int
    unresolved_refs: int
    open_unknowns: int
    approval_id: str | None
    priority: int
    #: Upstream, as of `upstream_checked_at`: the default branch's newest commit
    #: date and how many commits it is ahead of the reviewed one.
    upstream_committed_at: datetime | None
    commits_since_review: int | None
    upstream_checked_at: datetime | None
    upstream_check_error: str


class LibraryList(BaseModel):
    workspace_slug: str
    counts: dict[LibraryState, int]
    sources: list[LibraryRow]


class LibraryWorkspace(BaseModel):
    slug: str
    question: str
    why: str


class LibraryPass(BaseModel):
    ordinal: int
    label: str
    mode: str
    status: str
    question: str
    outcome: str | None
    outcome_note: str
    stopped_at: str
    refused_reason: str
    finished_at: datetime | None
    #: What the pass recorded, as written (the legacy record's text for an
    #: imported pass); "" for a pass that recorded through the tools.
    record: str
    files_read: int
    files_examined: int
    carrier: str | None


class LibraryTicket(BaseModel):
    external_id: str
    title: str
    state: str
    workspace: str
    actions: list[str]
    passes: list[int]


class LibraryFinding(BaseModel):
    id: str
    status: str
    line: str


class LibraryUnknown(BaseModel):
    id: str
    question: str
    authority_required: str


class LibraryLearning(BaseModel):
    id: str
    title: str
    #: The body's opening, so a learning titled only by its ticket can be named
    #: by what it says (the client's `recordTitle` does the same on the map).
    excerpt: str
    ticket: str
    workspace: str
    discredited: bool
    updated_at: str


class LibraryLearnings(BaseModel):
    availability: LearningsAvailability
    reason: str
    items: list[LibraryLearning]


class LibraryCoverage(BaseModel):
    mapped_commit: str
    tree_files: int
    read: int
    partial: int
    skipped: int
    unopened: int


class LibraryDetail(BaseModel):
    source: LibraryRow
    #: Initiatives following this source; its open tickets are their members.
    initiatives: list[LinkedInitiative]
    licence_constraint: str
    pinned_commit: str
    refused_reason: str
    queued_reason: str
    #: What the record says about the source: the survey's own summary for an
    #: imported one (its link note), in full. The reader shows it as markdown.
    record: str
    workspaces: list[LibraryWorkspace]
    passes: list[LibraryPass]
    tickets: list[LibraryTicket]
    unresolved_refs: list[str]
    findings: list[LibraryFinding]
    open_unknowns: list[LibraryUnknown]
    coverage: LibraryCoverage
    learnings: LibraryLearnings


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise SurveyNotFound(f"No workspace {slug!r}")
    return workspace


def _states(session: Session, sources: list[SurveySource]) -> dict[str, LibraryState]:
    ids = [s.id for s in sources]
    card_ids = [s.approval_id for s in sources if s.approval_id]
    pending_cards = set(
        session.exec(
            select(Approval.id).where(
                col(Approval.id).in_(card_ids), Approval.status == ApprovalStatus.PENDING
            )
        ).all()
    )
    reading = set(
        session.exec(
            select(SurveyPass.source_id).where(
                col(SurveyPass.source_id).in_(ids), SurveyPass.status == SurveyPassStatus.DISPATCHED
            )
        ).all()
    )
    states: dict[str, LibraryState] = {}
    for source in sources:
        if source.status is SurveySourceStatus.PROPOSED:
            awaiting = source.approval_id is not None and source.approval_id in pending_cards
            states[source.id] = (
                LibraryState.AWAITING_APPROVAL if awaiting else LibraryState.CANDIDATE
            )
        elif source.status is SurveySourceStatus.ACTIVE:
            states[source.id] = (
                LibraryState.READING if source.id in reading else LibraryState.QUEUED
            )
        elif source.status is SurveySourceStatus.DONE:
            states[source.id] = LibraryState.SURVEYED
        else:
            states[source.id] = LibraryState.REFUSED
    return states


def _ticket_counts(session: Session, ids: list[str]) -> tuple[Counter[str], Counter[str]]:
    resolved: dict[str, set[str]] = defaultdict(set)
    unresolved: dict[str, set[str]] = defaultdict(set)
    rows = session.exec(
        select(SurveyPass.source_id, SurveyPassTicket.ticket_id, SurveyPassTicket.legacy_ref)
        .join(SurveyPassTicket, col(SurveyPassTicket.pass_id) == SurveyPass.id)
        .where(col(SurveyPass.source_id).in_(ids))
    ).all()
    for source_id, ticket_id, legacy_ref in rows:
        if ticket_id:
            resolved[source_id].add(ticket_id)
        else:
            unresolved[source_id].add(legacy_ref)
    return (
        Counter({k: len(v) for k, v in resolved.items()}),
        Counter({k: len(v) for k, v in unresolved.items()}),
    )


def _rows(session: Session, sources: list[SurveySource]) -> list[LibraryRow]:
    ids = [s.id for s in sources]
    states = _states(session, sources)
    pass_stats = {
        source_id: (count, last)
        for source_id, count, last in session.exec(
            select(
                SurveyPass.source_id,
                func.count(col(SurveyPass.id)),
                func.max(SurveyPass.finished_at),
            )
            .where(col(SurveyPass.source_id).in_(ids))
            .group_by(col(SurveyPass.source_id))
        ).all()
    }
    tickets, unresolved = _ticket_counts(session, ids)
    unknowns = Counter(
        session.exec(
            select(SurveyUnknown.source_id).where(
                col(SurveyUnknown.source_id).in_(ids),
                SurveyUnknown.status == SurveyUnknownStatus.OPEN,
            )
        ).all()
    )
    serving: dict[str, list[str]] = defaultdict(list)
    for source_id, slug in session.exec(
        select(SurveySourceWorkspace.source_id, Workspace.slug)
        .join(Workspace, col(Workspace.id) == SurveySourceWorkspace.workspace_id)
        .where(col(SurveySourceWorkspace.source_id).in_(ids))
        .order_by(col(SurveySourceWorkspace.added_at))
    ).all():
        serving[source_id].append(slug)
    rows = []
    for source in sources:
        count, last = pass_stats.get(source.id, (0, None))
        rows.append(
            LibraryRow(
                id=source.id,
                name=source.name or source.url,
                url=source.url,
                kind=source.kind.value,
                state=states[source.id],
                licence_name=source.licence_name,
                licence_use=source.licence_use.value if source.licence_use else None,
                workspaces=serving[source.id],
                question=source.question,
                passes=count,
                last_read_at=last,
                tickets=tickets[source.id],
                unresolved_refs=unresolved[source.id],
                open_unknowns=unknowns[source.id],
                approval_id=source.approval_id
                if states[source.id] is LibraryState.AWAITING_APPROVAL
                else None,
                priority=source.priority,
                upstream_committed_at=source.upstream_committed_at,
                commits_since_review=source.commits_since_review,
                upstream_checked_at=source.upstream_checked_at,
                upstream_check_error=source.upstream_check_error,
            )
        )
    return rows


def _serving(session: Session, workspace: Workspace) -> list[SurveySource]:
    return list(
        session.exec(
            select(SurveySource)
            .join(SurveySourceWorkspace, col(SurveySourceWorkspace.source_id) == SurveySource.id)
            .where(SurveySourceWorkspace.workspace_id == workspace.id)
            .order_by(col(SurveySource.priority), col(SurveySource.name))
        ).all()
    )


def library_row(session: Session, source: SurveySource) -> LibraryRow:
    return _rows(session, [source])[0]


def library_list(session: Session, workspace_slug: str) -> LibraryList:
    """Every source serving the workspace, with counts per state."""
    rows = _rows(session, _serving(session, _workspace(session, workspace_slug)))
    counts = Counter(row.state for row in rows)
    return LibraryList(
        workspace_slug=workspace_slug,
        counts={state: counts[state] for state in LibraryState},
        sources=rows,
    )


def _passes(session: Session, source_id: str) -> list[LibraryPass]:
    passes = session.exec(
        select(SurveyPass)
        .where(SurveyPass.source_id == source_id)
        .order_by(col(SurveyPass.ordinal))
    ).all()
    carriers = {
        t.id: t.external_id
        for t in session.exec(
            select(Ticket).where(
                col(Ticket.id).in_([p.carrier_ticket_id for p in passes if p.carrier_ticket_id])
            )
        ).all()
    }
    views = []
    for p in passes:
        read, examined = pass_counts(session, p.id)
        views.append(
            LibraryPass(
                ordinal=p.ordinal,
                label=p.label,
                mode=p.mode.value,
                status=p.status.value,
                question=p.question,
                outcome=p.outcome.value if p.outcome else None,
                outcome_note=p.outcome_note,
                stopped_at=p.stopped_at,
                refused_reason=p.refused_reason,
                finished_at=p.finished_at,
                record=p.legacy_text,
                files_read=read,
                files_examined=examined,
                carrier=carriers.get(p.carrier_ticket_id or ""),
            )
        )
    return views


def _tickets(session: Session, source_id: str) -> tuple[list[LibraryTicket], list[str]]:
    rows = session.exec(
        select(SurveyPassTicket, SurveyPass.ordinal, Ticket, Workspace.slug)
        .join(SurveyPass, col(SurveyPass.id) == SurveyPassTicket.pass_id)
        .join(Ticket, col(Ticket.id) == SurveyPassTicket.ticket_id, isouter=True)
        .join(Workspace, col(Workspace.id) == Ticket.workspace_id, isouter=True)
        .where(SurveyPass.source_id == source_id)
        .order_by(col(SurveyPass.ordinal))
    ).all()
    by_ticket: dict[str, LibraryTicket] = {}
    unresolved: list[str] = []
    for link, ordinal, ticket, slug in rows:
        if ticket is None:
            if link.legacy_ref not in unresolved:
                unresolved.append(link.legacy_ref)
            continue
        view = by_ticket.setdefault(
            ticket.id,
            LibraryTicket(
                external_id=ticket.external_id,
                title=ticket.title,
                state=ticket.state.value,
                workspace=slug or "",
                actions=[],
                passes=[],
            ),
        )
        if link.action.value not in view.actions:
            view.actions.append(link.action.value)
        if ordinal not in view.passes:
            view.passes.append(ordinal)
    return list(by_ticket.values()), unresolved


def _learnings(
    session: Session, source: SurveySource, tickets: list[LibraryTicket]
) -> LibraryLearnings:
    """Learnings recorded on the tickets this source touched, from each served
    workspace's memory graph. Unavailable is its own answer, never an empty list."""
    if not tickets:
        return LibraryLearnings(
            availability=LearningsAvailability.EMPTY, reason="No linked tickets.", items=[]
        )
    ids = session.exec(
        select(Ticket.id, Ticket.external_id).where(
            col(Ticket.external_id).in_([t.external_id for t in tickets])
        )
    ).all()
    refs = {ref for pair in ids for ref in pair}
    by_external = dict(ids)
    service = AgentMemoryService.from_settings()
    items: list[LibraryLearning] = []
    unavailable: list[str] = []
    #: Records in each graph searched: the denominator a "none found" is read against.
    searched: list[str] = []
    for workspace, _link in target_workspaces(session, source.id):
        try:
            graph = service.require_graph(workspace.slug)
        except ValueError as exc:  # silent-ok: not swallowed; the reason is returned and the library shows "could not read learnings"
            unavailable.append(f"{workspace.slug}: {exc}")
            continue
        placeholders = ",".join("?" for _ in refs)
        with graph.connection() as conn:
            total = conn.execute("SELECT count(*) FROM memory_nodes").fetchone()[0]
            searched.append(
                f"the {workspace.slug} memory graph holds {total} record{'' if total == 1 else 's'}"
            )
            node_ids = [
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM memory_nodes WHERE node_type = ? AND ticket_id IN ({placeholders})",  # noqa: S608 - placeholders only
                    (MemoryNodeType.LEARNING.value, *refs),
                ).fetchall()
            ]
        for node_id in node_ids:
            node: dict[str, Any] | None = graph.get_node(node_id)
            if node is None:
                continue
            ref = str(node.get("ticket_id", ""))
            items.append(
                LibraryLearning(
                    id=node_id,
                    title=str(node.get("title", "")),
                    excerpt=str(node.get("body", ""))[:EXCERPT_CHARS],
                    ticket=by_external.get(ref, ref),
                    workspace=workspace.slug,
                    discredited=bool(node.get("discredited")),
                    updated_at=str(node.get("updated_at", "")),
                )
            )
    items.sort(key=lambda item: item.updated_at, reverse=True)
    if items:
        reason = "; ".join(unavailable)
        return LibraryLearnings(
            availability=LearningsAvailability.FOUND, reason=reason, items=items
        )
    if unavailable:
        return LibraryLearnings(
            availability=LearningsAvailability.UNAVAILABLE, reason="; ".join(unavailable), items=[]
        )
    return LibraryLearnings(
        availability=LearningsAvailability.EMPTY,
        reason=f"No learnings are recorded on its {len(tickets)} linked tickets; {', '.join(searched)}.",
        items=[],
    )


def library_detail(session: Session, source_id: str) -> LibraryDetail:
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    tickets, unresolved = _tickets(session, source.id)
    findings = session.exec(
        select(SurveyFinding)
        .where(SurveyFinding.source_id == source.id)
        .order_by(col(SurveyFinding.created_at))
    ).all()
    unknowns = session.exec(
        select(SurveyUnknown).where(
            SurveyUnknown.source_id == source.id, SurveyUnknown.status == SurveyUnknownStatus.OPEN
        )
    ).all()
    report = coverage_report(session, source.id)
    has_map = bool(
        session.exec(select(SurveyCoverage.id).where(SurveyCoverage.source_id == source.id)).first()
    )
    return LibraryDetail(
        source=_rows(session, [source])[0],
        initiatives=linked_initiatives(session, source.id),
        licence_constraint=source.licence_constraint,
        pinned_commit=source.pinned_commit,
        refused_reason=source.refused_reason,
        queued_reason=source.queued_reason,
        record=source.notes,
        workspaces=[
            LibraryWorkspace(slug=w.slug, question=link.question, why=link.queued_reason)
            for w, link in target_workspaces(session, source.id)
        ],
        passes=_passes(session, source.id),
        tickets=tickets,
        unresolved_refs=unresolved,
        findings=[
            LibraryFinding(
                id=f.id,
                status=f.status.value,
                line=render_finding(f, finding_evidence(session, f.id)),
            )
            for f in findings
        ],
        open_unknowns=[
            LibraryUnknown(id=u.id, question=u.question, authority_required=u.authority_required)
            for u in unknowns
        ],
        coverage=LibraryCoverage(
            mapped_commit=report["mapped_commit"] if has_map else "",
            tree_files=report["tree_files"],
            read=report["files_in_read_leaves"],
            partial=report["files_in_partial_leaves"],
            skipped=report["files_in_skipped_leaves"],
            unopened=report["files_in_unopened_leaves"],
        ),
        learnings=_learnings(session, source, tickets),
    )
