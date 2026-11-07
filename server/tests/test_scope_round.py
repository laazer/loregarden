"""A ticket's spec decides every finding filed against it before the ticket finishes.

On lg-milestone-that-1216 the reviewer who noticed a finding decided its fate
by default, by filing it, and the only outcome a filing had was a new ticket.
The scope round gives that decision to the spec, which says what the ticket's
work is: amend it into the criteria, make it the parent's follow-up, defer it,
or drop it.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    FindingDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.discovered_work import undecided
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.scope_round import (
    MAX_AMEND_ROUNDS,
    ScopeRoundStuck,
    decide_finding,
    open_round,
    open_round_or_defer,
    round_due,
)
from loregarden.services.seed import seed_database
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    set_stage_status,
)
from sqlmodel import Session
from tests.factories import make_workspace_ticket


def _run(session: Session, ticket: Ticket, agent_id: str, code: str) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key="spec" if agent_id == "spec" else "review",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _file(session: Session, run: AgentRun, title: str, *, parent: Ticket) -> Ticket:
    created = json.loads(
        execute_tool(
            session,
            "loregarden_create_ticket",
            normalize_tool_arguments(
                "loregarden_create_ticket",
                {
                    "workspace_slug": "loregarden",
                    "title": title,
                    "work_item_type": "bug",
                    "parent": parent.id,
                },
            ),
            orchestrated=True,
            run_id=run.id,
        )
    )
    return session.get(Ticket, created["id"])


def _decide(session: Session, run: AgentRun | None, finding: Ticket, decision: str, **extra):
    """Decide through the tool, as ``run`` (orchestrated) or as an operator (None)."""
    return json.loads(
        execute_tool(
            session,
            "loregarden_decide_finding",
            normalize_tool_arguments(
                "loregarden_decide_finding",
                {"ticket_id": finding.id, "decision": decision, "reason": "because", **extra},
            ),
            orchestrated=run is not None,
            run_id=run.id if run else "",
        )
    )


@pytest.fixture(name="source")
def source_fixture(db_session: Session) -> Ticket:
    """A feature whose every stage but the terminal one is done: it is about to finish."""
    seed_database(db_session)
    milestone = make_workspace_ticket(db_session, "sr-milestone")
    milestone.work_item_type = WorkItemType.MILESTONE
    source = make_workspace_ticket(db_session, "sr-source")
    source.work_item_type = WorkItemType.FEATURE
    source.parent_ticket_id = milestone.id
    db_session.add_all([milestone, source])
    db_session.commit()
    OrchestrationService(db_session).ensure_workflow_instance(source, commit=True)
    instance = workflow_instance_for(db_session, source.id)
    _, stages = resolve_ticket_stages(db_session, source)
    for stage in stages[:-1]:
        set_stage_status(source, instance, stages, stage.key, StageStatus.DONE)
    db_session.add_all([source, instance])
    db_session.commit()
    return source


def _stages(session: Session, ticket: Ticket):
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    return instance, stages


def test_a_round_is_due_only_before_finishing_with_findings_undecided(db_session, source):
    instance, stages = _stages(db_session, source)
    terminal, review = stages[-1], next(s for s in stages if s.key == "review")
    assert not round_due(db_session, source, stages, terminal)

    finding = _file(
        db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source
    )
    assert round_due(db_session, source, stages, terminal)
    assert not round_due(db_session, source, stages, review), "not a gate, not terminal"

    _decide(db_session, None, finding, "defer")
    assert not round_due(db_session, source, stages, terminal)


def test_a_round_reopens_the_spec_stage_and_nothing_else(db_session, source):
    _file(db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source)
    instance, stages = _stages(db_session, source)

    open_round(db_session, source, instance, stages, stages[-1])
    db_session.commit()

    stage_map = parse_stage_map(instance, stages)
    assert next_executable_stage(stages, stage_map) == "spec"
    assert [k for k, v in stage_map.items() if v is StageStatus.PENDING] == ["spec", stages[-1].key]


def test_a_round_that_amends_nothing_goes_straight_back_to_finishing(db_session, source):
    finding = _file(
        db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source
    )
    instance, stages = _stages(db_session, source)
    open_round(db_session, source, instance, stages, stages[-1])
    db_session.commit()

    _decide(db_session, _run(db_session, source, "spec", "s1"), finding, "follow_up")
    set_stage_status(source, instance, stages, "spec", StageStatus.DONE)
    db_session.commit()

    assert next_executable_stage(stages, parse_stage_map(instance, stages)) == stages[-1].key


def test_an_amend_holds_the_ticket_to_it_and_reruns_everything_after_spec(db_session, source):
    finding = _file(
        db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source
    )

    _decide(
        db_session,
        _run(db_session, source, "spec", "s1"),
        finding,
        "amend",
        criterion="The no-layout arm reads width",
    )

    db_session.refresh(source)
    db_session.refresh(finding)
    assert load_criteria(source.acceptance_criteria_json)[-1] == "The no-layout arm reads width"
    assert finding.state is TicketState.WONT_DO and source.external_id in finding.blocking_issues
    instance, stages = _stages(db_session, source)
    stage_map = parse_stage_map(instance, stages)
    after_spec = [
        s.key for s in stages if s.order > next(x for x in stages if x.key == "spec").order
    ]
    assert all(stage_map[key] is StageStatus.PENDING for key in after_spec)


def test_only_the_source_tickets_spec_may_decide(db_session, source):
    reviewer = _run(db_session, source, "architecture_reviewer", "r1")
    finding = _file(db_session, reviewer, "F", parent=source)
    other = make_workspace_ticket(db_session, "sr-other")

    with pytest.raises(ValueError, match="Only the spec stage"):
        _decide(db_session, reviewer, finding, "defer")
    with pytest.raises(ValueError, match="Only the spec stage"):
        _decide(db_session, _run(db_session, other, "spec", "s-other"), finding, "defer")
    assert (
        _decide(db_session, _run(db_session, source, "spec", "s1"), finding, "defer")["decision"]
        == "defer"
    )


def test_a_ticket_amends_its_scope_in_at_most_its_rounds(db_session, source):
    reviewer = _run(db_session, source, "architecture_reviewer", "r1")
    findings = [_file(db_session, reviewer, f"F{i}", parent=source) for i in range(3)]
    second_reviewer = _run(db_session, source, "security_reviewer", "r2")
    findings += [_file(db_session, second_reviewer, f"G{i}", parent=source) for i in range(2)]
    spec_runs = [_run(db_session, source, "spec", f"s{i}") for i in range(MAX_AMEND_ROUNDS + 1)]

    for index, run in enumerate(spec_runs[:MAX_AMEND_ROUNDS]):
        _decide(db_session, run, findings[index], "amend", criterion=f"criterion {index}")
    # The same round may amend more than once.
    _decide(db_session, spec_runs[0], findings[2], "amend", criterion="criterion again")

    with pytest.raises(ValueError, match="amended its scope"):
        _decide(db_session, spec_runs[-1], findings[3], "amend", criterion="one round too many")
    assert _decide(db_session, spec_runs[-1], findings[3], "follow_up")["decision"] == "follow_up"


def test_a_round_that_decided_nothing_is_not_asked_again(db_session, source):
    _file(db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source)
    instance, stages = _stages(db_session, source)
    open_round(db_session, source, instance, stages, stages[-1])
    db_session.commit()

    with pytest.raises(ScopeRoundStuck, match="decided\\s+none"):
        open_round(db_session, source, instance, stages, stages[-1])


def test_every_decision_carries_a_reason(db_session, source):
    finding = _file(
        db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source
    )
    with pytest.raises(ValueError, match="reason is required"):
        decide_finding(db_session, finding, FindingDecision.DROP, reason=" ", decided_by="operator")


def test_a_finding_amended_from_under_its_source_does_not_finish_the_source(db_session, source):
    """The finding was filed as the source's child; closing it into a criterion is not work done."""
    finding = _file(
        db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source
    )
    _decide(db_session, _run(db_session, source, "spec", "s1"), finding, "amend", criterion="c")
    db_session.refresh(source)
    assert source.state is not TicketState.DONE


def test_the_spec_records_what_it_rules_out_and_every_reader_sees_it(db_session, source):
    ruled_out = ["The no-layout arm — this ticket covers laid-out regions only"]
    execute_tool(
        db_session,
        "loregarden_update_ticket",
        normalize_tool_arguments(
            "loregarden_update_ticket", {"ticket_id": source.id, "out_of_scope": ruled_out}
        ),
    )
    read = json.loads(
        execute_tool(
            db_session,
            "loregarden_get_ticket",
            normalize_tool_arguments("loregarden_get_ticket", {"ticket_id": source.id}),
        )
    )
    assert read["out_of_scope"] == ruled_out


def test_findings_a_spec_will_not_decide_are_deferred_not_blocked(db_session, source):
    """The second identical round defers the findings and the ticket goes on; no person is asked."""
    finding = _file(
        db_session, _run(db_session, source, "architecture_reviewer", "r1"), "F", parent=source
    )
    instance, stages = _stages(db_session, source)
    open_round(db_session, source, instance, stages, stages[-1])
    set_stage_status(source, instance, stages, "spec", StageStatus.DONE)  # spec decided nothing
    db_session.commit()

    open_round_or_defer(db_session, source, stages[-1])

    db_session.refresh(source)
    assert source.state is not TicketState.BLOCKED
    assert undecided(db_session, finding.id) is None
    assert not round_due(db_session, source, stages, stages[-1])
