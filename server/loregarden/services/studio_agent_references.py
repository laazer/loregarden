"""What still references a Studio agent, and the refusal that names it.

`DELETE /api/studio/agents/{slug}` must succeed or refuse in words, never 500.
The refusal is decided *here*, by reading the places an agent slug can appear,
and never by catching the integrity error the delete itself used to raise: the
only foreign key to `studio_agents` is the agent's own version history, so a
refusal derived from that error names `studio_agent_versions` as what still
references the agent — a refusal the operator can never clear, which is the
failure mode lg-milestone-that-1239 exists to stop (AC4, plan decision D4).

Two halves, because they have opposite remedies:

* **blocking** — a template, a draft, or a live ticket's agent pin. Each is a
  place an operator can go and edit, so refusing is actionable.
* **historical** — the append-only run ledger. Nothing the operator does can
  clear a row in it, so blocking on one is a refusal with no exit (A7). It is
  counted and reported, and only ever alongside a blocking class.

The match is the orchestrator's match, not a tighter or a looser one:
`stage_offers_agent` (`services/studio_routing.py`) compares
`(stage.agent_id or "").strip() == agent_id`. Looser (casefold, slugify)
invents a reference no stage would dispatch; tighter (a bare `==`) misses a
padded `agent_id` that really does dispatch, and leaves the stale slot behind.
A raw substring search over `stages_json` is wrong in the same direction as
looser: it hits a stage key or a skill name that merely contains the slug.
"""

import json
import logging
from enum import Enum

from loregarden.models.domain import (
    AgentRun,
    McpToolCall,
    StageFanoutGroup,
    StudioWorkflow,
    Ticket,
    TicketState,
    WorkflowTemplate,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlmodel import Field, Session, SQLModel, select

logger = logging.getLogger(__name__)

#: Identifiers reported per class. The operator reads these in a toast; `planner`
#: carries ~76 blocking identifiers and 491 agent runs on the live database, and
#: the whole list in a nine-second toast is the failure AC8 names.
MAX_REPORTED_IDS = 10

#: Ticket states whose agent pin routes nothing, so it cannot block (A9).
TERMINAL_TICKET_STATES = (TicketState.DONE, TicketState.WONT_DO)


class AgentReferenceKind(str, Enum):
    """One place an agent slug can appear. The wire carries these values."""

    WORKFLOW_TEMPLATE = "workflow_template"
    STUDIO_WORKFLOW = "studio_workflow"
    TICKET_NEXT_AGENT = "ticket_next_agent"
    TICKET_SCOPE_REROUTE_AGENT = "ticket_scope_reroute_agent"
    AGENT_RUN = "agent_run"
    MCP_TOOL_CALL = "mcp_tool_call"
    STAGE_FANOUT_GROUP = "stage_fanout_group"


#: Operator-facing wording, singular and plural in one, as
#: `refuse_unconfirmed_stage_removal` already phrases its own refusal.
_KIND_LABELS: dict[AgentReferenceKind, str] = {
    AgentReferenceKind.WORKFLOW_TEMPLATE: "workflow template(s)",
    AgentReferenceKind.STUDIO_WORKFLOW: "draft workflow(s)",
    AgentReferenceKind.TICKET_NEXT_AGENT: "live ticket(s) pinned through next_agent",
    AgentReferenceKind.TICKET_SCOPE_REROUTE_AGENT: (
        "live ticket(s) pinned through scope_reroute_agent"
    ),
    AgentReferenceKind.AGENT_RUN: "past agent run(s)",
    AgentReferenceKind.MCP_TOOL_CALL: "past MCP tool call(s)",
    AgentReferenceKind.STAGE_FANOUT_GROUP: "fan-out group snapshot(s)",
}


class AgentReferenceClass(SQLModel):
    """One class of reference: its identifiers, truncated, beside its true total.

    `count` is the number of referencing *things*, not of occurrences: "8
    templates still name it" is a number of places the operator must go, and a
    template naming the agent at two stages and a classify route is still one
    visit.
    """

    kind: AgentReferenceKind
    ids: list[str] = Field(default_factory=list)
    count: int = 0


class AgentReferences(SQLModel):
    blocking: list[AgentReferenceClass] = Field(default_factory=list)
    historical: list[AgentReferenceClass] = Field(default_factory=list)


class AgentDeleteRefused(ValueError):
    """A delete refused because something still references the agent.

    A `ValueError` subclass so the route can catch it *ahead* of the generic
    `except ValueError` that maps every other service failure to 404, and
    carries the structured references because the notice in the Studio panel
    reads its links off this payload rather than a second request.
    """

    def __init__(self, message: str, references: AgentReferences) -> None:
        super().__init__(message)
        self.references = references


class _ScannedAgentRef(BaseModel):
    """A `classify_routes` / `parallel_agents` entry, read leniently."""

    model_config = ConfigDict(extra="ignore")

    agent_id: str | None = None


class _ScannedStage(BaseModel):
    """A `stages_json` stage, narrowed to the three positions that name an agent.

    Modelled rather than read key by key: `stages_json` is a schemaless text
    column, so one row written by an older build is a foreign payload, and the
    three fields below are the only ones this scan is allowed to look at.
    Every other key is ignored, including ones a later `StudioWorkflowStage`
    gains — see `models/domain/schemas.py` for the authoritative stage, whose
    `agent_id`, `classify_routes[].agent_id` and `parallel_agents[].agent_id`
    these mirror.
    """

    model_config = ConfigDict(extra="ignore")

    agent_id: str | None = None
    classify_routes: list[_ScannedAgentRef] | None = None
    parallel_agents: list[_ScannedAgentRef] | None = None


_STAGES_ADAPTER = TypeAdapter(list[_ScannedStage])


def _matches(value: str | None, agent_id: str) -> bool:
    """The orchestrator's own comparison; see this module's docstring."""
    return (value or "").strip() == agent_id


def _stages_name_agent(stages_json: str, agent_id: str, *, source: str, slug: str) -> bool:
    """True when this `stages_json` dispatches `agent_id` at any position.

    A row that cannot be read as a stage list is reported on the log at
    `warning` and treated as naming nobody. Not swallowed, and not fatal: the
    scan reads several sources, and ending it on one unreadable row would hide
    a template that genuinely blocks and delete the agent out from under it.
    """
    try:
        stages = _STAGES_ADAPTER.validate_python(json.loads(stages_json))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "Studio agent reference scan could not read %s '%s' stages_json while "
            "scanning for agent '%s'; its stages were not checked: %s",
            source,
            slug,
            agent_id,
            exc,
        )
        return False
    for stage in stages:
        if _matches(stage.agent_id, agent_id):
            return True
        for entry in (stage.classify_routes or []) + (stage.parallel_agents or []):
            if _matches(entry.agent_id, agent_id):
                return True
    return False


def _reference_class(kind: AgentReferenceKind, identifiers: set[str]) -> AgentReferenceClass:
    """One class, sorted and truncated. Sorted so two clicks on one refused
    delete answer identically rather than reshuffling with set iteration."""
    ordered = sorted(identifiers)
    return AgentReferenceClass(
        kind=kind,
        ids=ordered[:MAX_REPORTED_IDS],
        count=len(ordered),
    )


def _template_slugs_naming(session: Session, agent_id: str) -> set[str]:
    return {
        template.slug
        for template in session.exec(select(WorkflowTemplate)).all()
        if _stages_name_agent(
            template.stages_json, agent_id, source="workflow template", slug=template.slug
        )
    }


def _draft_slugs_naming(session: Session, agent_id: str) -> set[str]:
    return {
        draft.slug
        for draft in session.exec(select(StudioWorkflow)).all()
        if _stages_name_agent(draft.stages_json, agent_id, source="draft workflow", slug=draft.slug)
    }


def _live_tickets_pinning(session: Session, column, agent_id: str) -> set[str]:
    """External ids of non-terminal tickets whose `column` names the agent.

    Restricted to live tickets because a closed ticket's pin routes nothing, and
    refusing on one is a blocker the operator can only clear by editing history
    (K5, A2, A9).
    """
    rows = session.exec(
        select(Ticket).where(column == agent_id, Ticket.state.notin_(TERMINAL_TICKET_STATES))
    ).all()
    return {ticket.external_id for ticket in rows}


def scan_agent_references(session: Session, slug: str) -> AgentReferences:
    """Every reference to `slug`, split into the blocking and historical halves.

    A pure read: nothing is added to or removed from the session, so
    `delete_agent` can run this *before* it touches a row and leave the session
    untouched when it refuses.
    """
    blocking = [
        _reference_class(
            AgentReferenceKind.WORKFLOW_TEMPLATE, _template_slugs_naming(session, slug)
        ),
        _reference_class(AgentReferenceKind.STUDIO_WORKFLOW, _draft_slugs_naming(session, slug)),
        _reference_class(
            AgentReferenceKind.TICKET_NEXT_AGENT,
            _live_tickets_pinning(session, Ticket.next_agent, slug),
        ),
        _reference_class(
            AgentReferenceKind.TICKET_SCOPE_REROUTE_AGENT,
            _live_tickets_pinning(session, Ticket.scope_reroute_agent, slug),
        ),
    ]
    historical = [
        _reference_class(
            AgentReferenceKind.AGENT_RUN,
            {
                run.run_code
                for run in session.exec(select(AgentRun).where(AgentRun.agent_id == slug)).all()
            },
        ),
        _reference_class(
            AgentReferenceKind.MCP_TOOL_CALL,
            {
                call.run_id
                for call in session.exec(
                    select(McpToolCall).where(McpToolCall.agent_id == slug)
                ).all()
            },
        ),
        _reference_class(
            AgentReferenceKind.STAGE_FANOUT_GROUP,
            {
                group.id
                for group in session.exec(
                    select(StageFanoutGroup).where(StageFanoutGroup.pre_fanout_next_agent == slug)
                ).all()
            },
        ),
    ]
    # Only non-empty classes reach the operator: a reported class with nothing
    # in it reads as a reference nobody can find.
    return AgentReferences(
        blocking=[entry for entry in blocking if entry.count],
        historical=[entry for entry in historical if entry.count],
    )


def _class_phrase(entry: AgentReferenceClass) -> str:
    phrase = f"{entry.count} {_KIND_LABELS[entry.kind]}: {', '.join(entry.ids)}"
    hidden = entry.count - len(entry.ids)
    return f"{phrase} (and {hidden} more)" if hidden else phrase


def describe_agent_delete_refusal(slug: str, references: AgentReferences) -> str:
    """One bounded sentence an operator can act on.

    Bounded at `MAX_REPORTED_IDS` per class, with each class carrying its true
    total, because this string is the whole of what the toast shows and
    `http.ts` lifts `detail` into it only when it is a `str` (AC8, K10).
    """
    blocking = "; ".join(_class_phrase(entry) for entry in references.blocking)
    sentence = (
        f"Cannot delete agent '{slug}' while it is still referenced by {blocking}. "
        "Remove the agent from those first, then delete it."
    )
    if references.historical:
        kept = ", ".join(
            f"{entry.count} {_KIND_LABELS[entry.kind]}" for entry in references.historical
        )
        sentence += f" Its run history ({kept}) is kept either way and does not block the delete."
    return sentence


def refuse_agent_delete(slug: str, references: AgentReferences) -> AgentDeleteRefused:
    return AgentDeleteRefused(describe_agent_delete_refusal(slug, references), references)
