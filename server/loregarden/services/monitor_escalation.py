"""Escalate recurring monitor findings into report-only Bug tickets.

When ≥2 distinct tickets in one workspace currently share a persisted
``monitor_finding`` title, upsert exactly one Bug under an idempotent Milestone.
Lookup is ``legacy_external_id`` via ``ticket_ids.resolve`` — never
``Ticket.external_id LIKE 'monitor-escalation:%'``.

Called from ``workflow_monitor.sweep`` after ``record_findings`` and before
``apply_autofixes``. Filing is not an auto-fix and never opens Approvals.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.models.domain import (
    Artifact,
    MonitorArtifactKind,
    MonitorCondition,
    Ticket,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.ticket_ids import resolve as resolve_ticket_id
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, col, select

ESCALATION_THRESHOLD = 2
MILESTONE_LEGACY_ID = "monitor-escalations"
_EXTERNAL_ID_EXISTS_PREFIX = "external_id already exists"
_DEFAULT_TEMPLATE_SLUG = "loregarden-tdd"

# Mirrors workflow_monitor.WORKSPACE_SCOPED — those conditions never persist as
# ticket-scoped findings, and must not escalate even if planted. Kept local so
# this module does not import workflow_monitor (sweep imports us).
_WORKSPACE_SCOPED_VALUES = frozenset(
    {
        MonitorCondition.FAILURE_CLUSTER.value,
        MonitorCondition.DRAFT_DRIFT.value,
        MonitorCondition.SKIP_CONDITION_ROT.value,
        MonitorCondition.TIMEOUT_FLOOR_STALE.value,
        MonitorCondition.HARNESS_FAILURE_CLUSTER.value,
    }
)


def escalation_legacy_id(condition: str, stage_key: str) -> str:
    """``monitor-escalation:{condition}:{stage_key or "-"}`` — same encoding as ``_finding_title``."""
    return f"monitor-escalation:{condition}:{stage_key or '-'}"


@dataclass(frozen=True)
class _CurrentFinding:
    ticket: Ticket
    title: str
    summary: str
    evidence: dict[str, str]


def escalate_recurring_findings(session: Session, *, sweep_started_at: datetime) -> int:
    """Upsert report-only escalation Bugs for currently recurring finding titles.

    Returns the number of ``(workspace, title)`` groups created or refreshed.
    """
    groups = _current_groups(session, sweep_started_at=sweep_started_at)
    upserted = 0
    for (workspace_id, title), findings in groups.items():
        ticket_ids = {f.ticket.id for f in findings}
        if len(ticket_ids) < ESCALATION_THRESHOLD:
            continue
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            continue
        _ensure_workspace_template(session, workspace)
        _upsert_group(session, workspace=workspace, title=title, findings=findings)
        upserted += 1
    return upserted


def _ensure_workspace_template(session: Session, workspace: Workspace) -> None:
    """TicketService refuses create without a template; bind the seeded default if missing.

    Bare workspaces (and test factories that skip WorkflowService.create_workspace)
    can still hold findings. Escalation needs create_ticket, so give them the same
    default slug the API would have assigned.
    """
    if workspace.workflow_template_id:
        return
    template = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == _DEFAULT_TEMPLATE_SLUG)
    ).first()
    if template is None:
        template = session.exec(select(WorkflowTemplate)).first()
    if template is None:
        raise ValueError(
            f"Workspace {workspace.slug!r} has no workflow template and none are available"
        )
    workspace.workflow_template_id = template.id
    session.add(workspace)
    session.commit()
    session.refresh(workspace)


def _current_groups(
    session: Session, *, sweep_started_at: datetime
) -> dict[tuple[str, str], list[_CurrentFinding]]:
    """``(workspace_id, title) → current findings`` meeting the currency filter."""
    rows = session.exec(
        select(Artifact, Ticket)
        .join(Ticket, col(Artifact.ticket_id) == col(Ticket.id))
        .where(col(Artifact.kind) == MonitorArtifactKind.FINDING.value)
    ).all()

    groups: dict[tuple[str, str], list[_CurrentFinding]] = defaultdict(list)
    for artifact, ticket in rows:
        if _title_is_workspace_scoped(artifact.title or ""):
            continue
        payload = _parse_payload(artifact.content_json)
        if payload is None:
            continue
        last_seen = _parse_last_seen(payload.get("last_seen"))
        if last_seen is None:
            continue
        if last_seen < sweep_started_at:
            continue
        groups[(ticket.workspace_id, artifact.title or "")].append(
            _CurrentFinding(
                ticket=ticket,
                title=artifact.title or "",
                summary=_as_str(payload.get("summary")),
                evidence=_as_str_dict(payload.get("evidence")),
            )
        )
    return groups


def _title_is_workspace_scoped(title: str) -> bool:
    condition = title.split(":", 1)[0]
    return condition in _WORKSPACE_SCOPED_VALUES


def _as_str(value: object) -> str:
    try:
        return value if value.__class__ is str else ""
    except AttributeError:  # silent-ok: None/foreign objects have no usable summary
        return ""


def _as_str_dict(value: object) -> dict[str, str]:
    try:
        items = value.items()
    except AttributeError:  # silent-ok: non-mapping evidence is dropped from the description
        return {}
    out: dict[str, str] = {}
    for key, item in items:
        if key.__class__ is str and item.__class__ is str:
            out[key] = item
    return out


def _parse_payload(content_json: str | None) -> dict[str, object] | None:
    if not content_json:
        return None
    try:
        payload = json.loads(content_json)
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):  # silent-ok: corrupt finding JSON is skipped, not counted
        return None
    try:
        # Mapping protocol — lists/null from json.loads have no .items().
        return dict(payload.items())
    except AttributeError:  # silent-ok: non-object JSON cannot carry last_seen
        return None


def _parse_last_seen(raw: object) -> datetime | None:
    try:
        text = raw.strip()
    except AttributeError:  # silent-ok: non-string last_seen is ignored for currency
        return None
    if not text:
        return None
    try:
        # record_findings may emit +00:00 or Z; fromisoformat (3.11+) accepts both.
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):  # silent-ok: unparseable last_seen is ignored for currency
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _upsert_group(
    session: Session,
    *,
    workspace: Workspace,
    title: str,
    findings: list[_CurrentFinding],
) -> None:
    milestone = _ensure_milestone(session, workspace)
    bug_legacy = f"monitor-escalation:{title}"
    description = _build_description(title=title, findings=findings)
    existing = resolve_ticket_id(session, bug_legacy, workspace_id=workspace.id)
    if existing is not None:
        _refresh_bug(session, existing, description=description)
        return
    _create_bug(
        session,
        workspace=workspace,
        milestone=milestone,
        legacy_id=bug_legacy,
        title=title,
        description=description,
    )


def _ensure_milestone(session: Session, workspace: Workspace) -> Ticket:
    existing = resolve_ticket_id(session, MILESTONE_LEGACY_ID, workspace_id=workspace.id)
    if existing is not None:
        return existing
    svc = TicketService(session)
    try:
        milestone = svc.create_ticket(
            workspace_slug=workspace.slug,
            title="Monitor escalations",
            work_item_type=WorkItemType.MILESTONE,
            description=(
                "Report-only parent for cross-ticket monitor finding escalations. "
                "Not driven by autopilot."
            ),
            external_id=MILESTONE_LEGACY_ID,
        )
    except ValueError as exc:
        if _EXTERNAL_ID_EXISTS_PREFIX not in str(exc):
            raise
        resolved = resolve_ticket_id(session, MILESTONE_LEGACY_ID, workspace_id=workspace.id)
        if resolved is None:
            raise
        return resolved
    return _disable_workflow(session, milestone)


def _create_bug(
    session: Session,
    *,
    workspace: Workspace,
    milestone: Ticket,
    legacy_id: str,
    title: str,
    description: str,
) -> Ticket:
    svc = TicketService(session)
    try:
        bug = svc.create_ticket(
            workspace_slug=workspace.slug,
            title=f"Monitor escalation: {title}",
            work_item_type=WorkItemType.BUG,
            parent_ticket_id=milestone.id,
            description=description,
            external_id=legacy_id,
        )
    except ValueError as exc:
        if _EXTERNAL_ID_EXISTS_PREFIX not in str(exc):
            raise
        resolved = resolve_ticket_id(session, legacy_id, workspace_id=workspace.id)
        if resolved is None:
            raise
        _refresh_bug(session, resolved, description=description)
        return resolved
    return _disable_workflow(session, bug)


def _refresh_bug(session: Session, bug: Ticket, *, description: str) -> None:
    bug.description = description
    # Preserve report-only: never clear workflow_disabled on refresh.
    session.add(bug)
    session.commit()


def _disable_workflow(session: Session, ticket: Ticket) -> Ticket:
    ticket.workflow_disabled = True
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _build_description(*, title: str, findings: list[_CurrentFinding]) -> str:
    # Stable order for readable diffs between sweeps.
    by_ticket: dict[str, _CurrentFinding] = {}
    for finding in findings:
        by_ticket[finding.ticket.id] = finding
    lines = [
        f"Recurring monitor finding `{title}` across {len(by_ticket)} tickets.",
        "",
        "Source tickets:",
    ]
    for ticket_id in sorted(by_ticket):
        finding = by_ticket[ticket_id]
        ticket = finding.ticket
        label = ticket.external_id or ticket.id
        lines.append(f"- {label} ({ticket.id}): {finding.summary or '(no summary)'}")
        if finding.evidence:
            evidence_bits = ", ".join(f"{k}={v}" for k, v in sorted(finding.evidence.items()))
            lines.append(f"  evidence: {evidence_bits}")
    return "\n".join(lines)
