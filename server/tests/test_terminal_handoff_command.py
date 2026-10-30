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

import ast
import inspect
import textwrap
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.api import ticket_handoff as ticket_handoff_module
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services import orchestration_profile as orchestration_profile_module
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.stage_retry_budget import count_stage_dispatches
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session, select
from tests.handoff_lead_helpers import (
    NO_TARGET_PROSE_LIMIT,
    PROFILE_PROSE_LIMIT,
    no_target_lead,
    profile_lead,
    prose,
)
from tests.handoff_lead_vocabulary import FORBIDDEN_TREE_PHRASES
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


# lg-milestone-that-1214. The same three readings, for the failure that used to
# escape the renderer instead of being rendered by it.
#
# Vector V2, and it is not interchangeable with a genuinely malformed profile
# here. The route's own `start_stage_execution` reaches `orchestration.start_run`
# -> `evaluate_standalone_stage_dispatch` -> `resolve_stage_retry_budget`
# (stage_retry_budget.py:579, 700) -> `resolve_orchestration_profile`,
# **unguarded**, before any tree is prepared. A file that is malformed on disk
# therefore 400s at dispatch with no run stamped at all, so a test built that way
# passes while never reaching the defect. So only `target_branch`'s own view of
# the base branch is patched, which leaves `stage_retry_budget`'s resolver
# intact: the dispatch guard resolves for real, the run is stamped RUNNING, and
# the failure lands exactly where the defect lives.
#
# That view is `git_automation_config.base_branch`, as of
# lg-milestone-that-1419: the nine spellings of this read became one accessor,
# and `target_branch` imports it rather than the resolver behind it. Patching
# the accessor's name here keeps the property the vector depends on — one
# consumer blinded, the rest resolving for real.

MALFORMED_PROFILE = (
    "Conditions cannot be auto-fixed: stage_thrash. Auto-fixable: emptied_group, stale_cursor."
)


@pytest.fixture(name="unreadable_profile")
def unreadable_profile_fixture():
    """`target_branch`'s own view of the base branch, raising the way a malformed
    YAML's `ValidationError` does — a `ValueError` subclass, and not the
    `TargetBranchError` the handler used to guard for."""
    return mock.patch(
        "loregarden.services.target_branch.base_branch",
        side_effect=ValueError(MALFORMED_PROFILE),
    )


def test_a_profile_that_cannot_be_read_is_a_readable_400_and_not_a_500(
    client, db_session, ticket, worktree, unreadable_profile
):
    """AC3 and AC7. The status code, and the words the operator actually reads.

    `describe_handoff_failure` is called *inside* the route's `except ValueError`
    (api/ticket_handoff.py:73-76), so a `ValueError` raised by it is not caught
    by its own handler: FastAPI turns it into a 500 whose whole body is
    `{"detail": "Internal Server Error"}`. `request()` parses that `detail` into
    `ApiError.message` and `describeError` returns it unchanged, so the entire
    "Copy terminal command" error toast reads "Copy terminal command failed /
    Internal Server Error" — which answers neither half of the question the
    operator came with.

    So the `detail` is asserted with `startswith`: that string is verbatim what
    the toast renders, and the action the operator takes next — run the stage
    supervised, in the named worktree — is in it.

    lg-milestone-that-1237 AC18: this edge inherits the new lead with no edit of
    its own, so what changes here is only the expected string. `unreadable_profile`
    patches the resolver with a **plain `ValueError`**, not a
    `ProfileUnreadableError`, so this is input class **B** and the lead is AC3's
    no-target one — not AC2's profile lead, which belongs to the corrupt-file
    vector further down. Built with `no_target_lead` rather than split off the
    detail, because that lead is two sentences and `_first_sentence` would return
    it with `The worktree is …` cut off (AC13).
    """
    with unreadable_profile:
        response = _handoff(client, ticket)

    assert response.status_code == 400, (
        f"the renderer raised inside the route's own handler: {response.status_code} "
        f"{response.text[:200]}"
    )
    detail = response.json()["detail"]
    lead = no_target_lead(ticket.external_id, worktree)
    assert detail.startswith(lead), (
        f"the toast renders this string verbatim:\n{lead!r}\ngot\n{detail!r}"
    )
    measurable = prose(lead, exempt=worktree)
    assert len(measurable) <= NO_TARGET_PROSE_LIMIT, f"{len(measurable)}: {lead}"
    assert "conflict" not in measurable.lower(), (
        f"the operator is still sent to resolve a conflict that does not exist: {lead}"
    )
    assert GitAutomationConfig().base_branch not in measurable, (
        f"the lead names a branch nobody resolved: {lead}"
    )
    assert str(worktree) in lead, f"the tree the operator has to run the stage in: {lead}"
    assert "supervised" in lead, f"the next step is missing: {lead}"


def test_the_profile_failure_is_readable_where_the_toast_clamp_cannot_eat_it(
    client, db_session, ticket, worktree, unreadable_profile
):
    """AC8. The settled run's `stderr` is the only surface carrying the cause.

    `.toast__message` clamps at four lines (about 188 characters at 1600px,
    measured) and the lead sentence alone is 222 characters on this ticket's own
    identifiers, so everything appended behind it — including the validator
    message, the only text saying *which field of which YAML* is wrong — is past
    the clamp and invisible in the toast. `RunLogModal` renders `stderr` under
    its own heading, and before this fix no such run existed at all, because the
    500 skipped `settle_failed_handoff` entirely.

    `stderr` is asserted as equal to the whole `detail`: lead sentence first,
    cause behind it, neither truncated nor replaced by a summary.
    """
    with unreadable_profile:
        response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert MALFORMED_PROFILE in detail, (
        f"the only text naming which field of which YAML is wrong was dropped: {detail!r}"
    )
    runs = _runs(db_session, ticket)
    assert len(runs) == 1, f"one handoff, one run: {[r.run_code for r in runs]}"
    db_session.refresh(runs[0])
    assert runs[0].stderr == detail, (
        f"the run view shows a different string than the toast did: {runs[0].stderr!r}"
    )


def test_a_profile_that_cannot_be_read_still_settles_the_run_the_route_stamped(
    client, db_session, ticket, worktree, unreadable_profile
):
    """AC3's other half — the three run/stage facts.

    The 500 this replaces never reached `settle_failed_handoff` on the next
    line, so the run stayed RUNNING with no agent behind it, the stage stayed
    RUNNING for `fail_interrupted_runs` to kill on somebody else's pass, and the
    dispatch charge stayed spent on work nothing ever attempted. These three
    cannot hold at all unless the run really was stamped, which is what makes
    them the check that V2 reached the defect rather than the dispatch guard in
    front of it.
    """
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, "fixture: nothing charged"

    with unreadable_profile:
        response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    runs = _runs(db_session, ticket)
    assert len(runs) == 1, (
        "the dispatch guard 400-ed before a run was stamped, so this never reached "
        f"the renderer at all: {[r.run_code for r in runs]}"
    )
    db_session.refresh(runs[0])
    assert runs[0].status == RunStatus.FAILED, "the run the route opened must be settled"
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.PENDING, (
        f"the stage was left {ticket.workflow_stage_status}: a dispatch that never "
        "attempted the work puts the stage back where it was"
    )
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
    """AC9, now AC5 of lg-milestone-that-974. The route cannot report on a tree
    it was not told about, and the tree is already resolved inside this call —
    re-deriving it at the route would be a second answer to a question with one
    correct one. Compatibility posture is `internal`: the third element is added
    and every caller moves with it, rather than a second method that returns it.

    The third element is a `PreparedTree`, not a bare path: the *branch* the
    tree was brought up to is the other half of the one answer, and a route
    handed only the path went back to the write-path resolver to name it
    (`_handoff_target_branch`, deleted). `stamp_run_boundary` and the
    invocation's `workspace_root` both read `tree.repo_root`, so there is one
    tree in play and not two.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor
    from tests.worktree_helpers import make_run

    run = make_run(db_session, workspace, ticket, "handoff-signature")
    run.stage_key = STAGE
    db_session.add(run)
    db_session.commit()
    target = _target(db_session, ticket, workspace, repo)

    invocation, _cleanup_path, tree = CliAgentExecutor(db_session).prepare_terminal_handoff(
        run, ticket
    )

    assert tree.repo_root == worktree, "the tree reported is the one the command will run in"
    assert tree.target_branch == target, (
        f"the handoff reports {tree.target_branch!r} as the branch it brought the tree "
        f"up to, but the tree lands on {target!r}"
    )
    assert invocation.cwd == str(tree.repo_root), (
        "the command runs in a different tree from the one reported: "
        "`resolve_terminal_handoff_invocation(workspace_root=...)` must take the "
        "prepared tree, not a second answer"
    )
    assert invocation.adapter


def test_the_terminal_route_takes_the_branch_from_the_tree_it_was_handed(
    client, db_session, workspace, ticket, worktree, repo
):
    """AC7. `_handoff_target_branch` is deleted, and nothing in the router
    resolves a target branch.

    It was the guarded half of this ticket's defect: the route had the tree
    `prepare_terminal_handoff` prepared and then called the *write*-path
    resolver again to name the branch that tree had just been brought up to —
    creating the integration branch, merging the base into it, and able to
    dispatch a blocking merge turn, to fill in one response key.

    Asserted on the module rather than by behaviour because the behaviour is
    identical when the second resolution agrees, which it almost always does.
    The response contract is checked here too, so a deletion that quietly drops
    a key cannot pass.
    """
    from pathlib import Path as _Path

    from loregarden.api import ticket_handoff

    assert not hasattr(ticket_handoff, "_handoff_target_branch"), (
        "the route still re-derives the branch it was handed"
    )
    source = _Path(ticket_handoff.__file__).read_text(encoding="utf-8")
    assert "resolve_target_branch" not in source, (
        "the handoff router still reaches for the write-path resolver"
    )

    response = _handoff(client, ticket)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "run_id",
        "adapter",
        "command",
        "repo_path",
        "target_branch",
        "tree_behind",
        "tree_warning",
    }, f"the response contract changed: {sorted(body)}"
    assert body["repo_path"] == str(worktree)
    assert body["target_branch"] == _target(db_session, ticket, workspace, repo)


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


# --- lg-milestone-that-974, test-break: where the route's two facts come from -


def test_the_route_reports_the_branch_the_handoff_prepared_and_not_a_second_read(
    client, db_session, workspace, ticket, worktree, repo, tmp_path
):
    """AC7, closed against the swap the source grep cannot see.

    `test_the_terminal_route_takes_the_branch_from_the_tree_it_was_handed`
    asserts that `resolve_target_branch` is absent from the router's source and
    that the response names the right branch. Both survive a route that deletes
    the write-path resolver and calls the *read* — `target_branch_name` — at
    the boundary instead: the name is absent from the source, and the second
    read agrees with the first whenever nothing is wrong, which is almost
    always. Agreement is what made the original defect invisible for a release.

    So the tree the handoff hands back is made to disagree. Whatever the route
    prints has to be what it was given; anything else is a second answer to a
    question with one.
    """
    from unittest.mock import patch

    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.handoff_tree_status import PreparedTree

    real = CliAgentExecutor.prepare_terminal_handoff
    sentinel_root = tmp_path / "a-tree-the-route-could-not-have-guessed"
    sentinel_root.mkdir()

    def handing_back_a_different_tree(self, run, ticket_):
        invocation, cleanup_path, tree = real(self, run, ticket_)
        assert tree.target_branch != "integration/not-the-branch-anyone-resolves"
        return (
            invocation,
            cleanup_path,
            PreparedTree(
                repo_root=sentinel_root, target_branch="integration/not-the-branch-anyone-resolves"
            ),
        )

    with patch.object(CliAgentExecutor, "prepare_terminal_handoff", handing_back_a_different_tree):
        response = _handoff(client, ticket)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_branch"] == "integration/not-the-branch-anyone-resolves", (
        f"the route reported {body['target_branch']!r}: it re-derived the branch "
        "instead of taking the one the tree it was handed was brought up to"
    )
    assert body["repo_path"] == str(sentinel_root), (
        f"the route reported {body['repo_path']!r}: the tree it reports on and the "
        "tree it was handed must be the same tree"
    )


def test_the_run_s_boundary_is_stamped_from_the_tree_the_command_will_run_in(
    db_session, workspace, ticket, worktree, repo
):
    """AC5's third clause, which the migrated test states and does not check.

    It asserts `tree.repo_root`, `tree.target_branch` and `invocation.cwd`, and
    says that `stamp_run_boundary` reads `tree.repo_root` too — but nothing
    reads the stamp back. `prepare_terminal_handoff` has `workspace` and
    `workspace_root` in scope, so stamping the *shared checkout* is a one-token
    slip that changes no test: the run would then record a HEAD and a branch
    from a tree the operator never touches, and every later diff of what that
    run changed is taken against the wrong baseline.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor
    from tests.worktree_helpers import make_run

    run = make_run(db_session, workspace, ticket, f"h-{uuid4().hex[:6]}")
    run.stage_key = STAGE
    db_session.add(run)
    db_session.commit()
    assert Path(repo) != worktree, "fixture: the two trees must be distinguishable"

    _invocation, _cleanup, tree = CliAgentExecutor(db_session).prepare_terminal_handoff(run, ticket)

    db_session.refresh(run)
    assert run.start_repo_path == str(tree.repo_root), (
        f"the run was stamped as starting from {run.start_repo_path!r}, not the tree "
        f"the command runs in ({tree.repo_root}) — every later diff of this run's "
        "work is taken against the wrong baseline"
    )
    assert run.start_head_sha == git(tree.repo_root, "rev-parse", "HEAD").stdout.strip()


# lg-milestone-that-1215. The same route, the same question, and this time the
# profile on disk is the defect rather than a patched resolver.
#
# V2 above patches `target_branch`'s own reference to
# `resolve_orchestration_profile` because a file that is malformed *on disk* used
# to 400 at the dispatch guard — `start_stage_execution` ->
# `evaluate_standalone_stage_dispatch` -> `resolve_stage_retry_budget` reads the
# profile unguarded, before any tree is prepared — so a test built that way
# never reached the renderer at all. Measured on this branch: a corrupt
# `default.yaml` raised at `stage_retry_budget.py:662` for every shape, and no
# run was stamped.
#
# That is why the dispatch-path read now falls back to the built-in budgets with
# a WARNING instead of raising (lg-milestone-that-1215 R5): with it, the failure
# lands where the defect lives, the run IS stamped, and the whole settle path
# this module covers is reachable on a real file. So these tests patch
# `orchestration_profile.orchestration_dir` — the directory, not any consumer's
# imported resolver — and every consumer resolves the same broken file for real.
#
# Three shapes, because they are three *exception types* escaping three
# libraries: a truncated file (`ruamel` `ParserError`), a document that is not a
# mapping (`TypeError`), and one that parses but fails schema validation
# (pydantic `ValidationError`, the only one of the three that is a `ValueError`
# and so the only one that ever reached a 400 by itself).

CORRUPT_PROFILES = {
    "corrupt-syntax": "slug: default\nname: [unclosed\n",
    "non-mapping": "- one\n- two\n",
    "schema-invalid": "slug: default\nname: Default\nmonitor:\n  autofix: [stage_thrash]\n",
}


@pytest.fixture(name="corrupt_profile", params=sorted(CORRUPT_PROFILES))
def corrupt_profile_fixture(request, tmp_path: Path, worktree: Path, monkeypatch) -> Path:
    """A real unreadable `default.yaml`, installed after the tree is cut.

    The order matters and is not incidental: cutting the ticket's worktree reads
    the profile itself (`resolve_execution_root` -> `resolve_git_automation` ->
    `resolve_orchestration_profile`), so a fixture that broke the file first
    would fail in its own setup and prove nothing about the route. Depending on
    `worktree` is what pins the ordering.

    The broken file has to be the one that actually resolves.
    `resolve_orchestration_profile` takes the first existing candidate —
    `<orchestration_profile_slug>.yaml`, then `<workspace.slug>.yaml`, then
    `default.yaml` — and the workspace fixture's slug is a uuid with no file of
    its own in this directory, so `default.yaml` is the only candidate that
    exists.

    Returns the absolute path, because that path is what every assertion below
    looks for: it is the one fact that tells the operator which file to open.
    """
    orchestration = tmp_path / "orchestration-corrupt"
    orchestration.mkdir()
    path = orchestration / "default.yaml"
    path.write_text(CORRUPT_PROFILES[request.param], encoding="utf-8")
    monkeypatch.setattr(orchestration_profile_module, "orchestration_dir", lambda: orchestration)
    return path.resolve()


def test_no_shape_of_an_unreadable_profile_reaches_the_operator_as_a_500(
    client, db_session, ticket, corrupt_profile: Path
):
    """AC7 and AC8. The status code, and the words the toast renders verbatim.

    `{"detail": "Internal Server Error"}` is asserted against explicitly rather
    than only implied by `== 400`, because that body is the whole defect: the
    "Copy terminal command" toast renders the `detail` through `describeError`
    unchanged, so an operator whose profile will not parse is told "Copy
    terminal command failed / Internal Server Error" — which answers neither
    half of the question they came with.

    `ProfileUnreadableError` subclasses `RuntimeError`, so no guard spelled
    `except ValueError` catches it. That is deliberate (C2) and it is why every
    tuple on the route is edited rather than relied upon.

    lg-milestone-that-1237 AC2/AC18: this is the vector that really is input
    class **A** — the file on disk is the defect, so the renderer catches a
    `ProfileUnreadableError` — and the route inherits the profile lead with no
    edit of its own. The lead names the file to open and nothing else: the
    worktree is deliberately absent, because nothing in a worktree fixes
    malformed YAML and a second absolute path pushes the actionable one past the
    toast clamp.
    """
    response = _handoff(client, ticket)

    assert response.status_code == 400, (
        f"an unreadable profile escaped the route's own handlers: {response.status_code} "
        f"{response.text[:300]}"
    )
    body = response.json()
    assert body != {"detail": "Internal Server Error"}, (
        "the operator was handed FastAPI's fallback body, which names neither the file "
        "nor the next step"
    )
    assert list(body) == ["detail"], (
        f"the route answers with exactly one string for the toast to render: {sorted(body)}"
    )
    detail = body["detail"]
    lead = profile_lead(ticket.external_id, corrupt_profile)
    assert detail.startswith(lead), (
        f"the toast renders this string verbatim:\n{lead!r}\ngot\n{detail!r}"
    )
    measurable = prose(lead, exempt=corrupt_profile)
    assert len(measurable) <= PROFILE_PROSE_LIMIT, f"{len(measurable)}: {lead}"
    assert str(corrupt_profile) in lead, (
        f"the one action this arm owes the operator is the file to edit, named absolutely: {lead}"
    )
    assert "conflict" not in measurable.lower(), (
        f"malformed YAML is not a conflict, and nothing was merged: {lead}"
    )
    assert GitAutomationConfig().base_branch not in measurable, (
        f"the lead names a branch nobody resolved: {lead}"
    )


def test_the_route_reaches_the_handoff_instead_of_refusing_at_dispatch(
    client, db_session, ticket, corrupt_profile: Path
):
    """AC7's other half — the three run/stage facts, and the proof of the vector.

    These cannot hold at all unless the run really was stamped, which is what
    makes them the check that this reached the renderer rather than the dispatch
    guard in front of it. Before R5 the guard raised on the same file and no run
    existed, so a test asserting only "400" passed while measuring nothing.

    The 500 this replaces never reached `settle_failed_handoff` on the next
    line: the run stayed RUNNING with no agent behind it, the stage stayed
    RUNNING for `fail_interrupted_runs` to kill on somebody else's pass, and the
    dispatch charge stayed spent on work nothing ever attempted.
    """
    before = count_stage_dispatches(db_session, ticket.id, STAGE)
    assert before == 0, "fixture: nothing charged"

    response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    runs = _runs(db_session, ticket)
    assert len(runs) == 1, (
        "the dispatch guard refused before a run was stamped, so this never reached "
        f"the renderer where the defect lives: {[r.run_code for r in runs]}"
    )
    db_session.refresh(runs[0])
    assert runs[0].status == RunStatus.FAILED, (
        f"the run the route opened was left {runs[0].status}: nothing else settles it"
    )
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.PENDING, (
        f"the stage was left {ticket.workflow_stage_status}: a dispatch that never "
        "attempted the work puts the stage back where it was"
    )
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == before, (
        "the dispatch charged for a run that never attempted the work was not handed back"
    )


def test_the_profile_that_will_not_parse_is_named_where_the_operator_can_read_it(
    client, db_session, ticket, corrupt_profile: Path
):
    """AC10 and AC14. The absolute path, in the 400 and in the run's `stderr`.

    The question this surface answers is "why can I not get a terminal command
    for this stage, and which file do I go and fix?", and the action it leads to
    is opening that file. Naming it in the exception alone is not enough — the
    operator never sees that string.

    Both places, for a measured reason. `.toast__message` clamps at four lines
    (about 188 characters at 1600px) and the lead sentence alone measures 191 at
    the live median — an 88-character worktree path and a 21.6-character
    `external_id` — so roughly 290 characters including the whole profile path
    fall past the clamp on a toast that auto-dismisses in 9s. `RunLogModal`
    renders `stderr` under its own heading, with no clamp and no timer, which is
    why no criterion asks for the path to be readable *inside* the toast and
    this one asserts it in `stderr` specifically.

    `stderr` is asserted equal to the whole `detail`: lead sentence first, cause
    behind it, neither truncated nor replaced by a summary.
    """
    response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert str(corrupt_profile) in detail, (
        f"the file the operator has to go and open is not in what the toast renders: {detail!r}"
    )
    [run] = _runs(db_session, ticket)
    db_session.refresh(run)
    assert run.stderr == detail, (
        f"the run view shows a different string than the toast did: {run.stderr!r}"
    )
    assert str(corrupt_profile) in (run.stderr or ""), (
        "the only surface with room for the path dropped it, so past the toast clamp "
        f"there is nothing naming the file: {run.stderr!r}"
    )


def test_one_failure_is_one_refusal_and_one_toast(client, db_session, ticket, corrupt_profile):
    """AC11. One `HTTPException` out of the route, whatever fell over inside it.

    A corrupt profile now fails twice on one request — the dispatch-path budget
    read falls back with a WARNING, and the branch lookup falls back with
    another — and that is the correct outcome, not a failure. What must not
    happen is a second refusal reaching the operator: `pushToast` dedupes by
    text, not by cause, so two `HTTPException`s with different wording are two
    toasts for one click, and the second one would be about the budget rather
    than the file.

    Counted by substituting a subclass for the name the route raises, so the
    count is of refusals *this route* constructed and nothing FastAPI does
    afterwards.
    """
    real = ticket_handoff_module.HTTPException
    refusals: list[tuple] = []

    class CountingHTTPException(real):
        def __init__(self, *args, **kwargs):
            refusals.append((args, kwargs))
            super().__init__(*args, **kwargs)

    with mock.patch.object(ticket_handoff_module, "HTTPException", CountingHTTPException):
        response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    assert len(refusals) == 1, (
        f"the route raised {len(refusals)} HTTPExceptions for one click, so the operator "
        f"gets more than one toast: {[args for args, _ in refusals]}"
    )


def test_both_guards_on_this_route_name_the_unreadable_profile(client):
    """AC8's last sentence, read off the AST of the route module.

    Two guards sit on this route: `start_stage_execution`'s at
    api/ticket_handoff.py:65 and the renderer's at :74. Both are spelled
    `except ValueError` today, and both stop covering an unreadable profile the
    moment the error becomes a `RuntimeError` — the renderer's is the defect
    this ticket closes, and the dispatch one is a regression it would otherwise
    introduce.

    Asserted on the handler rather than through behaviour because the dispatch
    guard is not reachable by this vector once the budget read falls back (R5):
    nothing on the route raises there any more, so no behaviour test in this
    module can tell a named guard from a missing one.
    """
    source = textwrap.dedent(
        inspect.getsource(ticket_handoff_module.build_terminal_handoff_command)
    )
    named = [
        handler
        for handler in ast.walk(ast.parse(source))
        if isinstance(handler, ast.ExceptHandler)  # py-org: allow-isinstance - AST node walk
        and "ProfileUnreadableError"
        in {
            node.id
            for node in ast.walk(handler.type)
            if isinstance(node, ast.Name)  # py-org: allow-isinstance - AST node walk
        }
    ]
    assert len(named) == 2, (
        f"{len(named)} of the route's guards name ProfileUnreadableError: both the "
        "dispatch guard and the renderer's have to, because a RuntimeError is caught by "
        "neither `except ValueError` and the two failures are one request"
    )


# --- lg-milestone-that-975: the failures that are not about the tree ----------
#
# Until this ticket the renderer's whole `ValueError` surface reached the
# operator through `describe_handoff_failure`, so three refusals that happen
# *before* any tree work — an unknown agent, a missing workspace row, a repo
# path that is gone — arrived as "resolve the conflict with main, or run the
# stage supervised — the worktree is /…". The toast clamp and the 9s dismissal
# mean that sentence is not a preface to the diagnosis, it is the whole of what
# is read, so the three were not padded, they were replaced. Before
# lg-milestone-that-917 this route returned `str(exc)`, i.e. accurate; these
# three got strictly worse.
#
# The classification lives in `test_handoff_failure_classification.py`. What is
# here is the route: the status code, the `detail` the toast renders verbatim,
# and the run the route opened one call earlier.

UNKNOWN_AGENT = "an_agent_the_registry_no_longer_has"


def _assert_not_reported_as_a_tree_failure(detail: str) -> None:
    # Case-insensitively, because lg-milestone-that-1237's no-target lead opens
    # its second sentence with `The worktree is`: a leak that differed from the
    # listed phrase only in one capital would otherwise read as clean.
    lowered = detail.lower()
    said = [phrase for phrase in FORBIDDEN_TREE_PHRASES if phrase in lowered]
    assert said == [], (
        f"a failure that is not about the tree is reported as one ({said}): {detail!r}"
    )


@pytest.fixture(name="unknown_agent_ticket")
def unknown_agent_ticket_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    """A ticket parked on a stage naming an agent the registry no longer has.

    The reproduction in the ticket, and a live shape: a Workflow Studio stage
    keeps its `agent_id` string after the agent is renamed or deleted.
    `start_stage_execution` validates the stage, not the registry, so it opens
    the run and the renderer is the first thing to look the agent up.
    """
    return make_agent_stage_ticket(
        db_session,
        workspace,
        external_id="lg-handoff-975",
        stage_key=STAGE,
        agent_id=UNKNOWN_AGENT,
    )


def test_a_stage_naming_an_agent_nobody_has_is_not_reported_as_a_merge_conflict(
    client, db_session, unknown_agent_ticket
):
    """AC1. The ticket's own reproduction, driven through the route it was found on.

    `detail` is asserted on all three counts the operator needs and the one they
    must not be given: which agent, which stage, what to do — and no tree. The
    old text sent them to a worktree to resolve a conflict that does not exist,
    against a branch `GitAutomationConfig()`'s built-in default supplied.
    """
    response = _handoff(client, unknown_agent_ticket)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    _assert_not_reported_as_a_tree_failure(detail)
    assert detail.startswith(f"{unknown_agent_ticket.external_id}:"), (
        f"the toast renders this verbatim and the operator may have several tickets "
        f"open: {detail!r}"
    )
    assert UNKNOWN_AGENT in detail, f"the agent that is missing is not named: {detail!r}"
    assert STAGE in detail, f"the stage holding the bad slot is not named: {detail!r}"
    assert "Workflow Studio" in detail, (
        f"the toast carries no link or button, so the next action has to be in the "
        f"sentence: {detail!r}"
    )


def test_the_unknown_agent_refusal_still_settles_the_run_the_route_opened(
    client, db_session, unknown_agent_ticket
):
    """AC1's other half, and the one the shorter `detail` could regress.

    `start_stage_execution` runs one call before the renderer, so this refusal
    has a RUNNING run behind it exactly as the conflict case does. The run's
    `stderr` is the second surface the sentence lands on (`RunLogModal`), and
    `settle_failed_handoff` derives the decision line from the same string
    (lg-milestone-that-976), so the detail has to be a complete sentence rather
    than a fragment.
    """
    response = _handoff(client, unknown_agent_ticket)

    assert response.status_code == 400
    detail = response.json()["detail"]
    [run] = _runs(db_session, unknown_agent_ticket)
    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, (
        f"the run the route opened was left {run.status}: a non-tree failure settles the "
        "same way a tree failure does"
    )
    assert run.stderr == detail, (
        f"the run view shows a different string than the toast did: {run.stderr!r}"
    )
    db_session.refresh(unknown_agent_ticket)
    assert unknown_agent_ticket.workflow_stage_status != StageStatus.RUNNING, (
        "the stage still reads as running with nothing running it"
    )


def test_a_dirty_checkout_still_leads_with_its_own_refusal(
    client, db_session, ticket, worktree, monkeypatch
):
    """AC3, the arm-ordering guard, read through the route.

    `DirtyPrimaryCheckoutError` is a `ValueError` subclass **and** so is the new
    `HandoffTreePreparationError`, so the order of the renderer's three arms is
    load-bearing in a way nothing in Python enforces: a bare `except ValueError`
    placed above the dirty-checkout arm swallows it, and the operator holding
    uncommitted work in the shared checkout is told to go and resolve a merge
    conflict instead of being told which files are in the way.

    The error is raised from `resolve_execution_root`, which is where the live
    one comes from (`_require_clean_fallback`, worktrees off over a dirty shared
    checkout), rather than being constructed inside the handler's reach.
    """
    from loregarden.models.domain import DirtyCheckoutCause, PrimaryCheckoutUse
    from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError

    refusal = DirtyPrimaryCheckoutError(
        repo_root=worktree,
        use=PrimaryCheckoutUse.TICKET_FALLBACK,
        cause=DirtyCheckoutCause.DIRTY,
        dirty_paths=("someone-elses.txt",),
    )
    monkeypatch.setattr(
        "loregarden.agents.executors.cli.resolve_execution_root",
        mock.Mock(side_effect=refusal),
    )

    response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    _assert_not_reported_as_a_tree_failure(detail)
    assert detail == str(refusal), (
        "the dirty-checkout refusal was re-worded on its way out, which loses the files "
        f"the operator has to deal with: {detail!r}"
    )
    assert "Commit, stash or discard" in detail, detail
    assert "someone-elses.txt" in detail, (
        f"the file in the way is not named, and it is the only actionable fact: {detail!r}"
    )


FOURTH_REFUSAL = "lg-handoff-975: the skill this stage names was deleted — pick another."


def test_a_refusal_nobody_classified_keeps_its_own_text_on_this_route(
    client, db_session, ticket, worktree
):
    """AC1 and AC2's default, on a vector no criterion names.

    The three refusals this ticket lists are the ones that exist today, so every
    other test here passes against a route that enumerates those three and
    leaves the old unconditional tree sentence as the default. The spec's claim
    is the opposite one: an *unclassified* failure keeps its own text, and that
    inverted default is what stops the next raise site added to the renderer
    from arriving as a merge conflict without anyone remembering this ticket.

    So the failure is raised from the prompt build, which runs below the
    renderer's tree-preparation `try` and is therefore classified by nothing —
    and it must still settle the run, because the run was stamped RUNNING one
    call earlier whatever the failure turns out to be.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor

    with mock.patch.object(
        CliAgentExecutor, "_build_prompt", side_effect=ValueError(FOURTH_REFUSAL)
    ):
        response = _handoff(client, ticket)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    _assert_not_reported_as_a_tree_failure(detail)
    assert detail == FOURTH_REFUSAL, (
        "a failure raised outside the renderer's tree-preparation try was reported as a "
        f"tree failure, or re-worded on the way out: {detail!r}"
    )
    [run] = _runs(db_session, ticket)
    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, (
        f"the run the route opened was left {run.status}: both arms of this guard settle, "
        "or lg-milestone-that-1215's 'the run must not stay RUNNING' holds on one of them"
    )
    assert run.stderr == detail, run.stderr
