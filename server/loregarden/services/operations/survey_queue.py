"""The reference-survey queue, as one operation: `loregarden_survey_queue` and the
reference library's writes.

Written twice before, as the tool's `action` table and as routes under
`/api/reference-library/`, and the two had drifted. Adding a source over REST
also linked areas and followed initiatives, and the route's docstring said a new
source waits for its admission card while the code admitted it. Over MCP it did
neither, but took a discovery run or a research question to answer. Each action
now has one handler, and the rule that differs by caller is the caller's:

- **Admission.** A person adding a source here is its admission; an agent's
  proposal waits for a person's card in the inbox (`enqueue_source`).
- **Who linked an initiative** is "operator" over REST and "agent" otherwise.

The page offers enqueue, the initiative links, the upstream check, review-now
and the auto re-review switch; `list`, `status`, `coverage` and `request_pass`
are an agent's (the page reads `library_list` and `library_detail`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveyQueueAction,
    SurveySourceKind,
)
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    OperationUnavailable,
    Transport,
)
from loregarden.services.reference_survey import passes, queue, report
from loregarden.services.reference_survey.areas import (
    link_picks,
    resolve_picks,
    with_area_workspaces,
)
from loregarden.services.reference_survey.discovery import enqueue_found
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.initiatives import (
    follow,
    link_initiative,
    resolve_initiatives,
    unlink_initiative,
)
from loregarden.services.reference_survey.library import LibraryRow, library_row
from loregarden.services.reference_survey.rereview import set_excluded
from loregarden.services.reference_survey.research_library import link_enqueued
from loregarden.services.reference_survey.schedule import ReviewNowIn, review_now
from loregarden.services.reference_survey.schemas import EntryAreaIn, PassRequestIn, SourceIn
from loregarden.services.reference_survey.upstream import check_upstream_by_id
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class SurveyQueueCall(BaseModel):
    """The tool's flat arguments, which the routes send too. Each action reads
    its own, and the service model it builds validates them."""

    model_config = _LENIENT

    action: SurveyQueueAction
    """What to do."""
    source_id: str = ""
    """Survey source id."""
    # enqueue
    workspace_slugs: list[str] = []
    """enqueue: every workspace whose backlog the source should answer; the first is home. A URL
    already known gains these workspaces instead of a second survey.
    """
    kind: SurveySourceKind | None = None
    """enqueue: what the source is."""
    url: str | None = None
    """enqueue: repository or https URL."""
    name: str | None = None
    """enqueue: display name (default: last URL segment)."""
    question: str = ""
    """enqueue/request_pass/review_now: what the source or pass is meant to answer. Optional:
    without one, a pass reads for what would serve the workspaces and initiatives it serves
    (review_now: only for a surveyed source).
    """
    #: enqueue: why it is worth reading. review_now: why a refused source is read after all.
    queued_reason: str = ""
    """enqueue: why it is worth reading now (optional). review_now: why a refused source should be
    read after all (required to reopen one; the licence gate still runs first).
    """
    priority: int | None = None
    """enqueue: lower runs first (default 100)."""
    discovery_id: str = ""
    """enqueue: the discovery run this find belongs to (from its brief); the find then serves the
    run's workspaces and initiatives.
    """
    research_question_id: str = ""
    """enqueue: the research question this pick answers; its pass brief then carries the question
    and the claims to confirm.
    """
    initiative_ids: list[str] = []
    """enqueue, review_now: initiatives to follow the source (its open tickets join them), by
    UUID or external id.
    """
    areas: list[EntryAreaIn] = []
    """enqueue: project areas the source serves; each also makes it serve that area's workspace."""
    # list
    workspace_slug: str = ""
    """list: only sources serving this workspace."""
    limit: int | None = None
    """list: max rows (default 50)."""
    # request_pass
    mode: SurveyPassMode | None = None
    """request_pass: continuation or scoped."""
    paths: list[str] = []
    """request_pass: map paths to read; empty lets the feeder slice the map."""
    # review_now
    only_for: list[str] = []
    """review_now: read for only these of the workspaces the source serves, for this pass alone.
    Empty reads for all.
    """
    # link_initiative / unlink_initiative
    initiative_id: str = ""
    """link_initiative/unlink_initiative: the initiative, by UUID or external id."""
    # set_auto_rereview
    excluded: bool | None = None
    """set_auto_rereview: true takes the source out of auto re-review, false puts it back."""


class AddSourceResult(BaseModel):
    """What the library's add form is answered with."""

    source: LibraryRow
    #: Workspaces this call added to the source (empty when it already served them all).
    attached: list[str]
    #: A scoped pass was queued because a surveyed source gained a workspace.
    scoped_pass: int | None
    #: Initiatives now following the source, by external id.
    initiatives_linked: list[str]


@dataclass(frozen=True)
class Answered:
    """An action's result, worded per transport on demand: the page's response
    model, and the agent's JSON (the same object for most actions)."""

    agent: Callable[[], Any]
    page: Callable[[], Any]


def _same(value: Any) -> Answered:
    return Answered(agent=lambda: value, page=lambda: value)


def _actor(caller: Caller) -> str:
    """Who linked an initiative, as the link records it."""
    return "operator" if caller.transport is Transport.REST else "agent"


def _source_in(call: SurveyQueueCall, workspace_slugs: list[str]) -> SourceIn:
    sent = call.model_dump(
        include={"kind", "url", "name", "question", "queued_reason", "priority"},
        exclude_none=True,
    )
    return SourceIn.model_validate(
        {**sent, "workspace_slugs": workspace_slugs, "initiative_ids": call.initiative_ids}
    )


def _enqueue(session: Session, call: SurveyQueueCall, caller: Caller) -> Answered:
    # Every name is resolved before anything is written.
    initiatives = resolve_initiatives(session, call.initiative_ids)
    picks = resolve_picks(session, call.areas)
    body = _source_in(call, with_area_workspaces(call.workspace_slugs, picks))
    enqueued = (
        enqueue_found(session, body, call.discovery_id)
        if call.discovery_id
        # A person adding it is its admission; an agent's waits for the card.
        else queue.enqueue_source(session, body, admitted=caller.transport is Transport.REST)
    )
    source = enqueued.source
    if call.research_question_id:
        link_enqueued(session, source.id, call.research_question_id)
    linked = follow(session, source.id, initiatives, actor=_actor(caller))
    link_picks(session, source.id, picks)
    return Answered(
        agent=lambda: {
            "ok": True,
            "source_id": source.id,
            "status": source.status.value,
            "approval_id": source.approval_id,
            "workspaces_attached": enqueued.attached,
            "scoped_pass_id": enqueued.scoped_pass.id if enqueued.scoped_pass else None,
        },
        page=lambda: AddSourceResult(
            source=library_row(session, source),
            attached=enqueued.attached,
            scoped_pass=enqueued.scoped_pass.ordinal if enqueued.scoped_pass else None,
            initiatives_linked=[link.initiative for link in linked],
        ),
    )


def _list(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    workspace_id = (
        report.workspace_id_for(session, call.workspace_slug) if call.workspace_slug else None
    )
    return _same(
        {"queue": queue.queue_listing(session, workspace_id=workspace_id, limit=call.limit or 50)}
    )


def _request_pass(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    request = PassRequestIn.model_validate(
        call.model_dump(include={"source_id", "mode", "question", "paths"}, exclude_none=True)
    )
    survey_pass = queue.request_pass(session, request)
    return _same({"ok": True, "pass_id": survey_pass.id, "ordinal": survey_pass.ordinal})


def _status(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    return _same(report.source_status(session, call.source_id))


def _coverage(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    return _same(passes.coverage_report(session, call.source_id))


def _link(session: Session, call: SurveyQueueCall, caller: Caller) -> Answered:
    return _same(link_initiative(session, call.source_id, call.initiative_id, actor=_actor(caller)))


def _unlink(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    return _same(unlink_initiative(session, call.source_id, call.initiative_id))


def _check_upstream(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    return _same(library_row(session, check_upstream_by_id(session, call.source_id)))


def _review_now(session: Session, call: SurveyQueueCall, caller: Caller) -> Answered:
    body = ReviewNowIn(
        question=call.question,
        reason=call.queued_reason,
        workspace_slugs=call.workspace_slugs,
        initiative_ids=call.initiative_ids,
        only_for=call.only_for,
    )
    return _same(review_now(session, call.source_id, body, actor=_actor(caller)))


def _set_auto_rereview(session: Session, call: SurveyQueueCall, _caller: Caller) -> Answered:
    if call.excluded is None:
        raise SurveyError(
            "set_auto_rereview needs `excluded`: true to take the source out, false to put it back"
        )
    return _same(
        library_row(session, set_excluded(session, call.source_id, excluded=call.excluded))
    )


_Handler = Callable[[Session, SurveyQueueCall, Caller], Answered]

_ACTIONS: dict[SurveyQueueAction, _Handler] = {
    SurveyQueueAction.ENQUEUE: _enqueue,
    SurveyQueueAction.LIST: _list,
    SurveyQueueAction.REQUEST_PASS: _request_pass,
    SurveyQueueAction.STATUS: _status,
    SurveyQueueAction.COVERAGE: _coverage,
    SurveyQueueAction.LINK_INITIATIVE: _link,
    SurveyQueueAction.UNLINK_INITIATIVE: _unlink,
    SurveyQueueAction.CHECK_UPSTREAM: _check_upstream,
    SurveyQueueAction.REVIEW_NOW: _review_now,
    SurveyQueueAction.SET_AUTO_REREVIEW: _set_auto_rereview,
}

#: The actions an agent has and the page does not, each with the reason.
_NOT_ON_REST: dict[SurveyQueueAction, str] = {
    SurveyQueueAction.LIST: "the library page reads library_list, with counts per source",
    SurveyQueueAction.STATUS: "the source page reads library_detail",
    SurveyQueueAction.COVERAGE: "the source page reads library_detail",
    SurveyQueueAction.REQUEST_PASS: "a person reviews a source now (review_now) instead",
}


def _queue(session: Session, call: SurveyQueueCall, caller: Caller) -> Answered:
    reason = _NOT_ON_REST.get(call.action) if caller.transport is Transport.REST else None
    if reason is not None:
        raise OperationUnavailable(
            OperationId.SURVEY_QUEUE, caller.transport, f"action {call.action.value!r}: {reason}"
        )
    return _ACTIONS[call.action](session, call, caller)


def _agent_reply(_session: Session, answered: Answered) -> Any:
    return answered.agent()


def _page_reply(_session: Session, answered: Answered) -> Any:
    return answered.page()


SURVEY_QUEUE = (
    Operation(
        id=OperationId.SURVEY_QUEUE,
        effect=Effect.WRITE,
        args=SurveyQueueCall,
        handler=_queue,
        replies={
            Transport.MCP: _agent_reply,
            Transport.CLI: _agent_reply,
            Transport.REST: _page_reply,
        },
    ),
)
