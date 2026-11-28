"""A person's requeue reason reaches the run it dispatches.

A requeue clears `blocking_issues`, the brief's fallback, and wrote its reason
only to a context artifact and the history rail, neither of which the brief
reads. lg-milestone-that-1424 was requeued on 2026-11-27 naming the one jest
test its landing failed; its implementer reported a zero diff, never told.
"""

from __future__ import annotations

from loregarden.agents.stage_context import turn_context_lines
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.requeue import requeue_stage
from tests.factories import make_agent_run
from tests.test_landing_conflict_resolver import (  # noqa: F401 - imported as pytest fixtures
    conflicted_fixture,
    milestone_fixture,
    repo_fixture,
    session_fixture,
    workspace_fixture,
)

REASON = "scrollRegionsMeasuredReachability fails: repoint TicketTimeline.tsx:455 to :464"


def _requeue(session, ticket):
    requeue_stage(
        session,
        OrchestrationService(session),
        ticket,
        stage_key="work",
        reason=REASON,
        actor="operator",
    )


def _run(session, ticket, code):
    return make_agent_run(
        session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code=code,
        stage_key="work",
        agent_id="frontend_implementer",
    )


def test_the_first_run_after_a_requeue_is_told_why(session, conflicted):
    _requeue(session, conflicted)
    run = _run(session, conflicted, "run-after-requeue")

    text = "\n".join(turn_context_lines(session, conflicted, run, None))

    assert "a person requeued this stage" in text
    assert REASON in text


def test_a_later_run_of_the_stage_is_not_told_an_old_reason(session, conflicted):
    _requeue(session, conflicted)
    _run(session, conflicted, "run-after-requeue")
    later = _run(session, conflicted, "run-after-that")

    text = "\n".join(turn_context_lines(session, conflicted, later, None))

    assert REASON not in text


def test_a_requeue_of_another_stage_is_not_this_stages_reason(session, conflicted):
    requeue_stage(
        session,
        OrchestrationService(session),
        conflicted,
        stage_key="done",
        reason=REASON,
        actor="operator",
    )
    run = _run(session, conflicted, "run-work")

    assert REASON not in "\n".join(turn_context_lines(session, conflicted, run, None))
