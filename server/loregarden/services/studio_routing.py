"""Classify-stage routing: pick the agent (and, with U5b, the branch) for a ticket."""

from __future__ import annotations

import json
import re

from loregarden.agents.registry import get_agent
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef

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
    acceptance_criteria = ""
    try:
        acceptance_criteria = " ".join(json.loads(ticket.acceptance_criteria_json or "[]"))
    except (TypeError, ValueError):
        pass

    return " ".join(
        [
            ticket.title or "",
            ticket.description or "",
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

    next_agent = (ticket.next_agent or "").strip()
    if next_agent and get_agent(next_agent):
        for route in stage.classify_routes:
            if route.agent_id == next_agent:
                return route

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
        return next_agent, stage.skill_name or "apply_patch"

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

# The agent a verify stage runs when the template names none. Kept separate from
# the reviewers: a reviewer reads the change, a verifier has to exercise it.
DEFAULT_VERIFIER_AGENT = "verifier"


def is_agentless_stage(stage: WorkflowStageDef) -> bool:
    """Stages with no CLI agent (human gates, terminal markers)."""
    if stage.stage_type in {"classify", "gate", "parallel", VERIFY_STAGE_TYPE}:
        return False
    return not (stage.agent_id or "").strip()


TERMINAL_STAGE_KEY = "done"


def is_terminal_stage(stage: WorkflowStageDef) -> bool:
    """Whether reaching this stage ends the workflow.

    The `terminal` flag is authoritative; `key == "done"` remains a fallback so
    templates authored before the flag — including version-pinned instances —
    keep terminating.
    """
    return bool(stage.terminal) or stage.key == TERMINAL_STAGE_KEY


def find_terminal_stage(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """First stage by order that ends the workflow, or None."""
    for stage in sorted(stages, key=lambda s: s.order):
        if is_terminal_stage(stage):
            return stage
    return None


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


def resolve_stage_execution(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str]:
    if stage.stage_type == "classify":
        return resolve_classify_route(ticket, stage)
    if stage.stage_type == "gate":
        return stage.agent_id or "gatekeeper", stage.skill_name or "ac_gate"
    if stage.stage_type == "parallel":
        return "", ""
    if stage.stage_type == VERIFY_STAGE_TYPE:
        return stage.agent_id or DEFAULT_VERIFIER_AGENT, stage.skill_name or "verify"
    routed = _resolve_next_agent_override(ticket, stage)
    if routed:
        return routed
    return stage.agent_id, stage.skill_name
