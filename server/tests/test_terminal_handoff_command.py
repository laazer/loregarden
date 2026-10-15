"""What `POST /terminal_handoff_command` tells the operator about the tree.

The route's whole job is to answer one question: *can I paste this and trust
the tree it will run in?* Until lg-milestone-that-917 it answered half of it —
a command, and nothing about the checkout the command would run in — and the
checkout it handed over had never been brought up to the ticket's target
branch. Measured live on 2026-10-10: 33 of 39 worktrees were behind
`origin/main` (median 40 commits, max 134) and 7 of 39 were dirty, so neither
the refresh nor the skip is an edge case.

Three outcomes, three shapes: success, degraded (the tree was left behind),
failure (the refresh conflicted). The failure case also has to settle the
`AgentRun` the route opened one call earlier, because `start_stage_execution`
runs first and a 400 after it leaves a stage reading RUNNING with no agent.

Beside `test_terminal_handoff_liveness.py`, which covers reaping the runs this
route creates rather than what it returns.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.stage_retry_budget import count_stage_dispatches
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session, select
from tests.worktree_helpers import (
    commit_on,
    cut_ticket_worktree,
    git,
    make_agent_stage_ticket,
    make_repo,
)

STAGE = "work"


@pytest.fixture(name="handoff_adapter", autouse=True)
def handoff_adapter_fixture(monkeypatch):
    """A terminal handoff needs a real CLI adapter to render a command for.

    The suite pins `LOREGARDEN_CLI_ADAPTER=local` session-wide for determinism,
    and the handoff refuses `local` outright — so without this every assertion
    below would be made against a 400 about the adapter, including the three
    that expect a 400 about the tree. Nothing is executed: the route renders a
    command string for a human to paste.
    """
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path / "handoff", name="project")


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session, repo) -> Workspace:
    ws = Workspace(slug=f"handoff-{uuid4()}", name="Handoff", repo_path=str(repo))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    return make_agent_stage_ticket(
        db_session, workspace, external_id="lg-handoff-917", stage_key=STAGE
    )


@pytest.fixture(name="worktree")
def worktree_fixture(db_session: Session, workspace: Workspace, ticket: Ticket, repo) -> Path:
    return cut_ticket_worktree(db_session, workspace, ticket, repo)


def _target(db_session: Session, ticket: Ticket, workspace: Workspace, repo) -> str:
    return resolve_target_branch(db_session, ticket, workspace, repo_root=Path(repo))


def _handoff(client, ticket: Ticket):
    return client.post(
        f"/api/tickets/{ticket.id}/terminal_handoff_command", json={"stage_key": STAGE}
    )


def _runs(db_session: Session, ticket: Ticket) -> list[AgentRun]:
    return list(
        db_session.exec(
            select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == STAGE)
        ).all()
    )


def _first_sentence(detail: str) -> str:
    head, _, _ = detail.partition(". ")
    return head if head == detail else f"{head}."


# --- success ------------------------------------------------------------------


def test_a_refreshed_handoff_reports_the_tree_it_hands_over(
    client, db_session, workspace, ticket, worktree, repo
):
    """AC11, success. The command is still the point; these four fields are the
    other half of the operator's question, and `tree_behind == 0` with an empty
    `tree_warning` is the only shape that says "this tree carries its target".
    """
    target = _target(db_session, ticket, workspace, repo)
    commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")

    response = _handoff(client, ticket)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["command"].strip(), "a handoff without a command is not a handoff"
    assert body["repo_path"] == str(worktree), "the tree named is the one the command runs in"
    assert body["target_branch"] == target
    assert body["tree_behind"] == 0, "the tree was brought up to its target before handover"
    assert body["tree_warning"] == ""
    assert body["run_id"] and body["adapter"]


# --- degraded -----------------------------------------------------------------


def test_a_dirty_tree_is_still_handed_over_and_the_operator_is_told(
    client, db_session, workspace, ticket, worktree, repo
):
    """AC11, degraded — one handoff in five (7 of 39 measured dirty).

    The command is still returned and still pasteable: an un-refreshable tree
    is a degraded handoff, not a blocked one. What must not happen is the
    silent version, where the operator is handed exactly the stale tree this
    ticket is about and a server-side log nobody reads.
    """
    target = _target(db_session, ticket, workspace, repo)
    commit_on(Path(repo), target, "sibling-a.txt", "one\n")
    commit_on(Path(repo), target, "sibling-b.txt", "two\n")
    (worktree / "in-flight-1.txt").write_text("still writing\n")
    (worktree / "in-flight-2.txt").write_text("still writing\n")
    assert git(worktree, "status", "--porcelain").stdout.strip(), "fixture: tree must be dirty"

    response = _handoff(client, ticket)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["command"].strip(), "the handoff is not blocked by a tree it could not refresh"
    assert body["tree_behind"] == 2
    assert body["tree_warning"] == (
        f"Commit or stash the 2 uncommitted changes, then copy again: "
        f"it is 2 commits behind {target} — the tree is {worktree}."
    )
    assert (worktree / "in-flight-1.txt").exists(), "and the uncommitted work is untouched"


# --- failure ------------------------------------------------------------------


@pytest.fixture(name="conflicting")
def conflicting_fixture(db_session, workspace, ticket, worktree, repo) -> str:
    """The ticket's branch and its target, each with a different version of one
    line — the state in which `_refresh_onto` aborts and raises."""
    target = _target(db_session, ticket, workspace, repo)
    (worktree / "shared.txt").write_text("the ticket's version\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "ticket side")
    commit_on(Path(repo), target, "shared.txt", "a sibling's version\n")
    return target


def test_a_conflicting_refresh_is_reported_in_one_readable_sentence(
    client, db_session, ticket, worktree, conflicting
):
    """AC12. `.toast__message` clamps at `max-height: 5.8em; overflow: hidden`
    and an error toast auto-dismisses after 9s, so anything past about four
    lines is unreadable *and* unrecoverable. `_refresh_onto`'s `ValueError`
    carries git's whole merge output, which is why the first sentence has to
    carry the four facts the operator acts on and nothing else.

    The 160-character cap is measured with the paths removed. An absolute
    worktree path is 70–130 characters on its own, so a sentence naming the
    path *and* the four required facts cannot fit in 160 as literally written;
    the cap bounds the prose, which is what makes it readable. Recorded as a
    checkpoint on this run.
    """
    response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    first = _first_sentence(detail)
    assert len(first.replace(str(worktree), "")) <= 160, f"{len(first)}: {first}"
    assert ticket.external_id in first, first
    assert str(worktree) in first, first
    assert conflicting in first, first
    assert "supervised" in first, f"the next step is missing: {first}"
    for noise in ("CONFLICT", "Automatic merge failed", "<<<<<<<"):
        assert noise not in first, f"git's merge output reached the first sentence: {first}"


def test_the_full_merge_output_is_kept_where_the_run_view_already_shows_it(
    client, db_session, ticket, worktree, conflicting
):
    """AC12's other half. The merge output is not discarded — it is the only
    thing that says *what* conflicted — it just does not go in the toast."""
    response = _handoff(client, ticket)

    assert response.status_code == 400
    runs = _runs(db_session, ticket)
    assert len(runs) == 1, f"one handoff, one run: {[r.run_code for r in runs]}"
    db_session.refresh(runs[0])
    stderr = runs[0].stderr or ""
    assert "could not take" in stderr, stderr
    assert "shared.txt" in stderr, f"the conflicting file is not recorded anywhere: {stderr}"


def test_a_failed_tree_preparation_does_not_leave_the_stage_looking_live(
    client, db_session, ticket, worktree, conflicting
):
    """AC13. `start_stage_execution` runs one call before the failure, so
    without this every conflict strands a RUNNING stage with no agent behind
    it — and a stranded run is what `fail_interrupted_runs` later kills on
    somebody else's pass.
    """
    response = _handoff(client, ticket)

    assert response.status_code == 400
    runs = _runs(db_session, ticket)
    assert len(runs) == 1
    db_session.refresh(runs[0])
    assert runs[0].status == RunStatus.FAILED, "the run the route opened must be settled"
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status != StageStatus.RUNNING, (
        "the stage still reads as running with nothing running it"
    )


def test_a_stage_that_never_attempted_the_work_is_refunded_exactly_once(
    client, db_session, ticket, worktree, conflicting
):
    """AC13's second half. The budget is charged pre-dispatch and never
    reconciled against the outcome, so a handoff that failed before any agent
    existed would otherwise walk a healthy stage toward its breaker — and
    clearing that breaker costs an operator a deliberate requeue with a reason
    string. Exactly once: a double refund hands back a charge somebody spent.
    """
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, "fixture: nothing charged"

    assert _handoff(client, ticket).status_code == 400

    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, (
        "the dispatch charged for a run that never attempted the work was not handed back"
    )


def test_the_conflicted_merge_is_not_left_half_applied_in_the_tree(
    client, db_session, ticket, worktree, conflicting
):
    """What the operator finds in the tree after the 400. A merge left
    conflicted is how the person sent in to resolve it loses the ticket's side.
    """
    own = git(worktree, "rev-parse", "HEAD").stdout.strip()

    assert _handoff(client, ticket).status_code == 400

    assert git(worktree, "status", "--porcelain").stdout.strip() == ""
    assert git(worktree, "rev-parse", "HEAD").stdout.strip() == own
    assert (worktree / "shared.txt").read_text() == "the ticket's version\n"


# --- the boundary's own signature ---------------------------------------------


def test_prepare_terminal_handoff_hands_back_the_tree_it_prepared(
    db_session, workspace, ticket, worktree, repo
):
    """AC9. The route cannot report on a tree it was not told about, and the
    tree is already resolved inside this call — re-deriving it at the route
    would be a second answer to a question with one correct one. Compatibility
    posture is `internal`: the third element is added and every caller moves
    with it, rather than a second method that returns it.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor
    from tests.worktree_helpers import make_run

    run = make_run(db_session, workspace, ticket, "handoff-signature")
    run.stage_key = STAGE
    db_session.add(run)
    db_session.commit()

    invocation, _cleanup_path, repo_root = CliAgentExecutor(db_session).prepare_terminal_handoff(
        run, ticket
    )

    assert repo_root == worktree, "the tree reported is the one the command will run in"
    assert invocation.adapter


# --- what the three outcomes do to the stage's budget --------------------------
#
# lg-milestone-that-917, test-break. AC13's refund is one line in the failure
# branch, and three plausible spellings of it pass the failure test above: one
# that refunds on every outcome, one that clears the stage's whole counter
# instead of handing back a single charge, and one that refunds once for the
# first failure and then stops. Each hands a real retry budget — the breaker a
# human has to clear by hand, with a reason string — to the wrong accounting.


def test_a_degraded_handoff_keeps_its_charge_and_its_run(
    client, db_session, workspace, ticket, worktree, repo
):
    """The dirty tree of AC11 is a *successful* handoff: a command was handed
    over and an operator is about to run it. Refunding it, or settling its run
    FAILED, would do the opposite of AC13 — the stage would read as never
    dispatched while a human runs it in their terminal, and the run the
    handoff opened would be reaped out from under them.
    """
    target = _target(db_session, ticket, workspace, repo)
    commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    (worktree / "in-flight.txt").write_text("still writing\n")

    response = _handoff(client, ticket)

    assert response.status_code == 200, response.text
    assert response.json()["tree_warning"], "fixture: this is the degraded shape"
    runs = _runs(db_session, ticket)
    assert len(runs) == 1
    db_session.refresh(runs[0])
    assert runs[0].status != RunStatus.FAILED, "a handed-over run was settled as a failure"
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1, (
        "the dispatch an operator is about to spend was handed back"
    )


def test_a_failed_handoff_hands_back_its_own_charge_and_not_the_stage_s_history(
    client, db_session, workspace, ticket, worktree, conflicting
):
    """AC13 names `refund_stage_dispatch_charged_before(...)`, which drops **one**
    marker. `clear_stage_dispatches` — the other function in that module, one
    line away — drops them all, and passes the failure test above, which starts
    from an empty counter.

    The difference is the whole point of the budget: an earlier attempt that
    really did run is a charge somebody spent, and a conflicted handoff is not
    a reason to give it back. A stage that has already burned attempts would
    otherwise be reset to a fresh budget by anyone clicking a copy that fails.
    """
    from loregarden.services.stage_retry_budget import record_stage_dispatch

    record_stage_dispatch(db_session, ticket.id, STAGE)
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1

    assert _handoff(client, ticket).status_code == 400

    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 1, (
        "the earlier attempt's charge was cleared along with this one's"
    )


def test_every_failed_handoff_is_refunded_not_only_the_first(
    client, db_session, workspace, ticket, worktree, conflicting
):
    """An operator who hits a conflict copies again after trying to fix it —
    that is what the button is for. Each attempt opens its own run and charges
    its own dispatch, so a refund that fires once walks a stage nobody has yet
    run toward its breaker, and clearing that costs a deliberate requeue.

    Also pins that each failure settles **its own** run: three 400s must leave
    three settled runs, not one settled and two more stranded RUNNING, which is
    the state AC13 exists to prevent.
    """
    for attempt in range(3):
        assert _handoff(client, ticket).status_code == 400, f"attempt {attempt}"

    runs = _runs(db_session, ticket)
    assert len(runs) == 3, f"one run per attempt: {[r.run_code for r in runs]}"
    for run in runs:
        db_session.refresh(run)
        assert run.status == RunStatus.FAILED, f"{run.run_code} was left looking live"
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, (
        "only the first failure was handed back"
    )
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status != StageStatus.RUNNING
