"""Readers derive the stage's agent instead of reading `ticket.next_agent`.

Every test here sets `next_agent = ""` before asserting, because that is the
state lg-workflow-integrity-441 is about to make normal: the pin is written
once, honoured where the stage offers it, and cleared at dispatch. Nine readers
treated it as a standing fact, so an empty pin did not raise — it produced a
plausible answer computed from nothing. The queue cost estimate priced an agent
of `""` and returned a number.

The parallel case is the discriminating one. `resolve_stage_execution` returns
an empty pair for a parallel stage by design, so a reader that used it directly
would answer "" where the stored field answered an agent. That is why
`resolve_display_agent` falls back to `stage.agent_id` — the same value the
three seed writes assign — and why "no behaviour change while the field is
populated" is true rather than hoped for.
"""

from loregarden.models.domain import (
    ClassifyRoute,
    ParallelAgentSpec,
    Ticket,
    TicketState,
    WorkflowStageDef,
    WorkItemType,
)
from loregarden.services.studio_routing import resolve_display_agent

BUILD = {
    "workspace_id": "ws",
    "title": "t",
    "state": TicketState.IN_PROGRESS,
    "work_item_type": WorkItemType.TASK,
}


def _ticket(**kwargs) -> Ticket:
    """A ticket with the pin EMPTY — the state every reader must survive."""
    return Ticket(external_id="derived", next_agent="", **{**BUILD, **kwargs})


def test_a_single_agent_stage_answers_its_declared_agent():
    stage = WorkflowStageDef(key="spec", name="Spec", agent_id="spec", order=1)
    assert resolve_display_agent(_ticket(), stage) == "spec"


def test_a_parallel_stage_answers_its_declared_agent_not_an_empty_string():
    """The case a naive `resolve_stage_execution` swap gets wrong.

    The resolver returns ("", "") for a parallel stage on purpose — its members
    live in `parallel_agents` and only a driver can fan them out. A reader still
    has something to show, and what it showed before was `stage.agent_id`,
    because that is what the seed writes stored.
    """
    stage = WorkflowStageDef(
        key="review",
        name="Review",
        agent_id="architecture_reviewer",
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id="architecture_reviewer"),
            ParallelAgentSpec(agent_id="static_qa"),
        ],
        order=1,
    )
    assert resolve_display_agent(_ticket(), stage) == "architecture_reviewer"


def test_an_agentless_stage_answers_nothing_rather_than_inventing_one():
    stage = WorkflowStageDef(key="done", name="Done", agent_id="", order=1)
    assert resolve_display_agent(_ticket(), stage) == ""


def test_a_gate_stage_answers_the_gatekeeper_default():
    stage = WorkflowStageDef(key="gate", name="Gate", agent_id="", stage_type="gate", order=1)
    assert resolve_display_agent(_ticket(), stage) == "gatekeeper"


def test_a_classify_stage_scores_its_routes_and_uses_the_pin_only_to_break_ties():
    """What classify routing actually does, which is neither thing the UI said.

    `resolve_stage_execution` returns at the classify branch, so it looks from
    there as though `next_agent` is never consulted. It is — one level down, in
    `_select_classify_route`: content classification wins whenever the ticket's
    text gives a real signal (`best_score > 0`), and only when the text is
    ambiguous does a pin naming one of the routes break the tie. That ordering
    is deliberate and is documented against loregarden #164, where a stale pin
    permanently overrode tickets that had since been reclassified.

    Scoring is on `specialties` and `languages` only. A route declaring
    neither is always eligible and scores zero; a route declaring a specialty
    that misses is ineligible entirely. So "ambiguous" resolves to the
    undeclared route, not to the first one. A classify stage with routes always
    returns one of them, so `stage.agent_id` answers only for a stage with no
    routes at all. The haystack is title plus acceptance criteria; the
    description is deliberately excluded.
    """
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        agent_id="unused_when_routes_exist",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(agent_id="frontend_implementer", specialties=["refactor"], to_stage=""),
            ClassifyRoute(agent_id="backend_implementer", to_stage=""),
        ],
        order=1,
    )

    # A real content signal wins outright, with the pin empty.
    assert resolve_display_agent(_ticket(title="Rename the parser module"), stage) == (
        "frontend_implementer"
    )

    # Ambiguous text and no pin. The specialty-declaring route is not merely
    # outscored, it is INELIGIBLE — `_route_match_score` returns None when a
    # route declares specialties and none of them hit — so the route declaring
    # nothing wins with a score of zero. Traced rather than assumed:
    #   'Rename the parser module' -> [(frontend, 1), (backend, 0)] -> frontend
    #   'Add a login page'         -> [(frontend, None), (backend, 0)] -> backend
    assert resolve_display_agent(_ticket(title="Add a login page"), stage) == (
        "backend_implementer"
    )


def test_a_classify_stage_with_no_routes_answers_its_declared_agent():
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        agent_id="backend_implementer",
        stage_type="classify",
        order=1,
    )
    assert resolve_display_agent(_ticket(), stage) == "backend_implementer"


def test_a_scope_reroute_pin_wins_over_the_stage(db_session):
    """`resolve_scope_reroute_pin` validates the agent through the registry,
    which is database-backed — hence the session.
    """
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        agent_id="backend_implementer",
        stage_type="classify",
        classify_routes=[ClassifyRoute(agent_id="frontend_implementer", to_stage="")],
        order=1,
    )
    ticket = _ticket()
    ticket.scope_reroute_agent = "frontend_implementer"
    assert resolve_display_agent(ticket, stage) == "frontend_implementer"


def test_the_derived_answer_matches_what_the_seed_writes_stored():
    """AC: no behaviour change while `next_agent` is still populated.

    The three seed writes (ticket_service, workflow_service, orchestration) all
    assign `first_stage.agent_id`. If the derived answer disagreed with that for
    an ordinary first stage, this change would silently alter what every reader
    displays for every ticket that has not moved yet.
    """
    for stage_type in ("agent", "parallel"):
        stage = WorkflowStageDef(
            key="plan", name="Plan", agent_id="planner", stage_type=stage_type, order=1
        )
        seeded = stage.agent_id
        assert resolve_display_agent(_ticket(), stage) == seeded
