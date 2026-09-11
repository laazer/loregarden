"""A classify stage whose roster owns none of the ticket's work must say so.

`blob-procedural-sdf-25` — "FastAPI CRUD routes for jailed creature definitions",
whose acceptance criteria say "backend" eight times — routed to
`implementation_frontend` on a score of 1: the word "layout", from AC-02's "the
route-first backend layout". The frontend agent read the criteria, correctly
concluded no frontend code was required, committed nothing and exited
`succeeded`; the transition gate then failed the stage for producing no change.
Five identical runs (`run_d186a8` … `run_cba3fc`).

Two defects, both covered here: one incidental synonym outvoting the route the
template declared `default`, and "no route owns this" being indistinguishable
from "this route won".
"""

import pytest
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef
from loregarden.models.domain.enums import ClassifyBasis
from loregarden.services.studio_routing import (
    classify_decision,
    resolve_classify_route,
    unrouteable_classify_detail,
)

GODOT_ROSTER = [
    ClassifyRoute(specialties=["simulation", "core", "deterministic"], agent_id="core_simulation"),
    ClassifyRoute(specialties=["gameplay", "mechanic", "combat"], agent_id="gameplay_systems"),
    ClassifyRoute(specialties=["presentation", "ui", "hud", "audio"], agent_id="presentation"),
    ClassifyRoute(specialties=["engine", "godot", "scene"], agent_id="engine_integration"),
    ClassifyRoute(
        specialties=["frontend", "web", "editor", "asset"],
        languages=["typescript", "javascript"],
        agent_id="implementation_frontend",
    ),
    ClassifyRoute(agent_id="core_simulation", default=True),
]


def _stage(routes: list[ClassifyRoute], key: str = "implement") -> WorkflowStageDef:
    return WorkflowStageDef(
        key=key, name=key, stage_type="classify", agent_id="core_simulation", classify_routes=routes
    )


def _ticket(title: str, criteria: list[str], *, next_agent: str = "") -> Ticket:
    import json

    return Ticket(
        external_id="blob-procedural-sdf-25",
        workspace_id="ws",
        title=title,
        acceptance_criteria_json=json.dumps(criteria),
        next_agent=next_agent,
    )


BACKEND_TICKET_CRITERIA = [
    "AC-01: The backend exposes only the in-scope CRUD routes for this ticket: "
    "GET /api/creatures, PUT /api/creatures/{id}, DELETE /api/creatures/{id}.",
    "AC-02: The routes are implemented in the route-first backend layout with a "
    "creatures router registered in main.py and a route-owned Pydantic schema.",
    "AC-03: The api endpoint is backed by the CreatureStore public API.",
]


def test_backend_ticket_on_a_godot_roster_is_unrouteable():
    decision = classify_decision(
        _ticket("FastAPI CRUD routes for jailed creature definitions", BACKEND_TICKET_CRITERIA),
        _stage(GODOT_ROSTER),
    )

    assert decision is not None
    assert decision.basis is ClassifyBasis.UNROUTEABLE
    assert decision.route is None
    assert "backend" in decision.detail
    assert "implement" in decision.detail


def test_unrouteable_stage_resolves_no_agent_rather_than_the_least_wrong_one():
    ticket = _ticket("FastAPI CRUD routes for jailed creature definitions", BACKEND_TICKET_CRITERIA)

    assert resolve_classify_route(ticket, _stage(GODOT_ROSTER)) == ("", "")
    assert "backend" in unrouteable_classify_detail(ticket, _stage(GODOT_ROSTER))


def test_a_stale_pin_cannot_revive_an_unrouteable_ticket():
    """The pin is the previous misroute replaying itself — five runs' worth."""
    ticket = _ticket(
        "FastAPI CRUD routes for jailed creature definitions",
        BACKEND_TICKET_CRITERIA,
        next_agent="implementation_frontend",
    )

    decision = classify_decision(ticket, _stage(GODOT_ROSTER))

    assert decision is not None
    assert decision.basis is ClassifyBasis.UNROUTEABLE


def test_one_synonym_does_not_outvote_a_declared_default():
    """ "layout" is a frontend synonym and also how backend code is organised."""
    routes = [
        ClassifyRoute(specialties=["frontend"], agent_id="frontend_implementer"),
        ClassifyRoute(specialties=["backend"], agent_id="backend_implementer", default=True),
    ]

    decision = classify_decision(
        _ticket("Route-first backend layout", ["AC-01: Keep the backend layout route-first."]),
        _stage(routes),
    )

    assert decision is not None
    assert decision.route is not None
    assert decision.route.agent_id == "backend_implementer"


def test_a_declared_specialty_still_wins_outright():
    routes = [
        ClassifyRoute(specialties=["frontend"], agent_id="frontend_implementer"),
        ClassifyRoute(specialties=["backend"], agent_id="backend_implementer", default=True),
    ]

    decision = classify_decision(
        _ticket("Frontend modal for the queue board", ["AC-01: Add the frontend dialog."]),
        _stage(routes),
    )

    assert decision is not None
    assert decision.basis is ClassifyBasis.CONTENT
    assert decision.route is not None
    assert decision.route.agent_id == "frontend_implementer"


def test_a_stage_with_no_default_still_takes_its_best_match():
    """One hit is the best evidence available where nothing is declared default."""
    routes = [
        ClassifyRoute(specialties=["frontend"], agent_id="frontend_implementer"),
        ClassifyRoute(specialties=["gameplay"], agent_id="gameplay_systems"),
    ]

    decision = classify_decision(
        _ticket("Tune the combat layout", ["AC-01: Adjust the layout."]), _stage(routes)
    )

    assert decision is not None
    assert decision.route is not None
    assert decision.route.agent_id == "frontend_implementer"


@pytest.mark.parametrize("title", ["Rename the queue service", "Add a creature list endpoint"])
def test_a_size_routed_stage_is_never_unrouteable(title: str):
    """`triage` splits on ticket size, not domain, and both routes name one agent.

    Measured against the live database: without this, 217 open tickets were
    called unrouteable for wanting a backend specialist from a stage that has
    never dispatched one.
    """
    routes = [
        ClassifyRoute(specialties=["typo", "docs", "changelog"], agent_id="ticket_scoper"),
        ClassifyRoute(agent_id="ticket_scoper", default=True),
    ]

    decision = classify_decision(
        _ticket(title, ["AC-01: api endpoint schema service."]), _stage(routes, key="triage")
    )

    assert decision is not None
    assert decision.basis is not ClassifyBasis.UNROUTEABLE
    assert decision.route is not None


def test_a_default_agent_named_for_the_specialty_counts_as_owning_it():
    """`spike`'s default is `backend_implementer` and declares no specialties."""
    routes = [
        ClassifyRoute(specialties=["godot", "engine"], agent_id="engine_integration"),
        ClassifyRoute(specialties=["frontend"], agent_id="frontend_implementer"),
        ClassifyRoute(agent_id="backend_implementer", default=True),
    ]

    decision = classify_decision(
        _ticket("Creature CRUD", BACKEND_TICKET_CRITERIA), _stage(routes, key="experiment")
    )

    assert decision is not None
    assert decision.basis is ClassifyBasis.DEFAULT
    assert decision.route is not None
    assert decision.route.agent_id == "backend_implementer"


def test_a_single_passing_mention_is_not_a_specialty():
    """One word in a title is a mention, not a domain.

    "Needs a server change" would have refused a dispatch the roster may well
    have been able to serve. Refusing is the stronger claim and takes real
    evidence — see `_UNROUTEABLE_MIN_HITS`.
    """
    decision = classify_decision(_ticket("Needs a server change", []), _stage(GODOT_ROSTER))

    assert decision is not None
    assert decision.basis is not ClassifyBasis.UNROUTEABLE
    assert decision.route is not None
    assert decision.route.agent_id == "core_simulation"


def test_a_curated_synonym_is_as_good_as_the_word_itself():
    """Nobody titles a refactor "Refactor…".

    `refactor`'s synonyms were curated to exclude every generic structural verb,
    so a hit on one is real evidence — unlike a hit on the broad frontend and
    backend lists, which exist because tickets say "button", not "frontend".
    Treating both as weak sent "Extract the retry loop out of the orchestration
    service" to the default route and dropped the refactor skill with it.
    """
    routes = [
        ClassifyRoute(
            specialties=["refactor"], agent_id="backend_implementer", skill_name="refactor"
        ),
        ClassifyRoute(specialties=["backend"], agent_id="backend_implementer", default=True),
    ]

    decision = classify_decision(
        _ticket("Extract the retry loop out of the orchestration service", []), _stage(routes)
    )

    assert decision is not None
    assert decision.basis is ClassifyBasis.CONTENT
    assert decision.route is not None
    assert decision.route.skill_name == "refactor"
