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
    AgentRun,
    Artifact,
    ReworkArtifactKind,
    ReworkStopReason,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
    Worktree,
)
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    _entries,
    record_reroute_exhausts_budget,
    record_rework_feedback,
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


def test_the_tree_is_read_from_the_run_s_worktree_not_the_shared_checkout(db_session, tmp_path):
    """The common path, not an edge case: worktree execution is the default.

    `resolve_head_sha` answers for `workspace.repo_path` — the shared checkout —
    while `GitAutomationConfig.worktree` defaults to True, so a ticket's commits
    normally land in a per-ticket worktree the shared checkout never sees.
    Stamping the shared HEAD compared a repository the run never wrote to: two
    rounds read as "the same tree" while the ticket's worktree advanced between
    them, which is a false STUCK on the majority of real runs.

    Asserted by giving the run a worktree whose HEAD differs from the workspace
    checkout's, and requiring the ledger to record the worktree's.
    """
    from tests.worktree_helpers import git, make_repo

    shared = make_repo(tmp_path, name="shared")
    worktree_dir = make_repo(tmp_path, name="run-worktree")
    (worktree_dir / "only-here.txt").write_text("x", encoding="utf-8")
    git(worktree_dir, "add", "-A")
    git(worktree_dir, "commit", "-q", "-m", "work the shared checkout never saw")

    shared_head = git(shared, "rev-parse", "HEAD").stdout.strip()
    worktree_head = git(worktree_dir, "rev-parse", "HEAD").stdout.strip()
    assert shared_head != worktree_head

    workspace = Workspace(slug="wt", name="WT", repo_path=str(shared))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    ticket = Ticket(
        external_id="wt-1",
        workspace_id=workspace.id,
        title="Worktree",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    # The run comes first: `Worktree.agent_run_id` is NOT NULL, so the worktree
    # cannot exist without the run that owns it.
    run = AgentRun(
        run_code="wt_run",
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        agent_id="backend_implementer",
        stage_key="implement",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    worktree = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        worktree_path=str(worktree_dir),
    )
    db_session.add(worktree)
    db_session.commit()
    db_session.refresh(worktree)

    run.worktree_id = worktree.id
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    record_rework_feedback(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="verify",
        context="fix the parser",
        run_id=run.id,
    )

    entries = _entries(db_session, ticket, "implement")
    assert entries[-1].commit_sha == worktree_head, (
        "the ledger recorded the shared checkout's HEAD, which the ticket's "
        "commits never touch — convergence would compare the wrong repository"
    )
