"""MCP tools for the reference survey (`services.reference_survey`).

Four tools, each with an `action`, registered in `tool_registry.EXTENDED_TOOLS`:

- `loregarden_survey_queue` — propose a source, list the queue, request a
  continuation or scoped pass, read a source's whole record or its coverage.
- `loregarden_survey_pass` — a running pass's brief, the reads it records, and
  its finish.
- `loregarden_survey_record` — evidence, findings, retractions and unknowns.
- `loregarden_survey_write` — land findings on a ticket, read back byte-exact.

All auto-approve: the queue drains unattended by decision, and the one thing a
person gates (admitting a new source) is an inbox card the service files itself.
Validation and every rule live in the service; this module is schema and JSON.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, Json, TypeAdapter
from sqlmodel import Session

from loregarden.mcp.tool_args import coerce_mapping, coerce_string_list
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    boolean_prop,
    enum_string_prop,
    integer_prop,
    string_list_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain.survey_enums import (
    SurveyEpistemic,
    SurveyFindingClaim,
    SurveyPassAction,
    SurveyPassMode,
    SurveyPassOutcome,
    SurveyQueueAction,
    SurveyReadState,
    SurveyRecordAction,
    SurveySourceKind,
)
from loregarden.models.domain.work_item_types import WorkItemType
from loregarden.services.reference_survey import passes, queue, record, report
from loregarden.services.reference_survey.initiatives import link_initiative, unlink_initiative
from loregarden.services.reference_survey.schemas import (
    EvidenceIn,
    FindingIn,
    PassFinishIn,
    PassRequestIn,
    ReadIn,
    RetractIn,
    SourceIn,
    TicketWriteIn,
    UnknownOpenIn,
    UnknownUpdateIn,
)
from loregarden.services.reference_survey.ticket_writer import (
    publish_retraction,
    write_findings_to_ticket,
)


def _values(enum: type) -> list[str]:
    return [member.value for member in enum]


_SOURCE_ID = string_prop("Survey source id.")
_PASS_ID = string_prop("Survey pass id (from the carrier ticket's description).")

_READ_ITEM = {
    "type": "object",
    "properties": {
        "path": string_prop("A map path, or a single file under one."),
        "state": enum_string_prop("What the pass did with it.", _values(SurveyReadState)),
        "files_read": integer_prop("Files actually read under this path."),
        "file_count": integer_prop("For a path not in the map: its own file count (1 for a file)."),
        "note": string_prop("Why it was skipped, or what it answered."),
    },
    "required": ["path", "state"],
}

_NEW_TICKET = {
    "type": "object",
    "description": "A ticket to create when no existing ticket answers the finding.",
    "properties": {
        "title": string_prop("Ticket title."),
        "workspace_slug": string_prop(
            "Which of the source's workspaces the ticket belongs to; required when it serves several."
        ),
        "parent": string_prop("Parent ticket external id. Hierarchy is checked before creating."),
        "work_item_type": enum_string_prop("Work item type (default task).", _values(WorkItemType)),
        "description": string_prop("The spec. The finding itself goes in block, not here."),
        "priority": integer_prop("1-3, 1 highest (default 3)."),
    },
    "required": ["title", "parent", "description"],
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.SURVEY_QUEUE,
        "description": (
            "The reference-survey queue. action=enqueue proposes a source for one or more "
            "workspaces; one reading serves them all, and a URL already known gains the new "
            "workspaces instead of a second survey (a person admits a new source "
            "from the inbox before anything reads it); list shows waiting and running passes; "
            "request_pass queues a continuation (pick up the unread list) or a scoped re-read "
            "against one new question on an admitted source; link_initiative makes an "
            "initiative follow the source (its open tickets join it, now and as passes add "
            "more) and unlink_initiative undoes that; status returns a source's whole "
            "record (passes, tickets touched, findings, unknowns, coverage); coverage returns "
            "the directory map with what every pass read, each count with its denominator."
        ),
        "inputSchema": tool_schema(
            properties={
                "action": enum_string_prop("What to do.", _values(SurveyQueueAction)),
                "workspace_slugs": string_list_prop(
                    "enqueue: every workspace whose backlog the source should answer; the first is "
                    "home. A URL already known gains these workspaces instead of a second survey."
                ),
                "workspace_slug": string_prop("list: only sources serving this workspace."),
                "kind": enum_string_prop("enqueue: what the source is.", _values(SurveySourceKind)),
                "url": string_prop("enqueue: repository or https URL."),
                "name": string_prop("enqueue: display name (default: last URL segment)."),
                "question": string_prop(
                    "enqueue/request_pass: what the source or pass is meant to answer."
                ),
                "queued_reason": string_prop("enqueue: why it is worth reading now."),
                "priority": integer_prop("enqueue: lower runs first (default 100)."),
                "source_id": _SOURCE_ID,
                "mode": enum_string_prop(
                    "request_pass: continuation or scoped.",
                    [SurveyPassMode.CONTINUATION.value, SurveyPassMode.SCOPED.value],
                ),
                "paths": string_list_prop(
                    "request_pass: map paths to read; empty lets the feeder slice the map."
                ),
                "limit": integer_prop("list: max rows (default 50)."),
                "initiative_id": string_prop(
                    "link_initiative/unlink_initiative: the initiative, by UUID or external id."
                ),
            },
            required=["action"],
        ),
    },
    {
        "name": McpTool.SURVEY_PASS,
        "description": (
            "A running survey pass. action=brief returns the question, source, licence, clone "
            "path, slice of the map, earlier passes and open unknowns; reads records what the "
            "pass did with each map path (read, partial, skipped with a reason); finish closes it "
            "with an outcome, where it stopped, and the unread paths worth a later pass. "
            "'answered' is refused while a question the pass opened is open."
        ),
        "inputSchema": tool_schema(
            properties={
                "action": enum_string_prop("What to do.", _values(SurveyPassAction)),
                "pass_id": _PASS_ID,
                "reads": {
                    "type": "array",
                    "description": "reads: one entry per path.",
                    "items": _READ_ITEM,
                },
                "outcome": enum_string_prop(
                    "finish: the answer to the pass's question.", _values(SurveyPassOutcome)
                ),
                "outcome_note": string_prop("finish: the answer, or why it is partial."),
                "stopped_at": string_prop("finish: where the pass stopped and what remains."),
                "unread_priority": string_list_prop(
                    "finish: map paths still worth reading, best first."
                ),
            },
            required=["action", "pass_id"],
        ),
    },
    {
        "name": McpTool.SURVEY_RECORD,
        "description": (
            "The survey's evidence record. action=evidence records what one operation established, "
            "with its corpus and whether that corpus is complete for the question (kept apart from "
            "having exhausted a page). finding records a claim citing evidence ids; an absence is "
            "refused unless every cited record searched a corpus recorded as complete (use "
            "not_found instead). retract marks a finding false with the reason and its replacement, "
            "and appends the retraction to every ticket that carries it. unknown_open records a "
            "question with the authority still needed to settle it; unknown_update adds supporting "
            "or contradicting evidence or resolves it with observed evidence, against the revision "
            "you read. unknowns lists a source's open questions."
        ),
        "inputSchema": tool_schema(
            properties={
                "action": enum_string_prop("What to do.", _values(SurveyRecordAction)),
                "source_id": _SOURCE_ID,
                "pass_id": _PASS_ID,
                "epistemic": enum_string_prop(
                    "evidence/finding: observed or inferred.", _values(SurveyEpistemic)
                ),
                "operation": string_prop("evidence: what was run or read."),
                "result": string_prop("evidence: what it showed."),
                "authority": string_prop(
                    "evidence: what makes it authoritative (the file at a commit, their README)."
                ),
                "limitations": string_prop("evidence: what it cannot show."),
                "residual_unknowns": string_prop("evidence: what it leaves open."),
                "corpus": string_list_prop("evidence: paths searched or read."),
                "corpus_complete": boolean_prop(
                    "evidence: the corpus holds everything the question could be answered from."
                ),
                "completeness_basis": string_prop(
                    "evidence: why the corpus is complete (required with corpus_complete)."
                ),
                "examined_count": integer_prop("evidence: items examined (with total_count)."),
                "total_count": integer_prop("evidence: items in the corpus (with examined_count)."),
                "claim": enum_string_prop("finding: what it asserts.", _values(SurveyFindingClaim)),
                "statement": string_prop("finding: the claim in one or two sentences."),
                "evidence_ids": string_list_prop("finding: the evidence it rests on."),
                "finding_id": string_prop("retract: the finding proved false."),
                "reason": string_prop("retract: why it is false."),
                "replaced_by_id": string_prop("retract: the finding that replaces it, if any."),
                "question": string_prop("unknown_open: the open question."),
                "authority_required": string_prop(
                    "unknown_open/update: the authority or environment that would settle it."
                ),
                "supporting": string_list_prop("unknown_open/update: evidence ids bearing for it."),
                "contradicting": string_list_prop(
                    "unknown_open/update: evidence ids bearing against it."
                ),
                "unknown_id": string_prop("unknown_update: the unknown."),
                "revision": integer_prop("unknown_update: the revision you read."),
                "resolve_evidence_id": string_prop(
                    "unknown_update: observed evidence that settles it."
                ),
                "resolution": string_prop("unknown_update: the answer (with resolve_evidence_id)."),
            },
            required=["action"],
        ),
    },
    {
        "name": McpTool.SURVEY_WRITE,
        "description": (
            "Land survey findings on a ticket. Name ticket (append) or create (new). Writes the "
            "criteria through the safe path, appends the block (with its licence line and what it "
            "rests on) to the ticket's 'Prior art — reference survey' document, keeps one pointer "
            "in the description, and reads all of it back byte-exact. At the 12-criterion cap the "
            "finding goes to a new related sibling; criteria aimed at a done ticket go to a new "
            "related ticket. Creating while similar open tickets exist is refused unless "
            "force_new_reason says why none fits. self_defect files a defect in our own tooling."
        ),
        "inputSchema": tool_schema(
            properties={
                "pass_id": _PASS_ID,
                "finding_ids": string_list_prop("The findings this block reports."),
                "ticket": string_prop("The ticket the findings answer, by external id."),
                "create": _NEW_TICKET,
                "criteria": string_list_prop("Acceptance criteria to add."),
                "block": string_prop(
                    "The finding prose; heading, licence and evidence lines are added."
                ),
                "force_new_reason": string_prop(
                    "Why no similar open ticket fits, when creating beside them."
                ),
                "self_defect": boolean_prop(
                    "A defect in loregarden's own tooling, filed as a bug."
                ),
            },
            required=["pass_id", "finding_ids", "block"],
        ),
    },
]


M = TypeVar("M", bound=BaseModel)


def _strings(value: Any, field: str) -> list[str]:
    return coerce_string_list(value, field=field)


#: Arguments the MCP bridge may send as JSON strings, and how each is decoded.
_COERCE: dict[str, Callable[[Any, str], Any]] = {
    **dict.fromkeys(
        (
            "corpus",
            "evidence_ids",
            "supporting",
            "contradicting",
            "paths",
            "unread_priority",
            "criteria",
            "finding_ids",
            "workspace_slugs",
        ),
        _strings,
    ),
    "create": lambda value, _field: coerce_mapping(value),
}

#: `reads` arrives as a list of objects, or as that list JSON-encoded.
_READS: TypeAdapter[list[ReadIn]] = TypeAdapter(Json[list[ReadIn]] | list[ReadIn])


def _model(model: type[M], arguments: dict[str, Any], **extra: Any) -> M:
    """Build `model` from the arguments it declares, dropping absent ones."""
    data = {
        key: _COERCE.get(key, lambda v, _f: v)(value, key)
        for key, value in arguments.items()
        if key in model.model_fields and value is not None
    }
    return model.model_validate({**data, **extra})


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


Handler = Callable[[Session, dict[str, Any]], Any]


def _enqueue(session: Session, args: dict[str, Any]) -> dict:
    enqueued = queue.enqueue_source(session, _model(SourceIn, args))
    return {
        "ok": True,
        "source_id": enqueued.source.id,
        "status": enqueued.source.status.value,
        "approval_id": enqueued.source.approval_id,
        "workspaces_attached": enqueued.attached,
        "scoped_pass_id": enqueued.scoped_pass.id if enqueued.scoped_pass else None,
    }


def _list(session: Session, args: dict[str, Any]) -> dict:
    workspace_id = (
        report.workspace_id_for(session, args["workspace_slug"])
        if args.get("workspace_slug")
        else None
    )
    return {
        "queue": queue.queue_listing(
            session, workspace_id=workspace_id, limit=args.get("limit") or 50
        )
    }


def _request_pass(session: Session, args: dict[str, Any]) -> dict:
    survey_pass = queue.request_pass(session, _model(PassRequestIn, args))
    return {"ok": True, "pass_id": survey_pass.id, "ordinal": survey_pass.ordinal}


def _link(session: Session, args: dict[str, Any]) -> dict:
    result = link_initiative(session, args["source_id"], args["initiative_id"], actor="agent")
    return result.model_dump()


def _unlink(session: Session, args: dict[str, Any]) -> dict:
    return unlink_initiative(session, args["source_id"], args["initiative_id"]).model_dump()


_QUEUE: dict[SurveyQueueAction, Handler] = {
    SurveyQueueAction.ENQUEUE: _enqueue,
    SurveyQueueAction.LIST: _list,
    SurveyQueueAction.REQUEST_PASS: _request_pass,
    SurveyQueueAction.STATUS: lambda s, a: report.source_status(s, a["source_id"]),
    SurveyQueueAction.COVERAGE: lambda s, a: passes.coverage_report(s, a["source_id"]),
    SurveyQueueAction.LINK_INITIATIVE: _link,
    SurveyQueueAction.UNLINK_INITIATIVE: _unlink,
}


def _reads(session: Session, args: dict[str, Any]) -> dict:
    reads = _READS.validate_python(args.get("reads") or [])
    if not reads:
        raise ValueError("reads: give one entry per path the pass opened, partly read, or skipped")
    stored = passes.record_reads(session, args["pass_id"], reads)
    survey_pass, _ = passes.get_pass(session, args["pass_id"])
    return {
        "ok": True,
        "recorded": len(stored),
        "pass": passes.pass_counts_line(session, survey_pass),
    }


def _finish(session: Session, args: dict[str, Any]) -> dict:
    survey_pass = passes.finish_pass(session, _model(PassFinishIn, args))
    return {
        "ok": True,
        "status": survey_pass.status.value,
        "outcome": survey_pass.outcome.value if survey_pass.outcome else None,
        "reads": passes.pass_counts_line(session, survey_pass),
    }


_PASS: dict[SurveyPassAction, Handler] = {
    SurveyPassAction.BRIEF: lambda s, a: report.pass_brief(s, a["pass_id"]),
    SurveyPassAction.READS: _reads,
    SurveyPassAction.FINISH: _finish,
}


def _evidence(session: Session, args: dict[str, Any]) -> dict:
    return {"ok": True, "evidence_id": record.record_evidence(session, _model(EvidenceIn, args)).id}


def _finding(session: Session, args: dict[str, Any]) -> dict:
    finding = record.record_finding(session, _model(FindingIn, args))
    line = record.render_finding(finding, record.finding_evidence(session, finding.id))
    return {"ok": True, "finding_id": finding.id, "renders_as": line}


def _retract(session: Session, args: dict[str, Any]) -> dict:
    finding = record.retract_finding(session, _model(RetractIn, args))
    return {
        "ok": True,
        "finding_id": finding.id,
        "retraction_appended_to": publish_retraction(session, finding),
    }


def _unknown_open(session: Session, args: dict[str, Any]) -> dict:
    unknown = record.open_unknown(session, _model(UnknownOpenIn, args))
    return {"ok": True, "unknown_id": unknown.id, "revision": unknown.revision}


def _unknown_update(session: Session, args: dict[str, Any]) -> dict:
    resolve = None
    if args.get("resolve_evidence_id"):
        resolve = {
            "evidence_id": args["resolve_evidence_id"],
            "resolution": args.get("resolution") or "",
        }
    unknown = record.update_unknown(session, _model(UnknownUpdateIn, args, resolve=resolve))
    return {
        "ok": True,
        "unknown_id": unknown.id,
        "status": unknown.status.value,
        "revision": unknown.revision,
    }


_RECORD: dict[SurveyRecordAction, Handler] = {
    SurveyRecordAction.EVIDENCE: _evidence,
    SurveyRecordAction.FINDING: _finding,
    SurveyRecordAction.RETRACT: _retract,
    SurveyRecordAction.UNKNOWN_OPEN: _unknown_open,
    SurveyRecordAction.UNKNOWN_UPDATE: _unknown_update,
    SurveyRecordAction.UNKNOWNS: lambda s, a: report.source_status(s, a["source_id"])["unknowns"],
}


def _dispatch(
    table: dict[Any, Handler], action_enum: type, session: Session, args: dict[str, Any]
) -> str:
    try:
        action = action_enum(args["action"])
    except ValueError as exc:
        raise ValueError(
            f"Unknown action {args.get('action')!r}; one of {[a.value for a in action_enum]}"
        ) from exc
    return _dump(table[action](session, args))


def survey_queue_tool(session: Session, arguments: dict[str, Any]) -> str:
    return _dispatch(_QUEUE, SurveyQueueAction, session, arguments)


def survey_pass_tool(session: Session, arguments: dict[str, Any]) -> str:
    return _dispatch(_PASS, SurveyPassAction, session, arguments)


def survey_record_tool(session: Session, arguments: dict[str, Any]) -> str:
    return _dispatch(_RECORD, SurveyRecordAction, session, arguments)


def survey_write_tool(session: Session, arguments: dict[str, Any]) -> str:
    return _dump(write_findings_to_ticket(session, _model(TicketWriteIn, arguments)).as_dict())


HANDLERS = {
    McpTool.SURVEY_QUEUE.value: survey_queue_tool,
    McpTool.SURVEY_PASS.value: survey_pass_tool,
    McpTool.SURVEY_RECORD.value: survey_record_tool,
    McpTool.SURVEY_WRITE.value: survey_write_tool,
}
