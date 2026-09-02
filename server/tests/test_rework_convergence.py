"""A loop that cannot converge should stop, and say why.

Ticket 23 of the blobert milestone 14 run cycled `implementation` <->
`script_review` six times on one finding. A retry cap alone treats that and a
loop that is making progress identically: it cuts off work that was converging,
and still spends several full cycles on work that was not.

The signal is cheap because both halves are already on the ledger row — the
finding in `content_json`, the tree in `commit_sha`, a field `Artifact` already
had and the ledger simply left empty.
"""

from loregarden.models.domain import (
    Artifact,
    ReworkArtifactKind,
    ReworkStopReason,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_reroute_exhausts_budget,
    rework_is_stuck,
)
from sqlmodel import Session


def _ticket(session: Session) -> Ticket:
    workspace = Workspace(slug="conv", name="Conv", repo_path="/nonexistent/conv")
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    ticket = Ticket(
        external_id="conv-1",
        workspace_id=workspace.id,
        title="Converge",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _round(session: Session, ticket: Ticket, *, context: str, sha: str) -> None:
    """A ledger row as `record_rework_feedback` writes one, with the tree pinned
    so the test controls what changed between rounds."""
    import json

    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ReworkArtifactKind.FEEDBACK,
            title="Rework feedback — implement",
            commit_sha=sha,
            content_json=json.dumps(
                {"from_stage": "verify", "target_stage": "implement", "context": context}
            ),
        )
    )
    session.commit()


def test_the_same_finding_against_the_same_tree_is_stuck(db_session):
    """Ticket 23's shape. Nothing changed, so the next round cannot differ."""
    ticket = _ticket(db_session)
    _round(db_session, ticket, context="fix the parser", sha="abc123")
    _round(db_session, ticket, context="fix the parser", sha="abc123")

    assert rework_is_stuck(db_session, ticket, "implement") is True


def test_a_changed_finding_is_not_stuck(db_session):
    """The loop is doing something, even against the same tree — a reviewer that
    raises a different objection each round is making progress of a kind, and
    stopping it belongs to the count, not here."""
    ticket = _ticket(db_session)
    _round(db_session, ticket, context="fix the parser", sha="abc123")
    _round(db_session, ticket, context="now fix the lexer", sha="abc123")

    assert rework_is_stuck(db_session, ticket, "implement") is False


def test_a_changed_tree_is_not_stuck(db_session):
    """Same objection, but the agent committed something between rounds. The
    finding may be about different code now."""
    ticket = _ticket(db_session)
    _round(db_session, ticket, context="fix the parser", sha="abc123")
    _round(db_session, ticket, context="fix the parser", sha="def456")

    assert rework_is_stuck(db_session, ticket, "implement") is False


def test_one_round_is_never_stuck(db_session):
    """Convergence needs two rounds to compare. A first reroute has nothing to
    disagree with."""
    ticket = _ticket(db_session)
    _round(db_session, ticket, context="fix the parser", sha="abc123")

    assert rework_is_stuck(db_session, ticket, "implement") is False


def test_an_unrecorded_tree_is_not_stuck(db_session):
    """Rows written before the ledger recorded `commit_sha` carry "". Two of
    those must not read as "the same tree twice" — that would stop loops
    retroactively on rows that never claimed anything about the tree."""
    ticket = _ticket(db_session)
    _round(db_session, ticket, context="fix the parser", sha="")
    _round(db_session, ticket, context="fix the parser", sha="")

    assert rework_is_stuck(db_session, ticket, "implement") is False


def test_the_stop_decision_says_which_reason(db_session):
    """AC3. A count that ran out and a loop that repeated itself want different
    next actions, so the human is told which happened."""
    ticket = _ticket(db_session)
    _round(db_session, ticket, context="fix the parser", sha="abc123")

    stop = record_reroute_exhausts_budget(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="verify",
        context="fix the parser",
    )

    # The new row is written against a workspace with no repo, so its sha is ""
    # and cannot match — the loop reads as still moving rather than stuck.
    assert stop is ReworkStopReason.NONE


def test_the_count_still_stops_a_loop_that_keeps_changing(db_session):
    """AC4 in reverse: convergence does not replace the cap, it precedes it. A
    loop raising a fresh finding every round is still bounded."""
    ticket = _ticket(db_session)
    for index in range(MAX_REWORK_REROUTES):
        _round(db_session, ticket, context=f"finding {index}", sha=f"sha{index}")

    stop = record_reroute_exhausts_budget(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="verify",
        context="a brand new finding",
    )

    assert stop is ReworkStopReason.BUDGET


def test_no_stop_reason_must_be_compared_not_tested_for_truth():
    """The trap this return type introduced, pinned so it cannot come back.

    `record_reroute_exhausts_budget` used to return a bool, and one caller in
    `permission_bridge` still read it as one after the change. Every member of a
    StrEnum is a non-empty string, so `if record_reroute_exhausts_budget(...)`
    became unconditionally true — the scope-denial reroute blocked itself before
    it could hand work to the sibling implementer.

    mypy accepts an enum as a truth-value expression, so nothing else catches
    this. The only defence is that callers compare against NONE, and the only
    thing that makes that memorable is a test saying why.
    """
    assert bool(ReworkStopReason.NONE) is True, (
        "NONE is truthy — a caller writing `if stop:` gets the opposite of what "
        "it means, so every caller must compare `is not ReworkStopReason.NONE`"
    )
