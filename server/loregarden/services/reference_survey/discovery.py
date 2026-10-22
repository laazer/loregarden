"""Finding references on a topic, by an agent, for a person to keep or drop.

A discovery run is one carrier ticket on the `reference-survey` workflow, queued
into a lane like a survey pass. Its brief names the topic's search, the question a
pass will read each find for, and how many to propose. The agent enqueues each
find with `loregarden_survey_queue action=enqueue discovery_id=…`
(`enqueue_found`), which serves the run's workspaces, links its initiatives, and
files the usual agent card: nothing an agent finds is read until a person keeps it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.config import settings
from loregarden.models.domain import Ticket
from loregarden.models.domain.survey_enums import SurveyTopic
from loregarden.models.domain.survey_tables import SurveyDiscovery, SurveySource
from loregarden.services.queue_lanes import QueueLaneService, least_busy_lane
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.inbox import report_exception
from loregarden.services.reference_survey.initiatives import follow, resolve_initiatives
from loregarden.services.reference_survey.queue import Enqueued, enqueue_source, resolve_workspaces
from loregarden.services.reference_survey.schemas import SourceIn
from loregarden.services.reference_survey.topics import TOPICS
from loregarden.services.ticket_service import TicketService
from loregarden.services.workflow_service import WorkflowService
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

logger = logging.getLogger(__name__)

#: More than this many finds per run is a search, not a shortlist.
MAX_WANTED = 20


class DiscoveryIn(BaseModel):
    topic: SurveyTopic
    #: The first is home: where the run's ticket lives.
    workspace_slugs: list[str] = Field(min_length=1)
    initiative_ids: list[str] = Field(default_factory=list)
    #: Anything more specific than the topic; "" when the topic is enough.
    focus: str = ""
    wanted: int = Field(default=5, ge=1, le=MAX_WANTED)


class DiscoveryView(BaseModel):
    id: str
    topic: SurveyTopic
    label: str
    focus: str
    wanted: int
    workspaces: list[str]
    #: The run's ticket and its state; the run is over when the ticket is settled.
    carrier: str | None
    carrier_state: str | None
    #: Sources it has proposed so far.
    found: int
    created_at: datetime


def _brief(discovery: SurveyDiscovery, workspaces: list[str]) -> str:
    topic = TOPICS[discovery.topic]
    focus = f" Narrowed to: {discovery.focus.strip()}." if discovery.focus.strip() else ""
    slugs = ", ".join(workspaces)
    return "\n".join(
        [
            f"Reference discovery `{discovery.id}`: find up to {discovery.wanted} references on "
            f"**{topic.label}**, for {slugs}.",
            "",
            f"**Look for:** {topic.search}.{focus}",
            f"**A pass will read each one for:** {topic.question}",
            "",
            "1. Search the web and GitHub. Prefer active, documented open-source projects (for UI "
            "and UX, sites that show the work too). Skip anything already in the library: "
            "`loregarden_survey_queue action=list`, and enqueue says when a URL is known.",
            "2. For each find, call `loregarden_survey_queue action=enqueue` with "
            f"`discovery_id={discovery.id}`, `workspace_slugs=[{slugs}]`, its `kind` and `url`, "
            "a `question` narrowing the one above to what this find offers, and a `queued_reason` "
            "of one sentence on what you saw that makes it worth reading.",
            "3. Do not clone or read the finds in depth: a person keeps or drops each, and a pass "
            "reads the ones kept. Licences are checked then.",
            "4. Finish with `loregarden_complete_stage`, listing what you proposed, what you "
            "passed over, and why.",
        ]
    )


def start_discovery(session: Session, body: DiscoveryIn) -> DiscoveryView:
    """Record the run and queue its ticket, or refuse with what holds it."""
    workspaces = resolve_workspaces(session, body.workspace_slugs)
    initiatives = resolve_initiatives(session, body.initiative_ids)
    home = workspaces[0]
    if settings.sandbox:
        raise SurveyError(feeder.SANDBOX_WAITING)
    parent = feeder.carrier_parent(session, home.id)
    if parent is None:
        raise SurveyError(
            f"No open ticket in {home.slug} is tagged `{feeder.CARRIER_PARENT_TAG}`; tag the "
            "feature survey work should be filed under."
        )
    discovery = SurveyDiscovery(
        workspace_id=home.id,
        topic=body.topic,
        focus=body.focus.strip(),
        wanted=body.wanted,
        workspaces_json=json.dumps([w.slug for w in workspaces]),
        initiatives_json=json.dumps([i.id for i in initiatives]),
    )
    session.add(discovery)
    session.flush()
    carrier = TicketService(session).create_ticket(
        workspace_slug=home.slug,
        title=f"Discover references: {TOPICS[body.topic].label}",
        work_item_type=feeder.carrier_type(parent),
        parent_ticket_id=parent.id,
        description=_brief(discovery, [w.slug for w in workspaces]),
        acceptance_criteria=[
            f"Up to {body.wanted} references on the topic are enqueued with this run's "
            "discovery_id, each with a question and a one-sentence reason.",
            "Nothing already in the library is proposed again, and no find is cloned or read in "
            "depth.",
            "The stage closes listing what was proposed, what was passed over, and why.",
        ],
        priority=3,
        created_by="reference-survey",
    )
    WorkflowService(session).set_ticket_workflow_template(carrier, feeder.SURVEY_WORKFLOW_SLUG)
    discovery.carrier_ticket_id = carrier.id
    session.add(discovery)
    session.commit()
    lanes = QueueLaneService(session)
    try:
        lanes.add_to_lane(
            ticket_id=carrier.id, slot_number=least_busy_lane(lanes), auto_approve=True
        )
    except ValueError as exc:
        report_exception(
            session,
            workspace_id=home.id,
            title=f"Reference discovery could not be queued: {TOPICS[body.topic].label}",
            detail=f"{exc}. Its ticket {carrier.external_id} exists; queue it by hand or close it.",
        )
    return discovery_view(session, discovery)


def enqueue_found(session: Session, body: SourceIn, discovery_id: str) -> Enqueued:
    """An agent's find for a run: it serves the run's workspaces and initiatives,
    and waits for a person like any agent proposal."""
    discovery = session.get(SurveyDiscovery, discovery_id)
    if discovery is None:
        raise SurveyNotFound(f"No discovery run {discovery_id!r}")
    slugs = list(dict.fromkeys([*json.loads(discovery.workspaces_json), *body.workspace_slugs]))
    request = body.model_copy(
        update={
            "workspace_slugs": slugs,
            "question": body.question or TOPICS[discovery.topic].question,
            "queued_reason": body.queued_reason or TOPICS[discovery.topic].why,
        }
    )
    added = enqueue_source(session, request)
    if added.source.discovery_id is None:
        added.source.discovery_id = discovery.id
        session.add(added.source)
        session.commit()
    follow(
        session,
        added.source.id,
        resolve_initiatives(session, json.loads(discovery.initiatives_json)),
        actor="agent",
    )
    return added


def discovery_view(session: Session, discovery: SurveyDiscovery) -> DiscoveryView:
    carrier = (
        session.get(Ticket, discovery.carrier_ticket_id) if discovery.carrier_ticket_id else None
    )
    found = session.exec(
        select(func.count())
        .select_from(SurveySource)
        .where(SurveySource.discovery_id == discovery.id)
    ).one()
    return DiscoveryView(
        id=discovery.id,
        topic=discovery.topic,
        label=TOPICS[discovery.topic].label,
        focus=discovery.focus,
        wanted=discovery.wanted,
        workspaces=json.loads(discovery.workspaces_json),
        carrier=carrier.external_id if carrier else None,
        carrier_state=carrier.state.value if carrier else None,
        found=found,
        created_at=discovery.created_at,
    )


def recent_discoveries(
    session: Session, workspace_slug: str, limit: int = 10
) -> list[DiscoveryView]:
    """The runs that serve this workspace, newest first."""
    rows = session.exec(
        select(SurveyDiscovery)
        .where(col(SurveyDiscovery.workspaces_json).contains(json.dumps(workspace_slug)))
        .order_by(col(SurveyDiscovery.created_at).desc())
        .limit(limit)
    ).all()
    return [discovery_view(session, d) for d in rows]
