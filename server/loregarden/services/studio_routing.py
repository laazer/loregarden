"""Classify-stage routing: pick the agent (and, with U5b, the branch) for a ticket."""

from __future__ import annotations

import json
import re

from loregarden.agents.registry import get_agent
from loregarden.core.workflow_terminal import (  # noqa: F401 — re-exported for existing import sites
    TERMINAL_STAGE_KEY,
    find_terminal_stage,
    is_terminal_stage,
)
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session

_SPECIALTY_SYNONYMS: dict[str, list[str]] = {
    # Deliberately narrow. Generic structural verbs — move, split, simplify,
    # clean — appear just as often in feature work, and a route that fires on
    # them would relabel new features as refactors. These words are the ones
    # that rarely describe anything else.
    "refactor": [
        "refactoring",
        "rename",
        "extract",
        "restructure",
        "consolidate",
        "deduplicate",
        "untangle",
    ],
    "frontend": [
        "ui",
        "component",
        "modal",
        "button",
        "page",
        "screen",
        "css",
        "style",
        "styling",
        "react",
        "client",
        "dialog",
        "tooltip",
        "layout",
        "render",
        "dom",
        "browser",
        "form",
        "dropdown",
        "menu",
        "tab",
        "widget",
    ],
    "backend": [
        "api",
        "endpoint",
        "server",
        "database",
        "db",
        "schema",
        "migration",
        "service",
        "route",
        "controller",
        "query",
        "auth",
        "middleware",
        "sql",
        "orm",
        "cron",
        "worker",
        "queue",
    ],
}


def _word_in_haystack(word: str, haystack: str) -> bool:
    pattern = r"\b" + re.escape(word.lower()) + r"\b"
    return re.search(pattern, haystack) is not None


def _route_match_score(route: ClassifyRoute, haystack: str) -> int | None:
    """Returns the keyword hit count if the route is eligible, else None.

    Specialty is the hard gate: tickets rarely spell out an implementation
    language, but they do describe the domain (buttons, endpoints, etc.), so
    a specialty match is required whenever the route declares one. Language
    only contributes bonus score to break ties between otherwise-eligible
    routes — requiring it outright meant tickets that never mention
    "typescript"/"python"/etc. could never match any language-scoped route.
    """
    spec_words: list[str] = []
    for spec in route.specialties:
        spec_words.append(spec)
        spec_words.extend(_SPECIALTY_SYNONYMS.get(spec.lower(), []))
    spec_hits = [word for word in spec_words if _word_in_haystack(word, haystack)]
    if route.specialties and not spec_hits:
        return None

    lang_hits = [lang for lang in route.languages if _word_in_haystack(lang, haystack)]
    return len(spec_hits) + len(lang_hits)


def _classify_haystack(ticket: Ticket) -> str:
    """Text used to score classify routes.

    Description is deliberately excluded: stage specs and rework notes accumulate
    both frontend and backend path mentions, which drowned title/AC signal and
    stuck server-only tickets on frontend_implementer (ticket 327).
    """
    acceptance_criteria = ""
    try:
        acceptance_criteria = " ".join(json.loads(ticket.acceptance_criteria_json or "[]"))
    except (TypeError, ValueError):
        pass

    return " ".join(
        [
            ticket.title or "",
            ticket.external_id or "",
            acceptance_criteria,
        ]
    ).lower()


def _select_classify_route(ticket: Ticket, stage: WorkflowStageDef) -> ClassifyRoute | None:
    """Pick the winning route, or None when the stage isn't classify-routed.

    Shared by the agent and branch resolvers so a stage's agent and its branch
    always come from the same route.
    """
    if stage.stage_type != "classify" or not stage.classify_routes:
        return None

    haystack = _classify_haystack(ticket)

    default_route: ClassifyRoute | None = None
    best_route: ClassifyRoute | None = None
    best_score = -1
    for route in stage.classify_routes:
        if route.default:
            default_route = route
        score = _route_match_score(route, haystack)
        if score is not None and score > best_score:
            best_route = route
            best_score = score

    # Content classification wins whenever the ticket's current text gives a
    # real keyword signal (best_score > 0). Only fall back to the sticky
    # next_agent hint when the text is ambiguous — otherwise a stale hint
    # left over from an earlier, unrelated stage (e.g. next_agent stuck on
    # "frontend_implementer" from a prior route) permanently overrides a
    # ticket that has since been correctly reclassified to a different
    # specialist. See loregarden #164 / stale-next_agent classify loop.
    if best_route is not None and best_score > 0:
        return best_route

    # The pin steers only when it names exactly ONE route. It carries an agent,
    # and an agent does not identify a route: `studio-loregarden-tdd-v3`'s triage
    # stage names `ticket_scoper` on both of its routes, one jumping to
    # `test-design` for typo/docs work and one running the full pipeline. Matching
    # on the agent returned whichever came first, so a pin meaning "the scoper
    # handles this" was read as "skip plan, plan-synthesis, ui-design and spec".
    #
    # Measured over 488 non-terminal tickets sitting on a classify stage: this
    # branch decided 471 of them (96%) and overrode the route the template marked
    # `default` in every single one. It was not breaking ties, it was the router.
    #
    # A pin that matches several routes expresses an opinion about the agent and
    # none about the branch, so it is not a routing decision and the fallback
    # below — content, then the declared default — answers instead. Where the
    # routes name distinct agents, which is every other classify stage in the
    # live templates, the pin is unambiguous and still steers: that is the rework
    # reroute (`apply_stage_route` on reject) sending work back to the specialist
    # who should redo it.
    next_agent = (ticket.next_agent or "").strip()
    if next_agent and get_agent(next_agent):
        matches = [route for route in stage.classify_routes if route.agent_id == next_agent]
        if len(matches) == 1:
            return matches[0]

    return best_route or default_route or stage.classify_routes[0]


def resolve_classify_route(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str]:
    route = _select_classify_route(ticket, stage)
    if route is None:
        return stage.agent_id, stage.skill_name
    return route.agent_id, route.skill_name or stage.skill_name


def resolve_classify_branch(ticket: Ticket, stage: WorkflowStageDef) -> str:
    """Stage key this ticket's classify route branches to, or "" for linear flow."""
    route = _select_classify_route(ticket, stage)
    return route.to_stage if route else ""


def _resolve_next_agent_from_routes(
    ticket: Ticket,
    stage: WorkflowStageDef,
) -> tuple[str, str] | None:
    next_agent = (ticket.next_agent or "").strip()
    if not next_agent:
        return None

    if not get_agent(next_agent):
        return None

    if stage.classify_routes:
        for route in stage.classify_routes:
            if route.agent_id == next_agent:
                return next_agent, route.skill_name or stage.skill_name
        return None

    return next_agent, stage.skill_name or ""


def _resolve_next_agent_override(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str] | None:
    if not (stage.agent_id or "").strip() and stage.stage_type not in {
        "classify",
        "gate",
        "parallel",
    }:
        return None

    next_agent = (ticket.next_agent or "").strip()
    if not next_agent or stage.stage_type in {"parallel", "gate"}:
        return None

    if not get_agent(next_agent):
        return None

    if stage.classify_routes:
        return _resolve_next_agent_from_routes(ticket, stage)

    if stage.stage_type == "classify":
        return _resolve_next_agent_from_routes(ticket, stage)

    if stage.key in {"implementation", "route_impl", "implement"}:
        return next_agent, stage.skill_name or ""

    # A stage that names its own agent in the template keeps it. `next_agent` is a
    # sticky routing hint (specialist selection / reject-rework); on a standalone
    # stage start there is no advance/reconcile to refresh it, so it still holds
    # the *previous* stage's agent. Letting it override a fully-specified linear
    # stage silently ran the `learning` stage under `ac_gatekeeper` (run_43ea0c).
    # Only agentless/dynamic stages resolve their agent from the hint.
    if not stage.agent_id:
        return next_agent, stage.skill_name or ""

    return None


VERIFY_STAGE_TYPE = "verify"
PARALLEL_STAGE_TYPE = "parallel"

# The agent a verify stage runs when the template names none. Kept separate from
# the reviewers: a reviewer reads the change, a verifier has to exercise it.
DEFAULT_VERIFIER_AGENT = "verifier"


def is_parallel_stage(stage: WorkflowStageDef) -> bool:
    """Whether this stage fans out to ``parallel_agents`` instead of one agent.

    Every driver has to ask this before checking a stage out: a parallel stage
    has no single agent to resolve, so the members in ``stage.parallel_agents``
    are the unit of work. See ``services.parallel_stage``.
    """
    return stage.stage_type == PARALLEL_STAGE_TYPE


def is_agentless_stage(stage: WorkflowStageDef) -> bool:
    """Stages with no CLI agent (human gates, terminal markers)."""
    if stage.stage_type in {"classify", "gate", "parallel", VERIFY_STAGE_TYPE}:
        return False
    return not (stage.agent_id or "").strip()


def is_prunable_stage(stage: WorkflowStageDef) -> bool:
    """Whether a run may declare this stage won't-do while the workflow is live.

    The template stays static; what a run gets to decide is whether a stage the
    template already marked `optional` applies to *this* ticket. That is the
    whole of "pseudo-dynamic": pruning is offered per stage by the author, not
    claimed per run by the agent. A required stage is not prunable — otherwise
    an implementer could mark its own downstream review won't-do and the ticket
    would derive DONE, because `_derive_ticket_state` counts WONT_DO as
    resolved. To make a stage prunable, mark it optional in the template.

    The terminal stage is never prunable: pruning it removes the only stage that
    ends the workflow.
    """
    return bool(stage.optional) and not is_terminal_stage(stage)


def prunable_stage_keys(stages: list[WorkflowStageDef]) -> list[str]:
    """Keys of every prunable stage, in template order."""
    return [s.key for s in sorted(stages, key=lambda s: s.order) if is_prunable_stage(s)]


# Conditions a stage may declare via `skip_when`. Deliberately a closed, named
# vocabulary rather than an expression language: these are checked structurally
# against ticket fields, which the classify keyword matcher cannot express.
SKIP_CONDITIONS = ("has_description", "has_acceptance_criteria", "routed_as_light_work")


def took_light_route(ticket: Ticket, stages: list[WorkflowStageDef]) -> bool:
    """Whether triage routed this ticket down a shortened path.

    Read off the classify route that actually made the decision rather than
    re-testing the ticket text: the keywords live in the template and are
    editable in Studio, so a second copy in code would drift from the routing it
    is meant to describe. A route naming a `to_stage` is one that branches past
    stages, which is what "light" means here.
    """
    for stage in stages:
        if stage.stage_type != "classify":
            continue
        route = _select_classify_route(ticket, stage)
        if route is not None and (route.to_stage or "").strip():
            return True
    return False


def should_skip_stage(
    ticket: Ticket,
    stage: WorkflowStageDef,
    stages: list[WorkflowStageDef] | None = None,
) -> bool:
    """Whether `stage` declares a skip condition this ticket already satisfies.

    Motivating case: a ticket that arrived already scoped skips plan/spec rather
    than paying for work Ticket Studio has done.
    """
    condition = (stage.skip_when or "").strip()
    if not condition:
        return False
    if condition == "has_description":
        return bool((ticket.description or "").strip())
    if condition == "has_acceptance_criteria":
        try:
            criteria = json.loads(ticket.acceptance_criteria_json or "[]")
        except (TypeError, ValueError):
            return False
        return bool(criteria)
    if condition == "routed_as_light_work":
        return took_light_route(ticket, stages or [])
    return False


def resolve_scope_reroute_pin(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str] | None:
    """The scope-denial reroute pin, when it names a valid agent for this stage.

    Set only when a scoped implementer was denied a write onto a sibling's
    subtree (see ``agent_scope`` / ``permission_bridge``). It is *authoritative*:
    it outranks classify keyword-scoring so a frontend-keyword-heavy ticket that
    actually needs backend work reaches the backend implementer instead of being
    re-scored straight back to frontend. The pin is honored only on the stage
    whose route table (or static agent) actually offers the pinned agent, so a
    stale pin can never divert an unrelated later stage — and it is cleared the
    moment it is consumed at dispatch, so it steers exactly one re-run.
    """
    pinned = (ticket.scope_reroute_agent or "").strip()
    if not pinned or not get_agent(pinned):
        return None
    if stage.classify_routes:
        for route in stage.classify_routes:
            if route.agent_id == pinned:
                return pinned, route.skill_name or stage.skill_name
        # Fall through rather than giving up here: a classify stage can also name
        # a static `agent_id` that is not repeated as a route, and that agent can
        # still run the stage. Returning None on a route-table miss dropped a
        # valid pin for exactly that shape, sending the stage back to keyword
        # scoring — which is what pinned it to the wrong specialist to begin with.
    if (stage.agent_id or "").strip() == pinned:
        return pinned, stage.skill_name or ""
    return None


def resolve_display_agent(ticket: Ticket, stage: WorkflowStageDef) -> str:
    """The agent this ticket's `stage` would dispatch, for a reader to show.

    `ticket.next_agent` used to answer this, and nine readers asked it. It is a
    pin — written once, honoured where the stage offers it, cleared at dispatch
    (lg-workflow-integrity-441) — so it is empty for most of a ticket's life and
    reading it as a standing fact produces a plausible answer computed from
    nothing. Derive instead.

    Falls back to `stage.agent_id` where `resolve_stage_execution` answers an
    empty pair. That is not a guess: the three seed writes that populate
    `next_agent` (ticket_service, workflow_service, orchestration) all assign
    `stage.agent_id`, so this is the value the stored field has always carried.
    It matters for parallel stages, where the resolver deliberately returns ""
    because the members live in `parallel_agents` and only a driver can fan them
    out — a reader still wants the stage's declared agent to show.

    An agentless stage answers "" because its `agent_id` is empty, which is the
    honest answer. Callers that need "this stage runs no agent" as a decision
    rather than a display string want `is_agentless_stage`.

    This is for READERS. Dispatch resolves through `_resolve_run_agent`, which
    must keep raising on a stage that resolves no agent rather than showing one.
    """
    agent_id, _ = resolve_stage_execution(ticket, stage)
    if agent_id:
        return agent_id
    if is_parallel_stage(stage):
        # A parallel stage's agents live in `parallel_agents`; `stage.agent_id`
        # is whatever the stage carried before it was fanned out, and is not
        # kept in step. Three of the five parallel stages in the live templates
        # have it EMPTY, so falling through to it showed nothing for a stage
        # with two or three real lanes; a stage converted from single-agent
        # could equally leave behind an agent no lane will ever dispatch.
        # Answer with a member, so a displayed agent is always one that can run.
        members = [member.agent_id for member in stage.parallel_agents if member.agent_id]
        if members:
            return members[0]
    return stage.agent_id


def resolve_stage_execution(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str]:
    pinned = resolve_scope_reroute_pin(ticket, stage)
    if pinned:
        return pinned
    if stage.stage_type == "classify":
        return resolve_classify_route(ticket, stage)
    if stage.stage_type == "gate":
        return stage.agent_id or "gatekeeper", stage.skill_name or ""
    if is_parallel_stage(stage):
        # No single agent to resolve — the members live in `parallel_agents`,
        # and fanning them out is the driver's job (services.parallel_stage).
        return "", ""
    if stage.stage_type == VERIFY_STAGE_TYPE:
        return stage.agent_id or DEFAULT_VERIFIER_AGENT, stage.skill_name or ""
    routed = _resolve_next_agent_override(ticket, stage)
    if routed:
        return routed
    return stage.agent_id, stage.skill_name


def ticket_stage_definition(
    session: Session, ticket: Ticket, stage_key: str = ""
) -> WorkflowStageDef | None:
    """`ticket`'s definition of `stage_key`, defaulting to its current stage.

    `stage_key` is explicit for the callers that must not use the cursor: a
    queue entry is priced against the stage IT will run, which is not
    necessarily the stage the ticket is parked on now.
    """
    key = stage_key or ticket.workflow_stage_key
    if not key:
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return None
    return next((stage for stage in stages if stage.key == key), None)


def ticket_stage_agent(session: Session, ticket: Ticket, stage_key: str = "") -> str:
    """The agent `ticket` would dispatch for `stage_key`, or "" if none resolves.

    The seam every reader calls. An empty answer means "nothing to show" and
    callers must render it as a gap, not as an agent named "". A ticket with no
    workflow, no stage map, or a stage key its template does not define all
    answer "" — those are the cases the stored field used to paper over.
    """
    stage = ticket_stage_definition(session, ticket, stage_key)
    if stage is None:
        return ""
    return resolve_display_agent(ticket, stage)
