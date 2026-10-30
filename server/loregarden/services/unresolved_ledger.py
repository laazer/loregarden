"""Monotonic named unresolved set. Live derive stays honest in stage_verdict.

The only module that may persist, drop, or waive a named unresolved item.
persist never shrinks. clear (producer + live gone) and waive (non-empty
reason) are the only drops. Do not import gate_producer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import Artifact, Ticket
from loregarden.models.domain.enums import UnresolvedArtifactKind, utcnow
from loregarden.services.stage_report import WaiverItem
from loregarden.services.stage_verdict import UnresolvedItem
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

HUMAN_AGENT_ID = "human"


@dataclass(frozen=True)
class OpenShrinkFinding:
    """One ticket whose latest OPEN is missing a previously recorded name with no audit."""

    ticket_id: str
    entered_at: datetime | None
    detail: str


@dataclass(frozen=True)
class LedgerActor:
    """Who is asking the ledger to persist, clear, or waive.

    Bound from the calling AgentRun (or the literal ``human``), never from a
    document field the clearer writes.
    """

    agent_id: str
    stage_key: str = ""


class UnresolvedOpenItem(BaseModel):
    """One name in an OPEN snapshot."""

    model_config = ConfigDict(extra="ignore")

    name: str
    producer_agent_id: str = ""
    producer_stage_key: str = ""
    first_seen_run_id: str = ""
    stage_key: str = ""
    durable: bool = False


class UnresolvedOpenPayload(BaseModel):
    """Current named set after a write-path merge."""

    model_config = ConfigDict(extra="ignore")

    items: list[UnresolvedOpenItem] = Field(default_factory=list)


class UnresolvedWaiverPayload(BaseModel):
    """Audit row for one waived name."""

    model_config = ConfigDict(extra="ignore")

    name: str
    reason: str
    actor: LedgerActor
    run_id: str = ""
    when: datetime = Field(default_factory=utcnow)


class UnresolvedClearPayload(BaseModel):
    """Audit row for one producer-clear."""

    model_config = ConfigDict(extra="ignore")

    name: str
    actor: LedgerActor
    run_id: str = ""


def persist_unresolved(
    session: Session,
    ticket: Ticket,
    live: Sequence[UnresolvedItem],
    actor: LedgerActor,
    *,
    run_id: str = "",
) -> None:
    """Union new live names into OPEN. Never shrinks."""
    current = list(_current_open_items(session, ticket))
    by_name = {item.name: item for item in current}
    waived = _waived_names(session, ticket)
    added = False
    for item in live:
        if not item.name or item.name in by_name or item.name in waived:
            continue
        by_name[item.name] = _open_item_from_live(item, actor, run_id)
        added = True
    if not added and current:
        return
    if not by_name:
        return
    _write_open(session, ticket, list(by_name.values()))


def visible_unresolved(
    session: Session,
    ticket: Ticket,
    live: Sequence[UnresolvedItem],
    *,
    current_stage_key: str,
    actor: LedgerActor | None = None,
) -> tuple[UnresolvedItem, ...]:
    """Persisted ∪ live, filtered by durable/ephemeral. No writes."""
    by_name: dict[str, UnresolvedItem] = {}
    waived = _waived_names(session, ticket)
    # How an item reads to the agent (`detail`, `is_claim`) is derived on every
    # pass rather than stored; only the name and its producer are. So a name the
    # persisted snapshot also holds takes those two back from the live item —
    # otherwise the stored row wins and the agent is sent back the bare name, or
    # its own claim reflected at it.
    live_by_name = {item.name: item for item in live}
    for item in _current_open_items(session, ticket):
        if _item_visible(
            item.name,
            item.producer_agent_id,
            item.stage_key or item.producer_stage_key,
            current_stage_key=current_stage_key,
            actor=actor,
            durable=item.durable or _is_durable(item.name),
        ):
            fresh = live_by_name.get(item.name)
            by_name[item.name] = UnresolvedItem(
                name=item.name,
                producer_agent_id=item.producer_agent_id,
                producer_stage_key=item.producer_stage_key,
                detail=fresh.detail if fresh else "",
                is_claim=fresh.is_claim if fresh else False,
            )
    for item in live:
        if not item.name or item.name in by_name or item.name in waived:
            continue
        if _item_visible(
            item.name,
            item.producer_agent_id,
            item.producer_stage_key,
            current_stage_key=current_stage_key,
            actor=actor,
            durable=_is_durable(item.name),
        ):
            by_name[item.name] = item
    return tuple(by_name[name] for name in sorted(by_name))


def clear(
    session: Session,
    ticket: Ticket,
    name: str,
    actor: LedgerActor,
    live: Sequence[UnresolvedItem] = (),
) -> bool:
    """Drop ``name`` iff the actor produced it and live no longer emits it."""
    if actor.agent_id == HUMAN_AGENT_ID:
        return False
    item = _open_item(session, ticket, name)
    if item is None:
        return False
    if not _producer_matches(item, actor):
        return False
    if name in {entry.name for entry in live}:
        return False
    remaining = [entry for entry in _current_open_items(session, ticket) if entry.name != name]
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.CLEAR,
            title=f"Unresolved clear — {name}",
            content_json=UnresolvedClearPayload(name=name, actor=actor).model_dump_json(),
        )
    )
    session.flush()
    _write_open(session, ticket, remaining)
    return True


def waive(
    session: Session,
    ticket: Ticket,
    name: str,
    reason: str,
    actor: LedgerActor,
    *,
    run_id: str = "",
) -> bool:
    """Drop ``name`` after a non-empty written reason. Writes WAIVER first."""
    stripped = reason.strip()
    if not stripped:
        return False
    item = _open_item(session, ticket, name)
    if item is None:
        return False
    if actor.agent_id != HUMAN_AGENT_ID and not _producer_matches(item, actor):
        return False
    remaining = [entry for entry in _current_open_items(session, ticket) if entry.name != name]
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.WAIVER,
            title=f"Unresolved waiver — {name}",
            content_json=UnresolvedWaiverPayload(
                name=name,
                reason=stripped,
                actor=actor,
                run_id=run_id,
                when=utcnow(),
            ).model_dump_json(),
        )
    )
    session.flush()
    _write_open(session, ticket, remaining)
    return True


def settle_unresolved(
    session: Session,
    ticket: Ticket,
    live: Sequence[UnresolvedItem],
    actor: LedgerActor,
    waivers: Sequence[WaiverItem] = (),
    *,
    current_stage_key: str,
    run_id: str = "",
) -> tuple[UnresolvedItem, ...]:
    """Persist new names; producer-waive; producer-clear live-gone; return visible."""
    persist_unresolved(session, ticket, live, actor, run_id=run_id)
    live_names = {item.name for item in live}
    for waiver in waivers:
        waive(session, ticket, waiver.name, waiver.reason, actor, run_id=run_id)
    for item in _current_open_items(session, ticket):
        if item.name not in live_names:
            clear(session, ticket, item.name, actor, live=live)
    return visible_unresolved(
        session, ticket, live, current_stage_key=current_stage_key, actor=actor
    )


def persisted_open_names(session: Session, ticket: Ticket) -> tuple[str, ...]:
    """Names on the newest OPEN snapshot, or empty when none / unreadable."""
    return tuple(item.name for item in _current_open_items(session, ticket))


def detect_unaudited_open_shrink(session: Session) -> list[OpenShrinkFinding]:
    """Names absent from latest OPEN that left an earlier snapshot with no audit."""
    found: list[OpenShrinkFinding] = []
    for ticket_id, rows in _open_rows_by_ticket(session).items():
        if len(rows) < 2:
            continue
        latest_row, latest = rows[-1]
        latest_names = {item.name for item in latest.items}
        missing: dict[str, UnresolvedOpenItem] = {}
        for older_row, older in rows[:-1]:
            for item in older.items:
                if item.name in latest_names or item.name in missing:
                    continue
                if _has_audit(session, ticket_id, item.name, after=older_row.created_at):
                    continue
                missing[item.name] = item
        if not missing:
            continue
        ticket = session.get(Ticket, ticket_id)
        label = ticket.external_id if ticket is not None else ticket_id
        found.append(
            OpenShrinkFinding(
                ticket_id=ticket_id,
                entered_at=latest_row.created_at,
                detail=(f"{label} OPEN dropped {', '.join(sorted(missing))} without CLEAR/WAIVER"),
            )
        )
    return found


def repair_unaudited_open_shrink(session: Session, ticket_ids: Sequence[str]) -> int:
    """Restore missing names onto a new OPEN snapshot. Does not invent audit rows."""
    repaired = 0
    wanted = {ticket_id for ticket_id in ticket_ids if ticket_id}
    for ticket_id in wanted:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            continue
        rows = _open_rows_by_ticket(session).get(ticket_id, [])
        if len(rows) < 2:
            continue
        _latest_row, latest = rows[-1]
        by_name = {item.name: item for item in latest.items}
        for older_row, older in rows[:-1]:
            for item in older.items:
                if item.name in by_name:
                    continue
                if _has_audit(session, ticket_id, item.name, after=older_row.created_at):
                    continue
                by_name[item.name] = item
        if set(by_name) == {item.name for item in latest.items}:
            continue
        _write_open(session, ticket, list(by_name.values()))
        repaired += 1
    return repaired


def _is_durable(name: str) -> bool:
    return name.startswith("handoff:") or name.startswith("evidence:")


def _open_item_from_live(
    item: UnresolvedItem, actor: LedgerActor, run_id: str
) -> UnresolvedOpenItem:
    return UnresolvedOpenItem(
        name=item.name,
        producer_agent_id=item.producer_agent_id,
        producer_stage_key=item.producer_stage_key,
        first_seen_run_id=run_id,
        stage_key=item.producer_stage_key or actor.stage_key,
        durable=_is_durable(item.name),
    )


def _item_visible(
    name: str,
    producer_agent_id: str,
    stage_key: str,
    *,
    current_stage_key: str,
    actor: LedgerActor | None,
    durable: bool,
) -> bool:
    if durable or _is_durable(name):
        return True
    if stage_key != current_stage_key:
        return False
    if actor is not None and producer_agent_id != actor.agent_id:
        return False
    return True


def _producer_matches(item: UnresolvedOpenItem, actor: LedgerActor) -> bool:
    if item.producer_stage_key == "":
        return actor.agent_id == item.producer_agent_id
    return actor.agent_id == item.producer_agent_id and actor.stage_key == item.producer_stage_key


def _newest_open_row(session: Session, ticket: Ticket) -> Artifact | None:
    rows = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == UnresolvedArtifactKind.OPEN)
        .order_by(col(Artifact.created_at).desc())
    ).all()
    return rows[0] if rows else None


def _parse_open(row: Artifact, *, ticket_label: str) -> UnresolvedOpenPayload:
    try:
        return UnresolvedOpenPayload.model_validate_json(row.content_json)
    except ValidationError:
        logger.warning(
            "Ignoring malformed unresolved_open on ticket %s", ticket_label, exc_info=True
        )
        return UnresolvedOpenPayload()


def _current_open_items(session: Session, ticket: Ticket) -> tuple[UnresolvedOpenItem, ...]:
    row = _newest_open_row(session, ticket)
    if row is None:
        return ()
    return tuple(_parse_open(row, ticket_label=ticket.external_id).items)


def _open_item(session: Session, ticket: Ticket, name: str) -> UnresolvedOpenItem | None:
    return next((item for item in _current_open_items(session, ticket) if item.name == name), None)


def _write_open(session: Session, ticket: Ticket, items: Sequence[UnresolvedOpenItem]) -> None:
    previous = _current_open_items(session, ticket)
    prev_names = {item.name for item in previous}
    new_names = {item.name for item in items}
    dropped = prev_names - new_names
    if dropped:
        previous_row = _newest_open_row(session, ticket)
        after = previous_row.created_at if previous_row is not None else None
        unaudited = {
            name for name in dropped if not _has_audit(session, ticket.id, name, after=after)
        }
        if unaudited:
            raise ValueError(f"OPEN snapshot shrinks {sorted(unaudited)} without CLEAR/WAIVER")
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=UnresolvedArtifactKind.OPEN,
            title="Unresolved open",
            content_json=UnresolvedOpenPayload(items=list(items)).model_dump_json(),
        )
    )
    session.commit()


def _has_audit(session: Session, ticket_id: str, name: str, *, after: datetime | None) -> bool:
    kinds = (UnresolvedArtifactKind.CLEAR, UnresolvedArtifactKind.WAIVER)
    rows = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket_id, col(Artifact.kind).in_(kinds))
    ).all()
    for row in rows:
        if after is not None and row.created_at is not None and row.created_at < after:
            continue
        parsed_name = _audit_name(row)
        if parsed_name == name:
            return True
    return False


def _waived_names(session: Session, ticket: Ticket) -> set[str]:
    rows = session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id, Artifact.kind == UnresolvedArtifactKind.WAIVER
        )
    ).all()
    names: set[str] = set()
    for row in rows:
        name = _audit_name(row)
        if name:
            names.add(name)
    return names


def _audit_name(row: Artifact) -> str:
    try:
        if row.kind == UnresolvedArtifactKind.WAIVER:
            return UnresolvedWaiverPayload.model_validate_json(row.content_json).name
        if row.kind == UnresolvedArtifactKind.CLEAR:
            return UnresolvedClearPayload.model_validate_json(row.content_json).name
    except ValidationError:
        logger.warning("Ignoring malformed %s artifact %s", row.kind, row.id, exc_info=True)
    return ""


def _open_rows_by_ticket(
    session: Session,
) -> dict[str, list[tuple[Artifact, UnresolvedOpenPayload]]]:
    rows = session.exec(
        select(Artifact)
        .where(Artifact.kind == UnresolvedArtifactKind.OPEN)
        .order_by(col(Artifact.created_at).asc())
    ).all()
    grouped: dict[str, list[tuple[Artifact, UnresolvedOpenPayload]]] = {}
    for row in rows:
        grouped.setdefault(row.ticket_id, []).append(
            (row, _parse_open(row, ticket_label=row.ticket_id))
        )
    return grouped
