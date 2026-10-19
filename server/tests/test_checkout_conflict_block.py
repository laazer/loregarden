"""A ticket whose branch cannot take its target is a conflict, and the merge turn is its repair.

lg-external-harness-1026, 2026-10-16: test-break could not start because the
branch conflicted with `integration/lg-external-harness-463` in three test
files. The checkout ran the agent's merge turn, which timed out; the run's
failure was then classified `work`, so the stage's one repair turn was spent
re-running the stage into the same checkout (which ran the merge turn again,
and timed out again). With the merge budget spent the block was final, but
nothing said so: the lane and the autopilot still read it as repairable.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentRun,
    BlockKind,
    RepairRoute,
    RunStatus,
    StageStatus,
    TicketState,
    Workspace,
)
from loregarden.services.autopilot_retry import retry_reason
from loregarden.services.block_classification import classify_block_message
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.integration_merge_budget import INTEGRATION_MERGE_STAGE
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.queue_repair import resolve_repair_route
from loregarden.services.ticket_merge_turn import ticket_merge_turn_refusal
from sqlmodel import Session
from tests.history_helpers import decision_kinds, decision_payloads
from tests.test_block_repair import (  # noqa: F401 - fixtures are used by name
    WORKER,
    _report,
    _script,
    ticket_fixture,
)
from tests.worktree_helpers import commit_on, git, make_repo, make_ticket

#: The message 1026's run failed with, as the CLI executor wrote it.
CHECKOUT_CONFLICT = (
    "Failed to checkout branch: Branch 'loregarden/lg-external-harness-1026' could not take "
    "'integration/lg-external-harness-463' before starting: Auto-merging "
    "server/tests/test_chat_branch_sweep.py\nCONFLICT (content): Merge conflict in "
    "server/tests/test_chat_branch_sweep.py\nAutomatic merge failed; fix conflicts and then "
    "commit the result.\nconflicts in server/tests/test_chat_branch_sweep.py; the merge turn "
    "failed (Integration merge timed out after 900s)"
)

#: What the merge turn's own run records when it fails: its budget counts these
#: by kind, so they must not start reading as conflicts.
MERGE_TURN_FAILURE = (
    "the merge turn failed (Integration merge timed out after 900s); the merge turn left "
    "conflicts unresolved in: server/tests/test_chat_branch_sweep.py"
)


# ---- classification -------------------------------------------------------


def test_a_checkout_that_cannot_take_its_target_is_a_conflict():
    assert classify_block_message(CHECKOUT_CONFLICT) is BlockKind.CONFLICT


def test_the_merge_turns_own_failure_is_still_classified_as_before():
    assert classify_block_message(MERGE_TURN_FAILURE) is BlockKind.WORK


def test_a_checkout_that_fails_for_another_reason_is_not_a_conflict():
    message = (
        "Failed to checkout branch: Branch 'b' could not take 'main' before starting: "
        "cannot read 'b' and 'main'"
    )
    assert classify_block_message(message) is not BlockKind.CONFLICT


# ---- no repair turn, through the real orchestrator --------------------------


def test_a_checkout_conflict_does_not_spend_the_repair_turn(db_session, ticket, monkeypatch):
    dispatched = _script(monkeypatch, {WORKER: (RunStatus.FAILED, "", CHECKOUT_CONFLICT)})

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="conflict"), max_stages=10
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER], "no repair run into the same checkout"
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.CONFLICT
    assert "dispatched_repair" not in decision_kinds(db_session, ticket.id)
    escalated = [
        p for p in decision_payloads(db_session, ticket.id) if p["decision"] == "repair_escalated"
    ]
    assert escalated and "merge turn" in escalated[0]["reason"]


def test_a_conflict_an_agent_declares_says_the_merge_turn_is_its_repair(
    db_session, ticket, monkeypatch
):
    """Declared rather than read from a failed checkout, it still gets no repair
    turn, and the history says what will clear it instead of "a person reads it"."""
    dispatched = _script(
        monkeypatch, {WORKER: (RunStatus.SUCCEEDED, _report("blocked", kind="conflict"), "")}
    )

    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="conflict"), max_stages=10
    )

    db_session.refresh(ticket)
    assert dispatched == [WORKER]
    assert ticket.block_kind is BlockKind.CONFLICT
    escalated = [
        p for p in decision_payloads(db_session, ticket.id) if p["decision"] == "repair_escalated"
    ]
    assert escalated and "merge turn" in escalated[0]["reason"]


# ---- the route: a merge turn while one may run ------------------------------


@pytest.fixture(name="conflicted")
def conflicted_fixture(db_session, ticket):
    ticket.state = TicketState.BLOCKED
    ticket.block_kind = BlockKind.CONFLICT
    ticket.workflow_stage_status = StageStatus.BLOCKED
    ticket.blocking_issues = CHECKOUT_CONFLICT
    db_session.add(ticket)
    db_session.commit()
    return ticket


def test_a_conflict_is_routed_to_a_merge_turn_while_one_may_run(db_session, conflicted):
    with patch("loregarden.services.queue_repair.ticket_merge_turn_refusal", return_value=""):
        assert resolve_repair_route(db_session, conflicted) is RepairRoute.MERGE_TURN
        assert retry_reason(db_session, conflicted) == (
            "its branch conflict can still get a merge turn"
        )


def test_a_conflict_whose_merge_turns_are_spent_has_no_route(db_session, conflicted):
    """Its stage run failed with budget left, which alone reads as a stage retry;
    but every re-dispatch would refuse at the same checkout."""
    spent = "2 automatic merge attempt(s) on abc were refused (max_conflict_resolve_attempts=2)"
    with patch("loregarden.services.queue_repair.ticket_merge_turn_refusal", return_value=spent):
        assert resolve_repair_route(db_session, conflicted) is None
        assert retry_reason(db_session, conflicted) is None


# ---- asking the budget about the ticket's own branch ------------------------


@pytest.fixture(name="branch_ticket")
def branch_ticket_fixture(isolated_db, tmp_path):
    with Session(isolated_db) as session:
        repo = make_repo(tmp_path)
        workspace = Workspace(slug="proj", name="proj", repo_path=str(repo))
        session.add(workspace)
        session.commit()
        ticket = make_ticket(session, workspace)
        git(repo, "branch", ticket.branch, "main")
        tip = commit_on(repo, ticket.branch, "shared.txt", "ticket side\n")
        yield session, ticket, tip


def test_a_fresh_branch_tip_may_get_a_merge_turn(branch_ticket):
    session, ticket, _ = branch_ticket
    assert ticket_merge_turn_refusal(session, ticket) == ""


def test_a_tip_whose_turns_were_refused_may_not(branch_ticket):
    session, ticket, tip = branch_ticket
    for n in range(2):
        session.add(
            AgentRun(
                run_code=f"merge-{n}",
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                agent_id="repair",
                stage_key=INTEGRATION_MERGE_STAGE,
                status=RunStatus.FAILED,
                failure_kind=BlockKind.WORK,
                start_head_sha=tip,
            )
        )
    session.commit()

    assert "refused" in ticket_merge_turn_refusal(session, ticket)


def test_a_branch_that_does_not_exist_may_not(branch_ticket):
    session, ticket, _ = branch_ticket
    ticket.branch = "loregarden/never-cut"
    assert "cannot read" in ticket_merge_turn_refusal(session, ticket)
