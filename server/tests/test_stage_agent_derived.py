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
from loregarden.services.studio_routing import (
    resolve_classify_branch,
    resolve_display_agent,
)

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


def test_a_parallel_stage_with_no_declared_agent_answers_a_lane_member():
    """The live shape, and the one the `stage.agent_id` fallback got wrong.

    Three of the five parallel stages in the live templates carry an EMPTY
    `agent_id` — extended-tdd/review, blobert-tdd/script_review and
    studio-blobert-ddd/script_review — because a stage's agents move into
    `parallel_agents` when it is fanned out and the old field is not kept in
    step. Falling through to `stage.agent_id` therefore showed nothing at all
    for a stage with two or three real reviewers.

    The same fallback fails the other way for a stage converted from
    single-agent whose old `agent_id` is left behind: it would name an agent no
    lane will ever dispatch. Answering with a member is right in both
    directions — whatever is displayed is an agent that can actually run.
    """
    stage = WorkflowStageDef(
        key="script_review",
        name="Script Review",
        agent_id="",
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id="gdscript_reviewer"),
            ParallelAgentSpec(agent_id="static_qa"),
        ],
        order=1,
    )
    assert resolve_display_agent(_ticket(), stage) == "gdscript_reviewer"


def test_a_parallel_stage_never_reports_an_agent_that_is_not_a_lane():
    """A stale `agent_id` left behind by a single-agent-to-parallel conversion."""
    stage = WorkflowStageDef(
        key="review",
        name="Review",
        agent_id="backend_implementer",  # predates the fan-out, runs nothing here
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id="architecture_reviewer"),
            ParallelAgentSpec(agent_id="static_qa"),
        ],
        order=1,
    )
    reported = resolve_display_agent(_ticket(), stage)
    assert reported == "architecture_reviewer"
    assert reported in {member.agent_id for member in stage.parallel_agents}


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


# -- the pin steers only when it names one route (D) ---------------------------


def test_a_pin_matching_several_routes_does_not_choose_the_branch(db_session):
    """The 471-ticket misroute, in miniature.

    Triage names `ticket_scoper` on both routes: one jumps to `test-design` for
    typo/docs work, one runs the full pipeline and is the declared default. The
    pin says which AGENT, and an agent does not identify a route — so matching on
    it returned whichever came first and every pinned ticket took the docs
    shortcut, whatever it actually was.

    Measured over 488 live non-terminal tickets on a classify stage, this branch
    decided 471 of them and overrode the declared default in all 471.
    """
    stage = WorkflowStageDef(
        key="triage",
        name="Triage",
        agent_id="ticket_scoper",
        stage_type="classify",
        order=1,
        classify_routes=[
            ClassifyRoute(agent_id="ticket_scoper", specialties=["docs"], to_stage="test-design"),
            ClassifyRoute(agent_id="ticket_scoper", default=True, to_stage=""),
        ],
    )
    pinned = Ticket(
        external_id="pinned",
        workspace_id="ws",
        title="Bootstrap vertical slice",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        next_agent="ticket_scoper",
    )

    # No content signal and an ambiguous pin: the declared default answers.
    assert resolve_classify_branch(pinned, stage) == ""

    # A real docs ticket still takes the shortcut, because content wins outright.
    docs = Ticket(
        external_id="docs",
        workspace_id="ws",
        title="Fix the docs typo",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        next_agent="ticket_scoper",
    )
    assert resolve_classify_branch(docs, stage) == "test-design"


def test_a_pin_naming_one_route_still_steers_the_rework(db_session):
    """What dropping the tie-breaker would have broken.

    A reject writes the agent it wants back on the work. At a stage whose routes
    name distinct agents the pin is unambiguous, and it must still decide —
    otherwise rework returns to whichever specialist the content happens to pick,
    which is the one that just failed.
    """
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        agent_id="backend_implementer",
        stage_type="classify",
        order=8,
        classify_routes=[
            ClassifyRoute(agent_id="frontend_implementer", specialties=["ui"], to_stage=""),
            ClassifyRoute(agent_id="backend_implementer", default=True, to_stage=""),
        ],
    )
    ticket = Ticket(
        external_id="rework",
        workspace_id="ws",
        title="Tidy the panel spacing",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        next_agent="frontend_implementer",
    )

    assert resolve_display_agent(ticket, stage) == "frontend_implementer"
