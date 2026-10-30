"""Map items from records loregarden already keeps, with no model call.

Each adapter reads one conversation's records and returns them as `MapRecord`s,
each keyed by the record it came from (`source_ref`), so filing the same
records twice files nothing new. It also reports, per source, how many it read
and whether it could read at all: a source that could not be read is shown on
the map, never quietly missing from it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    BaxterChatMessage,
    MapItemKind,
    MapItemState,
    RunStatus,
    Ticket,
)
from loregarden.models.domain.chat_enums import ChatMessageRole
from loregarden.models.domain.chat_primitives import (
    ChatPart,
    QAPart,
    TicketListPart,
    TicketPart,
    TodoListPart,
)
from loregarden.models.domain.enums import comparable_utc
from loregarden.services.chat_primitives.storage import load_parts_json
from loregarden.services.checkpoint_logs import ticket_checkpoint_logs
from loregarden.services.memory_store import ObsidianMemoryStore, slugify
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Long enough for a sentence, short enough for one row on the vine.
TITLE_CHARS = 140

#: Tool-permission prompts are the CLI asking to run a command, hundreds per
#: ticket and settled in seconds: noise on a map of what a person decided.
_MAPPED_APPROVALS = frozenset(ApprovalKind) - {ApprovalKind.CLI_PERMISSION}
_QUESTION_APPROVALS = frozenset({ApprovalKind.CLI_QUESTION, ApprovalKind.BLOCK_DECISION})

_APPROVAL_STATE = {
    ApprovalStatus.PENDING: MapItemState.WAITING,
    ApprovalStatus.APPROVED: MapItemState.SETTLED,
    ApprovalStatus.REJECTED: MapItemState.SETTLED,
    ApprovalStatus.IGNORED: MapItemState.DROPPED,
}

_RUN_STATE = {
    RunStatus.QUEUED: MapItemState.OPEN,
    RunStatus.RUNNING: MapItemState.OPEN,
    RunStatus.AWAITING_PERMISSION: MapItemState.WAITING,
    RunStatus.SUCCEEDED: MapItemState.SETTLED,
    RunStatus.FAILED: MapItemState.FAILED,
    RunStatus.CANCELLED: MapItemState.DROPPED,
}


class MapRecord(BaseModel):
    source_ref: str
    kind: MapItemKind
    state: MapItemState
    title: str
    detail: str = ""
    happened_at: datetime
    #: The `source_ref` of the topic this hangs off.
    topic_ref: str | None = None


class SourceReport(BaseModel):
    """One source's read: how many records it gave, or why it gave none."""

    name: str
    count: int = 0
    ok: bool = True
    error: str = ""


class RecordRead(BaseModel):
    records: list[MapRecord]
    sources: list[SourceReport]


def _title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_CHARS else f"{line[: TITLE_CHARS - 1]}…"


# -- chats ------------------------------------------------------------------


def chat_records(session: Session, session_id: str) -> RecordRead:
    """A Baxter chat's map: its plans' tasks, its questions, and the tickets it
    talked about as topics.

    A question is answered once the operator replies after it. The `qa` part
    stores no answer of its own (none of the live ones do); the reply that
    follows it is the answer.
    """
    messages = list(
        session.exec(
            select(BaxterChatMessage)
            .where(BaxterChatMessage.session_id == session_id)
            .order_by(BaxterChatMessage.created_at, BaxterChatMessage.id)
        )
    )
    report = SourceReport(name="chat parts")
    records: list[MapRecord] = []
    topic_ref: str | None = None
    unreadable = 0
    for index, message in enumerate(messages):
        replied = any(later.role == ChatMessageRole.USER for later in messages[index + 1 :])
        for raw in load_parts_json(message.parts_json):
            try:
                part = _PART.validate_python(raw)
            except ValidationError:
                unreadable += 1
                continue
            reader = _CHAT_READERS.get(type(part))
            if reader is None:
                continue
            read, topic = reader(part, message, topic_ref, replied)
            records.extend(read)
            topic_ref = topic or topic_ref
    if unreadable:
        logger.warning("living map: %d unreadable chat parts in session %s", unreadable, session_id)
        report.ok = False
        report.error = f"{unreadable} chat part(s) could not be read and are not on the map"
    report.count = len(records)
    return RecordRead(records=_dedupe(records), sources=[report])


_PART: TypeAdapter[ChatPart] = TypeAdapter(ChatPart)

#: A reader returns the records a part files and, for a topic, its ref: the
#: items after it hang off that topic until the next one.
_ChatReader = Callable[
    [Any, BaxterChatMessage, str | None, bool], tuple[list[MapRecord], str | None]
]


def _topic(ref: str, title: str, message: BaxterChatMessage) -> tuple[list[MapRecord], str]:
    record = MapRecord(
        source_ref=ref,
        kind=MapItemKind.TOPIC,
        state=MapItemState.OPEN,
        title=_title(title),
        happened_at=message.created_at,
    )
    return [record], ref


def _ticket_topic(
    part: TicketPart, message: BaxterChatMessage, _topic_ref: str | None, _replied: bool
) -> tuple[list[MapRecord], str | None]:
    return _topic(f"ticket:{part.ticket_id}", part.title or part.ticket_id, message)


def _ticket_list_topic(
    part: TicketListPart, message: BaxterChatMessage, _topic_ref: str | None, _replied: bool
) -> tuple[list[MapRecord], str | None]:
    if not part.title:
        return [], None
    return _topic(f"ticket-list:{part.title}", part.title, message)


def _task_records(
    part: TodoListPart, message: BaxterChatMessage, topic_ref: str | None, _replied: bool
) -> tuple[list[MapRecord], str | None]:
    return _tasks(part, message, topic_ref), None


def _question_records(
    part: QAPart, message: BaxterChatMessage, topic_ref: str | None, replied: bool
) -> tuple[list[MapRecord], str | None]:
    return _questions(part, message, topic_ref, replied), None


_CHAT_READERS: dict[type, _ChatReader] = {
    TicketPart: _ticket_topic,
    TicketListPart: _ticket_list_topic,
    TodoListPart: _task_records,
    QAPart: _question_records,
}


def _tasks(
    part: TodoListPart, message: BaxterChatMessage, topic_ref: str | None
) -> list[MapRecord]:
    # A plan re-emitted under the same id supersedes the earlier card, so its
    # items are keyed by the plan: a later copy that ticks one updates it.
    plan = part.plan_id or message.id
    return [
        MapRecord(
            source_ref=f"todo:{plan}:{item.id}",
            kind=MapItemKind.TASK,
            state=MapItemState.SETTLED if item.checked else MapItemState.OPEN,
            title=_title(item.text),
            happened_at=message.created_at,
            topic_ref=topic_ref,
        )
        for item in part.items
    ]


def _questions(
    part: QAPart, message: BaxterChatMessage, topic_ref: str | None, replied: bool
) -> list[MapRecord]:
    return [
        MapRecord(
            source_ref=f"qa:{message.id}:{item.id}",
            kind=MapItemKind.QUESTION,
            state=MapItemState.SETTLED if (item.answer or replied) else MapItemState.WAITING,
            title=_title(item.question),
            detail=item.answer,
            happened_at=message.created_at,
            topic_ref=topic_ref,
        )
        for item in part.items
    ]


# -- tickets ----------------------------------------------------------------


def ticket_records(session: Session, ticket: Ticket) -> RecordRead:
    """A ticket's map: its stages as topics, the approvals and questions that
    stopped it on a person, its agent runs, and the assumptions they recorded."""
    names = {
        view.key: view.name for view in OrchestrationService(session).stage_views_for_read(ticket)
    }
    approvals = _approval_records(session, ticket)
    runs, run_stage = _run_records(session, ticket)
    checkpoints = _checkpoint_records(session, ticket, run_stage)

    records = approvals.records + runs.records + checkpoints.records
    topics = {record.topic_ref for record in records if record.topic_ref}
    first_seen: dict[str, datetime] = {}
    for record in sorted(records, key=lambda r: comparable_utc(r.happened_at)):
        if record.topic_ref and record.topic_ref not in first_seen:
            first_seen[record.topic_ref] = record.happened_at
    stage_topics = [
        MapRecord(
            source_ref=ref,
            kind=MapItemKind.TOPIC,
            state=MapItemState.OPEN,
            title=names.get(ref.removeprefix("stage:"), ref.removeprefix("stage:")),
            happened_at=first_seen[ref],
        )
        for ref in sorted(topics)
    ]
    return RecordRead(
        records=_dedupe(stage_topics + records),
        sources=approvals.sources + runs.sources + checkpoints.sources,
    )


def _stage_ref(stage_key: str | None) -> str | None:
    return f"stage:{stage_key}" if stage_key else None


def _approval_records(session: Session, ticket: Ticket) -> RecordRead:
    rows = session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id).order_by(Approval.created_at)
    ).all()
    records = [
        MapRecord(
            source_ref=f"approval:{row.id}",
            kind=MapItemKind.QUESTION if row.kind in _QUESTION_APPROVALS else MapItemKind.WAIT,
            state=_APPROVAL_STATE[row.status],
            title=_title(row.title or row.kind.value),
            detail=row.status.value if row.status != ApprovalStatus.PENDING else "",
            happened_at=row.created_at,
            topic_ref=_stage_ref(row.stage_key),
        )
        for row in rows
        if row.kind in _MAPPED_APPROVALS
    ]
    return RecordRead(records=records, sources=[SourceReport(name="approvals", count=len(records))])


def _run_records(session: Session, ticket: Ticket) -> tuple[RecordRead, dict[str, str]]:
    rows = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at)
    ).all()
    records: list[MapRecord] = []
    run_stage: dict[str, str] = {}
    for row in rows:
        for key in (row.id, row.run_code):
            if key and row.stage_key:
                run_stage[slugify(key)] = row.stage_key
        records.append(
            MapRecord(
                source_ref=f"run:{row.id}",
                kind=MapItemKind.CHANGE,
                state=_RUN_STATE[row.status],
                title=_title(f"{row.agent_id} ran {row.stage_key or 'a stage'}"),
                detail=row.status.value,
                happened_at=row.started_at or row.created_at,
                topic_ref=_stage_ref(row.stage_key),
            )
        )
    return RecordRead(
        records=records, sources=[SourceReport(name="agent runs", count=len(records))]
    ), run_stage


def _checkpoint_records(session: Session, ticket: Ticket, run_stage: dict[str, str]) -> RecordRead:
    store = ObsidianMemoryStore.from_settings()
    if store is None:
        return RecordRead(
            records=[],
            sources=[
                SourceReport(
                    name="checkpoints",
                    ok=False,
                    error="No Obsidian vault is configured, so recorded assumptions are not on this map",
                )
            ],
        )
    workspace = workspace_for_ticket(session, ticket)
    try:
        logs = ticket_checkpoint_logs(store, ticket, workspace.slug if workspace else "")
    except OSError as exc:
        logger.warning("living map: checkpoints for %s could not be read: %s", ticket.id, exc)
        return RecordRead(
            records=[],
            sources=[
                SourceReport(
                    name="checkpoints", ok=False, error=f"Checkpoints could not be read: {exc}"
                )
            ],
        )
    records = [
        MapRecord(
            source_ref=f"checkpoint:{log.run_slug}:{index}",
            kind=MapItemKind.ASSUMPTION,
            state=MapItemState.SETTLED,
            title=_title(entry),
            detail=entry,
            # A log has one mtime; its entries keep their written order after it.
            happened_at=datetime.fromtimestamp(log.modified, tz=timezone.utc),
            topic_ref=_stage_ref(run_stage.get(log.run_slug)),
        )
        for log in reversed(logs)
        for index, entry in enumerate(log.entries)
    ]
    return RecordRead(
        records=records, sources=[SourceReport(name="checkpoints", count=len(records))]
    )


# -- shared -----------------------------------------------------------------


def _dedupe(records: list[MapRecord]) -> list[MapRecord]:
    """One record per `source_ref`, the last one read winning: a ticket card
    shown twice is one topic, and a re-emitted plan's latest copy is its state."""
    latest: dict[str, MapRecord] = {}
    for record in records:
        if record.source_ref in latest:
            record = record.model_copy(
                update={"happened_at": latest[record.source_ref].happened_at}
            )
        latest[record.source_ref] = record
    return list(latest.values())
