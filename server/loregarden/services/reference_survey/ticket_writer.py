"""Land survey findings on tickets, with every rule that has already bitten us.

The method (`agent_context/skills/reference-survey/references/writing-findings.md`)
asks for two writes per ticket: the finding as a block in the ticket's
"Prior art — reference survey" document, and a pointer to it in the description.
This module does both, plus the criteria, and enforces what an agent kept getting
wrong by hand:

- Criteria are written through `update_ticket_manual`, the path MCP
  `loregarden_update_ticket` uses. `PATCH /api/tickets` once dropped them and
  answered 2xx.
- Every write is read back and compared byte-exact; a success code proves nothing.
- At the criteria cap the finding goes to a new sibling related to the target,
  never a 13th criterion and never a rewrite (`MAX_ACCEPTANCE_CRITERIA`).
- A done ticket's criteria are frozen, so the finding goes to a new ticket
  related to it.
- Hierarchy is checked before creating, and creating while a similar open ticket
  exists needs a stated reason: appending beats creating about half the time.
- Each block names what it rests on, rendered so observed, inferred, absent and
  not-found never read alike.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loregarden.models.domain import Artifact, Ticket, TicketState, UpdateTicketRequest, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    LicenceUse,
    SurveyFindingStatus,
    SurveyPassStatus,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import (
    SurveyFinding,
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
)
from loregarden.services.acceptance_criteria import (
    MAX_ACCEPTANCE_CRITERIA,
    load_criteria,
    merge_criteria,
)
from loregarden.services.hierarchy_service import validate_parent_assignment
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.reference_survey.errors import (
    SurveyError,
    SurveyNotFound,
    TicketWriteMismatch,
)
from loregarden.services.reference_survey.inbox import report_exception
from loregarden.services.reference_survey.initiatives import follow_new_tickets
from loregarden.services.reference_survey.queue import target_workspaces
from loregarden.services.reference_survey.record import finding_evidence, render_finding
from loregarden.services.reference_survey.schemas import NewTicketIn, TicketWriteIn
from loregarden.services.ticket_documents import write_document
from loregarden.services.ticket_ids import TicketNotFound, resolve_one
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, col, select

DOCUMENT_TITLE = "Prior art — reference survey"
DOCUMENT_KIND = "finding"
POINTER_HEADER = "## Prior art — moved to a document"
SELF_DEFECT_TAG = "survey-self-defect"

_CLOSED = (TicketState.DONE, TicketState.WONT_DO)
_TERM_RE = re.compile(r"[a-z0-9]{4,}")
#: Share of a new title's terms an open ticket must carry to count as a candidate.
SIMILARITY_THRESHOLD = 0.5


@dataclass
class TicketWriteResult:
    ticket: Ticket
    action: SurveyTicketAction
    aimed_at: Ticket | None
    criteria_added: list[str]
    document_artifact_id: str
    document_chars_before: int
    document_chars_after: int
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ticket": self.ticket.external_id,
            "action": self.action.value,
            "aimed_at": self.aimed_at.external_id if self.aimed_at else None,
            "criteria_added": self.criteria_added,
            "criteria_total": len(load_criteria(self.ticket.acceptance_criteria_json)),
            "document_artifact_id": self.document_artifact_id,
            "document_chars": [self.document_chars_before, self.document_chars_after],
            "verified": True,
            "notes": self.notes,
        }


def _terms(text: str) -> set[str]:
    return set(_TERM_RE.findall(text.lower()))


def similar_open_tickets(
    session: Session, workspace_id: str, title: str, limit: int = 5
) -> list[Ticket]:
    """Open tickets whose titles carry most of this title's terms."""
    wanted = _terms(title)
    if not wanted:
        return []
    scored = []
    for ticket in session.exec(
        select(Ticket).where(Ticket.workspace_id == workspace_id, col(Ticket.state).not_in(_CLOSED))
    ).all():
        share = len(wanted & _terms(ticket.title)) / len(wanted)
        if share >= SIMILARITY_THRESHOLD:
            scored.append((share, ticket))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [ticket for _, ticket in scored[:limit]]


def block_heading(source: SurveySource, survey_pass: SurveyPass) -> str:
    licence = source.licence_name or (
        source.licence_kind.value if source.licence_kind else "licence unclassified"
    )
    commit = (source.pinned_commit or "")[:8] or "commit unrecorded"
    return (
        f"## Prior art — {source.name} ({source.url}, {licence}, @{commit}), "
        f"surveyed {utcnow().date().isoformat()}, pass {survey_pass.ordinal}"
    )


def _compose_block(
    session: Session,
    source: SurveySource,
    survey_pass: SurveyPass,
    findings: list[SurveyFinding],
    prose: str,
) -> str:
    lines = [block_heading(source, survey_pass), ""]
    if source.licence_use is not LicenceUse.FULL:
        lines += [f"**Licence:** {source.licence_constraint or 'unclassified: copy nothing.'}", ""]
    lines += [prose.strip(), "", "**What this rests on:**", ""]
    lines += [f"- {render_finding(f, finding_evidence(session, f.id))}" for f in findings]
    return "\n".join(lines)


def _latest_document(session: Session, ticket: Ticket) -> Artifact | None:
    return session.exec(
        select(Artifact)
        .where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == DOCUMENT_KIND,
            Artifact.title == DOCUMENT_TITLE,
        )
        .order_by(col(Artifact.created_at).desc(), col(Artifact.id).desc())
    ).first()


def _document_body(artifact: Artifact | None) -> str:
    return json.loads(artifact.content_json).get("body", "") if artifact else ""


def _fresh(session: Session, ticket_id: str) -> Ticket:
    """Re-read a ticket from the database, not from the identity map."""
    session.expire_all()
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise TicketWriteMismatch(f"Ticket {ticket_id} vanished after the write")
    return ticket


def _write_criteria(session: Session, ticket: Ticket, incoming: list[str]) -> list[str]:
    before = load_criteria(ticket.acceptance_criteria_json)
    merged = merge_criteria(before, incoming, "append")
    added = merged[len(before) :]
    if added:
        OrchestrationService(session).update_ticket_manual(
            ticket, UpdateTicketRequest(acceptance_criteria=merged)
        )
    stored = load_criteria(_fresh(session, ticket.id).acceptance_criteria_json)
    if stored != merged:
        raise TicketWriteMismatch(
            f"{ticket.external_id}: criteria read back differ from what was written "
            f"({len(stored)} stored, {len(merged)} sent). The write reported success."
        )
    return added


def _write_document_block(
    session: Session, ticket: Ticket, block: str
) -> tuple[Artifact, int, int]:
    previous = _latest_document(session, ticket)
    old_body = _document_body(previous)
    body = f"{old_body}\n\n{block}" if old_body else block
    written = write_document(
        session,
        ticket,
        title=DOCUMENT_TITLE,
        body=body,
        kind=DOCUMENT_KIND,
        summary="prior-art blocks",
    )
    session.expire_all()
    got = _document_body(session.get(Artifact, written.artifact.id))
    if got.rstrip("\n") != body.rstrip("\n") or not got.startswith(old_body):
        raise TicketWriteMismatch(
            f"{ticket.external_id}: the prior-art document read back differs from what was sent"
        )
    return written.artifact, len(old_body), len(got)


def _block_headings(body: str) -> list[str]:
    return [line[3:].strip() for line in body.splitlines() if line.startswith("## Prior art — ")]


def _pointer_section(description: str) -> tuple[int, int] | None:
    start = description.find(POINTER_HEADER)
    if start < 0:
        return None
    following = description.find("\n## ", start + len(POINTER_HEADER))
    return start, (len(description) if following < 0 else following)


def _write_pointer(
    session: Session, ticket: Ticket, artifact: Artifact, previous_id: str | None, heading: str
) -> None:
    """Keep exactly one pointer section naming the newest version and every block.

    An existing section is edited in place (the artifact id swapped, the new
    heading added) and everything outside it is checked unchanged; a new one is
    appended and the description before it is checked unchanged.
    """
    description = ticket.description
    section = _pointer_section(description)
    if section is None:
        headings = _block_headings(_document_body(artifact))
        pointer = (
            f"\n\n{POINTER_HEADER}, {utcnow().date().isoformat()}\n\n"
            f'The prior-art blocks for this ticket are the document **"{DOCUMENT_TITLE}"** '
            f"(kind `{DOCUMENT_KIND}`). Read it with `loregarden_list_artifacts` then "
            f"`loregarden_read_artifact`. Artifact id `{artifact.id}`.\n\n"
            + "\n".join(f"- {h}" for h in headings)
        )
        prefix, suffix, new = description, "", description.rstrip("\n") + pointer
    else:
        start, end = section
        prefix, suffix = description[:start], description[end:]
        body = description[start:end]
        if previous_id and previous_id in body:
            body = body.replace(previous_id, artifact.id)
        elif artifact.id not in body:
            body = body.rstrip("\n") + f"\n\nNewest version: artifact id `{artifact.id}`."
        body = body.rstrip("\n") + f"\n- {heading[3:].strip()}" + ("\n" if suffix else "")
        new = prefix + body + suffix
    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(description=new))
    got = _fresh(session, ticket.id).description
    if got != new or not got.startswith(prefix.rstrip("\n")) or not got.endswith(suffix):
        raise TicketWriteMismatch(
            f"{ticket.external_id}: the description read back differs from what was sent"
        )


def _relate(session: Session, a: Ticket, b: Ticket) -> None:
    TicketRelationService(session).add_relation(a.id, b.id, created_by="reference-survey")


def _create(
    session: Session, workspace: Workspace, spec: NewTicketIn, criteria: list[str]
) -> Ticket:
    parent = resolve_one(session, spec.parent, workspace_id=workspace.id)
    try:
        validate_parent_assignment(spec.work_item_type, parent.work_item_type)
    except ValueError as exc:
        raise SurveyError(
            f"A {spec.work_item_type.value} cannot sit under {parent.external_id} "
            f"(a {parent.work_item_type.value}): {exc}. Resolve the parent before creating."
        ) from exc
    return TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=spec.title,
        work_item_type=spec.work_item_type,
        parent_ticket_id=parent.id,
        description=spec.description,
        acceptance_criteria=criteria,
        priority=spec.priority,
        created_by="reference-survey",
    )


def _overflow_spec(
    session: Session, target: Ticket, source: SurveySource, reason: str
) -> NewTicketIn:
    parent = session.get(Ticket, target.parent_ticket_id) if target.parent_ticket_id else None
    if parent is None:
        raise SurveyError(
            f"{target.external_id} has no parent to file a sibling under; name a target with one"
        )
    return NewTicketIn(
        title=f"{target.title} — continued ({source.name})",
        parent=parent.external_id,
        work_item_type=target.work_item_type,
        description=(
            f"Continues {target.external_id}: {reason} Findings from the reference survey of "
            f"{source.url} that answer that ticket land here instead."
        ),
        priority=target.priority,
    )


def _load(
    session: Session, body: TicketWriteIn
) -> tuple[SurveyPass, SurveySource, list[Workspace], list[SurveyFinding]]:
    survey_pass = session.get(SurveyPass, body.pass_id)
    if survey_pass is None:
        raise SurveyNotFound(f"No survey pass {body.pass_id!r}")
    if survey_pass.status is not SurveyPassStatus.DISPATCHED:
        raise SurveyError(
            f"Pass {survey_pass.id} is {survey_pass.status.value}; findings land while it runs"
        )
    source = session.get(SurveySource, survey_pass.source_id)
    targets = [w for w, _ in target_workspaces(session, source.id)] if source else []
    if source is None or not targets:
        raise SurveyNotFound(
            f"Pass {survey_pass.id} has no source, or its source serves no workspace"
        )
    findings = [session.get(SurveyFinding, i) for i in dict.fromkeys(body.finding_ids)]
    missing = [
        i for i, f in zip(dict.fromkeys(body.finding_ids), findings, strict=True) if f is None
    ]
    if missing:
        raise SurveyNotFound(f"No finding {', '.join(missing)}")
    rows = [f for f in findings if f is not None]
    bad = [
        f.id for f in rows if f.source_id != source.id or f.status is SurveyFindingStatus.RETRACTED
    ]
    if bad:
        raise SurveyError(f"Findings {', '.join(bad)} are retracted or about another source")
    return survey_pass, source, targets, rows


def _create_workspace(body: TicketWriteIn, targets: list[Workspace]) -> Workspace:
    """Where a new ticket goes: the named target, the only target, or for a
    defect in our own tooling, home."""
    slug = body.create.workspace_slug if body.create else None
    if slug:
        named = [w for w in targets if w.slug == slug]
        if not named:
            raise SurveyError(
                f"{slug} is not one of this source's workspaces ({', '.join(w.slug for w in targets)})"
            )
        return named[0]
    if len(targets) == 1 or body.self_defect:
        return targets[0]
    raise SurveyError(
        f"This source serves {', '.join(w.slug for w in targets)}: name create.workspace_slug for the new ticket."
    )


def _resolve_across(session: Session, ref: str, targets: list[Workspace]) -> Ticket:
    """The one ticket `ref` names in any of the source's workspaces."""
    found: list[Ticket] = []
    for workspace in targets:
        try:
            found.append(resolve_one(session, ref, workspace_id=workspace.id))
        except TicketNotFound:  # silent-ok: absent from this workspace; the others are tried, and none at all is raised below
            continue
    if len(found) == 1:
        return found[0]
    if not found:
        raise SurveyNotFound(f"No ticket {ref!r} in {', '.join(w.slug for w in targets)}")
    raise SurveyError(
        f"{ref!r} names a ticket in more than one workspace: {', '.join(t.external_id for t in found)}"
    )


def _choose_target(
    session: Session, body: TicketWriteIn, targets: list[Workspace], source: SurveySource
) -> tuple[Ticket, SurveyTicketAction, Ticket | None, list[str]]:
    """The ticket the finding lands on, and why, creating one when it must."""
    notes: list[str] = []
    if body.create is not None:
        workspace = _create_workspace(body, targets)
        similar = similar_open_tickets(session, workspace.id, body.create.title)
        if similar and not body.force_new_reason:
            raise SurveyError(
                "Open tickets look like this one: "
                + "; ".join(f"{t.external_id} ({t.title})" for t in similar)
                + ". Append to the one it answers, or say why none fits in force_new_reason."
            )
        if similar:
            notes.append(
                f"created beside {', '.join(t.external_id for t in similar)}: {body.force_new_reason}"
            )
        action = SurveyTicketAction.SELF_DEFECT if body.self_defect else SurveyTicketAction.CREATED
        return _create(session, workspace, body.create, []), action, None, notes
    target = _resolve_across(session, body.ticket or "", targets)
    workspace = next(w for w in targets if w.id == target.workspace_id)
    if target.state in _CLOSED and body.criteria:
        # Only the criteria are frozen: a finding with none still lands on the
        # done ticket's document, where its history is.
        spec = _overflow_spec(
            session, target, source, f"it is {target.state.value}, so its criteria are frozen."
        )
        new = _create(session, workspace, spec, [])
        _relate(session, new, target)
        return new, SurveyTicketAction.ROUTED_FROM_DONE, target, notes
    stored = load_criteria(target.acceptance_criteria_json)
    merged = merge_criteria(stored, body.criteria, "append")
    if len(merged) > MAX_ACCEPTANCE_CRITERIA and len(merged) > len(stored):
        spec = _overflow_spec(
            session,
            target,
            source,
            f"it holds {len(stored)} acceptance criteria, at or past the cap of {MAX_ACCEPTANCE_CRITERIA}.",
        )
        new = _create(session, workspace, spec, [])
        _relate(session, new, target)
        return new, SurveyTicketAction.SPLIT, target, notes
    return target, SurveyTicketAction.APPENDED, None, notes


def write_findings_to_ticket(session: Session, body: TicketWriteIn) -> TicketWriteResult:
    survey_pass, source, targets, findings = _load(session, body)
    try:
        ticket, action, aimed_at, notes = _choose_target(session, body, targets, source)
        if body.self_defect:
            OrchestrationService(session).update_ticket_manual(
                ticket, UpdateTicketRequest(tags=[SELF_DEFECT_TAG])
            )
        added = _write_criteria(session, ticket, body.criteria)
        previous = _latest_document(session, ticket)
        block = _compose_block(session, source, survey_pass, findings, body.block)
        artifact, before, after = _write_document_block(session, ticket, block)
        _write_pointer(
            session,
            _fresh(session, ticket.id),
            artifact,
            previous.id if previous else None,
            block.splitlines()[0],
        )
    except TicketWriteMismatch as exc:
        report_exception(
            session,
            workspace_id=source.workspace_id,
            title=f"Survey write did not land: pass {survey_pass.id[:8]}",
            detail=f"{exc}\n\nSource {source.url}, pass {survey_pass.ordinal}. Nothing was retried.",
        )
        raise
    for finding in findings:
        session.add(
            SurveyPassTicket(
                pass_id=survey_pass.id,
                ticket_id=ticket.id,
                action=action,
                finding_id=finding.id,
                aimed_at_ticket_id=aimed_at.id if aimed_at else None,
                criteria_added=len(added),
                verified=True,
            )
        )
    session.commit()
    for joined in follow_new_tickets(session, source.id, [ticket.id]):
        if joined.added:
            notes.append(f"joined linked initiative {joined.initiative}")
        notes.extend(f"did not join {joined.initiative}: {skip.reason}" for skip in joined.skipped)
    if aimed_at is not None:
        notes.append(
            f"aimed at {aimed_at.external_id}; landed on {ticket.external_id} ({action.value})"
        )
    return TicketWriteResult(
        ticket=_fresh(session, ticket.id),
        action=action,
        aimed_at=aimed_at,
        criteria_added=added,
        document_artifact_id=artifact.id,
        document_chars_before=before,
        document_chars_after=after,
        notes=notes,
    )


def publish_retraction(session: Session, finding: SurveyFinding) -> list[str]:
    """Say on every ticket that carries a finding that it was retracted, and why.

    The block it was written in stays as written; the retraction is appended, so
    the history reads in order. Criteria added from it are named, not removed:
    whether they still stand is the ticket's question, not the survey's.
    """
    rows = session.exec(
        select(SurveyPassTicket).where(
            SurveyPassTicket.finding_id == finding.id, col(SurveyPassTicket.ticket_id).is_not(None)
        )
    ).all()
    landed: list[str] = []
    for ticket_id in dict.fromkeys(r.ticket_id for r in rows if r.ticket_id):
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            continue
        replaced = (
            f" Replaced by finding `{finding.replaced_by_id}`." if finding.replaced_by_id else ""
        )
        block = (
            f"## Retracted — finding `{finding.id}`, {utcnow().date().isoformat()}\n\n"
            f"{render_finding(finding, finding_evidence(session, finding.id))}{replaced}\n\n"
            "Criteria added from this finding were not removed; check whether each still stands."
        )
        _write_document_block(session, ticket, block)
        landed.append(ticket.external_id)
    return landed
