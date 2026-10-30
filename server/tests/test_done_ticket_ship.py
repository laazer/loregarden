"""Done ticket, red or missing ship — detector, probe, invariant, Home read path.

Literal names are the Specification document's test list (D1, P3, I2, …).
GitHub and `git ls-remote` are the only mocked boundaries.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from loregarden.config import settings
from loregarden.models.domain import (
    Artifact,
    CIRunResult,
    CIStatus,
    InvariantReentry,
    MonitorArtifactKind,
    PrListingState,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.ci_checks import HEAD_CHECKS_FIELDS, OpenPullRequest, rollup
from loregarden.services.github_pr_listing import GitHubReadError, PullRequestListing
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.invariants import Invariant, Violation, run_invariant
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.services.ticket_pull_request import GhCheck
from loregarden.services.workflow_state import _derive_ticket_state
from loregarden.testing.factories import make_ticket, make_workspace
from sqlmodel import Session, col, select

SERVER_FAIL = {
    "name": "Server (Python)",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "isRequired": True,
}
CLIENT_FAIL = {
    "name": "Client (TypeScript)",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "isRequired": True,
}
SERVER_PEND = {
    "name": "Server (Python)",
    "status": "IN_PROGRESS",
    "conclusion": "",
    "isRequired": True,
}
SERVER_PASS = {
    "name": "Server (Python)",
    "status": "COMPLETED",
    "conclusion": "SUCCESS",
    "isRequired": True,
}
OPTIONAL_FAIL = {
    "name": "optional-lint",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "isRequired": False,
}
CLIENT_PASS = {
    "name": "Client (TypeScript)",
    "status": "COMPLETED",
    "conclusion": "SUCCESS",
    "isRequired": True,
}
REQUIRED_SKIP = {
    "name": "Deploy",
    "status": "COMPLETED",
    "conclusion": "SKIPPED",
    "isRequired": True,
}
STATUS_FAIL = {
    "context": "continuous-integration/travis-ci",
    "state": "FAILURE",
    "isRequired": True,
}

FINDING_TITLE = "invariant_unrepaired:done_ticket_ship_is_observable"
INVARIANT_NAME = "done_ticket_ship_is_observable"


def _dts():
    import importlib

    return importlib.import_module("loregarden.services.done_ticket_ship")


def _open_pr(head: str, *checks: dict, number: int = 733) -> OpenPullRequest:
    rollup_ = {"contexts": {"nodes": list(checks)}} if checks else None
    return OpenPullRequest.model_validate(
        {
            "number": number,
            "url": f"https://github.com/o/r/pull/{number}",
            "headRefName": head,
            "headRefOid": "a" * 40,
            "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup_}}]},
        }
    )


def _listed(number: int, state: str, head: str) -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        state=state,
        url=f"https://github.com/o/r/pull/{number}",
        headRefName=head,
        head=head,
    )


def _listing(prs, state: PrListingState = PrListingState.COMPLETE) -> PullRequestListing:
    return PullRequestListing(state=state, pull_requests=list(prs), total_count=len(prs))


def _ls_remote(heads: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    body = "".join(f"{'b' * 40}\trefs/heads/{name}\n" for name in heads)
    return subprocess.CompletedProcess(["git", "ls-remote"], 0, stdout=body, stderr="")


def _for_each_ref(heads: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    body = "".join(f"{name}\n" for name in heads)
    return subprocess.CompletedProcess(["git", "for-each-ref"], 0, stdout=body, stderr="")


@contextmanager
def _github(
    dts,
    *,
    open_prs: list | None = None,
    all_prs: list | None = None,
    open_state: PrListingState = PrListingState.COMPLETE,
    all_state: PrListingState = PrListingState.COMPLETE,
    remote_heads: tuple[str, ...] = (),
    #: The reconcile checkout's own branches. Empty by default, so a test that
    #: wants a never-reached-remote row must say the branch exists somewhere:
    #: a carrying branch in no repository is not a ship anyone can push.
    local_heads: tuple[str, ...] = (),
    github_error: BaseException | None = None,
    ls_error: BaseException | None = None,
    local_ref_error: BaseException | None = None,
):
    """Patch the detector's GitHub and git-branch-listing seams on the module."""
    open_listing = _listing(open_prs or [], open_state)
    all_listing = _listing(all_prs or [], all_state)

    def ls_remote(args, **_kwargs):
        if args and args[0] == "for-each-ref":
            if local_ref_error is not None:
                raise local_ref_error
            return _for_each_ref(local_heads)
        if ls_error is not None:
            raise ls_error
        return _ls_remote(remote_heads)

    if github_error is not None:
        open_cm = mock.patch.object(dts, "list_open_pull_requests", side_effect=github_error)
        all_cm = mock.patch.object(dts, "list_pull_requests", side_effect=github_error)
    else:
        open_cm = mock.patch.object(dts, "list_open_pull_requests", return_value=open_listing)
        all_cm = mock.patch.object(dts, "list_pull_requests", return_value=all_listing)
    with (
        open_cm,
        all_cm,
        mock.patch.object(dts, "run_git", side_effect=ls_remote),
    ):
        yield


def _lore(session: Session) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def _tree(
    session: Session,
    workspace: Workspace,
    *,
    child_state: TicketState = TicketState.DONE,
    #: DONE for the never-reached-remote shape: nothing publishes until the
    #: root finishes, so a done child of an open tree with no PR anywhere is
    #: mid-flight rather than a ship that never happened. Measured on the live
    #: database: 192 of 267 such rows are ordinary in-flight work.
    root_state: TicketState = TicketState.IN_PROGRESS,
    child_landed: str = "",
    root_ci: bool = False,
    child_ci: bool = False,
    child_external_id: str = "lg-ship-child-1",
    root_external_id: str = "lg-ship-root-1",
    title: str = "Map the hive",
) -> tuple[Ticket, Ticket]:
    """1624 shape: in-progress root, done child, no CIRunResult on the child by default."""
    root = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=root_external_id,
        title="Living map",
        work_item_type=WorkItemType.FEATURE,
        state=root_state,
    )
    child = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=child_external_id,
        title=title,
        work_item_type=WorkItemType.TASK,
        state=child_state,
        parent_ticket_id=root.id,
    )
    if child_landed:
        child.landed_sha = child_landed
        session.add(child)
        session.commit()
        session.refresh(child)
    if root_ci:
        session.add(
            CIRunResult(
                workspace_id=workspace.id,
                ticket_id=root.id,
                status=CIStatus.FAILING,
                head_sha="a" * 40,
                pr_number=733,
            )
        )
        session.commit()
    if child_ci:
        session.add(
            CIRunResult(
                workspace_id=workspace.id,
                ticket_id=child.id,
                status=CIStatus.FAILING,
                head_sha="c" * 40,
            )
        )
        session.commit()
    return root, child


def _ids(violations) -> list[str]:
    return [v.external_id for v in violations]


def _ship_findings(session: Session) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                col(Artifact.kind) == MonitorArtifactKind.FINDING.value,
                col(Artifact.title) == FINDING_TITLE,
            )
        ).all()
    )


def _ship_step():
    step = next((s for s in PERIODIC_STEPS if s.name == INVARIANT_NAME), None)
    assert step is not None, "custom SweepStep named done_ticket_ship_is_observable"
    return step


def _invariant():
    inv = next((i for i in INVARIANTS if i.name == INVARIANT_NAME), None)
    assert inv is not None, "register done_ticket_ship_is_observable in INVARIANTS"
    return inv


def _detect(session, dts, **github):
    with _github(dts, **github):
        return dts.detect_broken_ships(session)


# -- I4 / C1: confirmation that DONE and auto-fix arming stay put --------------


def test_derive_ticket_state_does_not_consult_ci_or_prs():
    src = inspect.getsource(_derive_ticket_state)
    assert "CIRunResult" not in src
    assert "CIStatus" not in src
    assert "pull_request" not in src.lower()


# -- I1 registry ----------------------------------------------------------------


def test_done_ticket_ship_is_registered_as_expected_report_only():
    inv = _invariant()
    assert inv.ticket == "lg-verdict-outcome-1630"
    assert inv.reentry is InvariantReentry.EXPECTED
    assert inv.repair is None
    assert inv.prevented_by == ""
    assert inv.since == date(2026, 10, 28)
    assert "stage map" in inv.expected_because
    assert "pull request" in inv.unrepaired_action.lower()
    assert "push" in inv.unrepaired_action.lower()
    scheduled = [step.name for step in PERIODIC_STEPS]
    assert scheduled.count(INVARIANT_NAME) == 1


# -- I2 EXPECTED report-only must not file a monitor-escalation bug ------------


def test_expected_unrepaired_does_not_upsert_monitor_escalation_bug(db_session: Session):
    workspace = _lore(db_session)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-esc-1",
        state=TicketState.DONE,
    )
    held = [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=utcnow(),
            detail="PR #733 failing",
        )
    ]

    def detect(_session: Session) -> list[Violation]:
        return list(held)

    inv = Invariant(
        name=INVARIANT_NAME,
        ticket="lg-verdict-outcome-1630",
        statement="no done ticket's carrying pull request is OPEN with a failing required check",
        detect=detect,
        repair=None,
        reentry=InvariantReentry.EXPECTED,
        since=date(2026, 10, 28),
        expected_because="a ticket finishes its stage map before remote CI settles",
        unrepaired_action="Open the named pull request and fix or merge it.",
    )
    check = run_invariant(db_session, inv)
    assert (check.found, check.repaired, check.reentered) == (1, 0, 0)
    assert [f.ticket_id for f in _ship_findings(db_session)] == [ticket.id]
    bugs = [
        b
        for b in db_session.exec(
            select(Ticket).where(
                Ticket.workspace_id == workspace.id,
                Ticket.work_item_type == WorkItemType.BUG,
                col(Ticket.legacy_external_id).like("monitor-escalation:%"),
            )
        ).all()
        if INVARIANT_NAME in (b.title + b.description)
    ]
    assert bugs == []


# -- R required_rollup ----------------------------------------------------------


def test_required_rollup_ignores_failing_non_required_checks():
    from loregarden.services.ci_checks import required_rollup

    checks = [
        GhCheck.model_validate(SERVER_PASS),
        GhCheck.model_validate(OPTIONAL_FAIL),
    ]
    assert required_rollup(checks) is CIStatus.PASSING
    assert rollup(checks) is CIStatus.FAILING


def test_required_rollup_treats_all_none_as_rollup():
    from loregarden.services.ci_checks import required_rollup

    checks = [
        GhCheck.model_validate(
            {"name": "Server (Python)", "status": "COMPLETED", "conclusion": "FAILURE"}
        ),
        GhCheck.model_validate({"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}),
    ]
    assert all(c.is_required is None for c in checks)
    assert required_rollup(checks) is rollup(checks) is CIStatus.FAILING


def test_required_rollup_mixed_none_and_true_uses_only_true():
    from loregarden.services.ci_checks import required_rollup

    checks = [
        GhCheck.model_validate(SERVER_PASS),
        GhCheck.model_validate({"name": "travis", "status": "COMPLETED", "conclusion": "FAILURE"}),
    ]
    assert checks[0].is_required is True
    assert checks[1].is_required is None
    assert required_rollup(checks) is CIStatus.PASSING
    assert rollup(checks) is CIStatus.FAILING


def test_required_rollup_all_false_is_not_empty_pending():
    # CHECKPOINT: all-False is not the all-None fallback. Filtering to True
    # then calling rollup([]) would be PENDING and flag every optional-only PR.
    from loregarden.services.ci_checks import required_rollup

    checks = [
        GhCheck.model_validate(OPTIONAL_FAIL),
        GhCheck.model_validate(
            {
                "name": "coverage",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "isRequired": False,
            }
        ),
    ]
    assert all(c.is_required is False for c in checks)
    assert required_rollup(checks) is not CIStatus.PENDING
    assert required_rollup(checks) is not CIStatus.FAILING


def test_gh_check_keeps_is_required_none_and_true():
    absent = GhCheck.model_validate({"name": "x"})
    assert absent.is_required is None
    flagged = GhCheck.model_validate({"name": "x", "isRequired": True})
    assert flagged.is_required is True
    explicit_none = GhCheck.model_validate({"name": "x", "isRequired": None})
    assert explicit_none.is_required is None


def test_gh_check_keeps_is_required_false():
    """`_absent_is_empty` on `*` would coerce None to \"\" — False must stay bool."""
    flagged = GhCheck.model_validate({"name": "x", "isRequired": False})
    assert flagged.is_required is False


def test_head_checks_fields_cannot_ask_is_required():
    """GitHub refuses `isRequired` in a repository-wide listing, so it is not asked there.

    Verified live on 2026-10-28 against laazer/loregarden. Inside
    `pullRequest(number: 733)` with the argument, GitHub answers: Server
    (Python) and Client (TypeScript) FAILURE isRequired=true, PR UX section and
    PR fix coverage SUCCESS isRequired=true, Workspace gates (PR diff) SUCCESS
    and CI FAILURE isRequired=false. Inside `pullRequests(states: OPEN, ...)`
    the same selection errors UNPROCESSABLE on every node ("A pull request ID
    or pull request number is required"), because `isRequired(pullRequestId:,
    pullRequestNumber:)` takes an argument no node in a listing can supply.

    `HEAD_CHECKS_FIELDS` is that listing's selection, shared with the CI poller
    and the workspace dashboard, so asking for the field there would make every
    one of those reads exit non-zero and go blind. `required_rollup`'s all-None
    fallback is what carries the required reading on this path, and
    lg-verdict-outcome-1637 sources the required set from the repository's
    ruleset so the field becomes live rather than assumed.
    """
    assert "isRequired" not in HEAD_CHECKS_FIELDS
    assert GhCheck.model_validate({"name": "x"}).is_required is None


def test_open_pr_with_no_checks_is_pending_required():
    from loregarden.services.ci_checks import required_rollup

    pr = _open_pr("integration/lg-ship-root-1")
    assert required_rollup(pr.checks) is CIStatus.PENDING


# -- D detector predicate -------------------------------------------------------


def test_done_child_tree_pr_failing_without_ci_run_result_is_reported(db_session: Session):
    """D1. 1624 shape: done child, tree PR #733 failing, no CIRunResult on the child."""
    dts = _dts()
    workspace = _lore(db_session)
    root, child = _tree(db_session, workspace, root_ci=True, child_ci=False)
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL, number=733)
    found = _detect(
        db_session,
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
        remote_heads=("publish/integration/lg-ship-root-1",),
    )
    assert _ids(found) == [child.external_id]
    hit = found[0]
    assert hit.ticket_id == child.id
    assert hit.reason == "failing"
    assert hit.pr_number == 733
    assert hit.check_names == ["Server (Python)", "Client (TypeScript)"]
    assert "integration/lg-ship-root-1" in hit.carrying_branch
    assert hit.ci_run_result  # copied from the ROOT as evidence
    child_results = list(
        db_session.exec(select(CIRunResult).where(CIRunResult.ticket_id == child.id)).all()
    )
    assert child_results == []


def test_pending_required_checks_are_a_violation(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-pend-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_PEND, number=733)
    found = _detect(
        db_session,
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].reason == "pending"


def test_merged_or_closed_carrying_pr_is_not_a_violation(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-merged-1")
    head = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[_listed(733, "MERGED", head)],
    )
    assert found == []
    found_closed = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[_listed(733, "CLOSED", head)],
    )
    assert found_closed == []


def test_member_own_branch_missing_remote_with_tree_pr_is_not_a_violation(
    db_session: Session,
):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-own-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    found = _detect(
        db_session,
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
        remote_heads=(),  # child's loregarden/<child> is absent; tree PR exists
    )
    assert len(found) == 1
    assert found[0].reason == "failing"


def test_no_pr_and_no_remote_ref_is_never_reached_remote(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    root, child = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-nrr-1",
        root_state=TicketState.DONE,
    )
    found = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[],
        remote_heads=(),
        local_heads=("integration/lg-ship-root-1",),
    )
    # The root is in the same state for the same reason, on the same carrying
    # branch; Home groups never-reached-remote by that branch into one row.
    assert _ids(found) == [root.external_id, child.external_id]
    assert found[0].reason == "never_reached_remote"
    assert found[0].pr_number is None
    assert found[0].pr_url is None
    assert "never reached remote" in (found[0].detail or found[0].reason.replace("_", " "))


def test_carrying_branch_in_no_repository_is_not_never_reached_remote(db_session: Session):
    """The branch is on no remote AND in no checkout, so there is nothing to push.

    Measured read-only on the live database 2026-10-28: every one of the 75
    never-reached-remote rows was this state, on seven carrying branches
    (`integration/lg-flex-views-432` and six more) that `git ls-remote --heads
    origin` and `git for-each-ref refs/heads` both agreed do not exist. The
    invariant tells the operator to push the published branch; for these there
    is no branch anywhere to push. Tracked as `lg-verdict-outcome-1647`.
    """
    dts = _dts()
    workspace = _lore(db_session)
    _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-gone-1",
        root_state=TicketState.DONE,
    )
    found = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[],
        remote_heads=(),
        local_heads=("some/other/branch",),
    )
    assert found == []


def test_unreadable_local_refs_are_unavailable_not_never_reached_remote(db_session: Session):
    """Empty is never green in the other direction: a checkout that cannot be read."""
    dts = _dts()
    workspace = _lore(db_session)
    _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-localerr-1",
        root_state=TicketState.DONE,
    )
    step = _ship_step()
    with _github(
        dts,
        open_prs=[],
        all_prs=[],
        local_ref_error=OSError("for-each-ref failed"),
    ):
        step.run(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.UNAVAILABLE
    assert "for-each-ref" in (probe.error or "")
    assert dts.violations_for_invariant(db_session) == []


def test_standalone_merged_deleted_branch_is_not_never_reached_remote(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-hist-1",
        state=TicketState.DONE,
    )
    ticket.branch = "loregarden/lg-ship-hist-1"
    db_session.add(ticket)
    db_session.commit()
    found = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[_listed(12, "MERGED", ticket.branch)],
        remote_heads=(),
    )
    assert found == []


def test_missing_local_integration_ref_still_matches_publish_integration_pr(
    db_session: Session,
):
    """D7. Do not call published_branch — the local integration ref may be missing."""
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-d7-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    with mock.patch("loregarden.services.publish_tree.published_branch") as published:
        found = _detect(
            db_session,
            dts,
            open_prs=[pr],
            all_prs=[_listed(733, "OPEN", head)],
        )
    published.assert_not_called()
    assert _ids(found) == [child.external_id]


def test_child_landed_sha_does_not_skip_a_failing_tree_pr(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-landed-1",
        child_landed="d" * 40,
    )
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    found = _detect(
        db_session,
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]


def test_wont_do_and_initiatives_are_skipped(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-wont-1",
        state=TicketState.WONT_DO,
    )
    make_ticket(
        db_session,
        workspace_id=None,
        external_id="init-ship-1",
        work_item_type=WorkItemType.INITIATIVE,
        state=TicketState.DONE,
        title="An initiative",
    )
    found = _detect(db_session, dts, open_prs=[], all_prs=[], remote_heads=())
    assert "lg-ship-wont-1" not in _ids(found)
    assert "init-ship-1" not in _ids(found)


def test_pushed_branch_with_no_pr_is_not_a_violation(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-push-1")
    found = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[],
        remote_heads=("integration/lg-ship-root-1",),
    )
    assert found == []


def test_ci_run_result_on_the_child_is_not_the_predicate(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(
        db_session, workspace, child_external_id="lg-ship-child-ci-1", child_ci=True
    )
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    found = _detect(
        db_session,
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].ci_run_result in (None, "")


def test_detector_still_reports_when_ci_enabled_is_off(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-off-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    with mock.patch.object(settings, "ci_enabled", False):
        found = _detect(
            db_session,
            dts,
            open_prs=[pr],
            all_prs=[_listed(733, "OPEN", head)],
        )
    assert _ids(found) == [child.external_id]


# -- P probe three-way result ---------------------------------------------------


def test_github_read_error_is_unavailable_not_empty_green(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-gh-1")
    found = _detect(db_session, dts, github_error=GitHubReadError("HTTP 401"))
    assert found == []
    step = _ship_step()
    with _github(dts, github_error=GitHubReadError("HTTP 401")):
        step.run(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.UNAVAILABLE
    assert probe.error
    assert dts.violations_for_invariant(db_session) == []


def test_load_probe_with_no_row_is_unavailable(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.UNAVAILABLE
    assert probe.error == "Ship probe has not run yet"
    assert probe.violations == []


def test_red_then_github_down_keeps_last_known_and_errors(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-keep-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    step = _ship_step()
    with _github(
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    ):
        step.run(db_session)
    first = dts.load_probe(db_session, workspace.id)
    assert first.listing_state is PrListingState.COMPLETE
    assert _ids(first.violations) == [child.external_id]
    with _github(dts, github_error=GitHubReadError("gh: timeout")):
        step.run(db_session)
    second = dts.load_probe(db_session, workspace.id)
    assert second.listing_state is PrListingState.UNAVAILABLE
    assert _ids(second.violations) == [child.external_id]
    assert dts.violations_for_invariant(db_session) == []


def test_incomplete_listing_reports_known_red_not_never_reached_remote(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-inc-red-1")
    other_root, other = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-inc-nrr-1",
        root_external_id="lg-ship-inc-root-2",
        root_state=TicketState.DONE,
    )
    del other_root
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    step = _ship_step()
    with _github(
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
        open_state=PrListingState.INCOMPLETE,
        all_state=PrListingState.INCOMPLETE,
        remote_heads=(),
    ):
        step.run(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.INCOMPLETE
    assert _ids(probe.violations) == ["lg-ship-inc-red-1"]
    assert probe.violations[0].reason == "failing"
    assert other.external_id not in _ids(probe.violations)
    assert dts.violations_for_invariant(db_session) == []


def test_successful_ask_with_zero_violations_retracts_findings(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-retract-1",
        state=TicketState.DONE,
    )
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=MonitorArtifactKind.FINDING.value,
            title=FINDING_TITLE,
            content_json=json.dumps({"condition": "invariant_unrepaired"}),
        )
    )
    db_session.commit()
    step = _ship_step()
    with _github(
        dts,
        open_prs=[],
        all_prs=[_listed(1, "MERGED", "loregarden/lg-ship-retract-1")],
        remote_heads=("loregarden/lg-ship-retract-1",),
    ):
        step.run(db_session)
    assert _ship_findings(db_session) == []
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.COMPLETE
    assert probe.violations == []


def test_two_reconcile_passes_rereport_unrepaired(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-twice-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    step = _ship_step()
    with _github(
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    ):
        first = step.run(db_session)
        second = step.run(db_session)
    for check in (first, second):
        repaired = getattr(check, "repaired", 0)
        assert repaired == 0
    findings = _ship_findings(db_session)
    assert [f.ticket_id for f in findings] == [child.id]


def test_violations_for_invariant_empty_on_unavailable_and_incomplete(
    db_session: Session,
):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-vfi-1")
    step = _ship_step()
    with _github(dts, github_error=GitHubReadError("down")):
        step.run(db_session)
    assert dts.violations_for_invariant(db_session) == []
    with _github(
        dts,
        open_prs=[],
        all_prs=[],
        open_state=PrListingState.INCOMPLETE,
        all_state=PrListingState.INCOMPLETE,
    ):
        step.run(db_session)
    assert dts.violations_for_invariant(db_session) == []


def test_detect_does_not_raise_on_github_read_error(db_session: Session):
    dts = _dts()
    _tree(db_session, _lore(db_session), child_external_id="lg-ship-noraise-1")
    found = _detect(db_session, dts, github_error=GitHubReadError("down"))
    assert found == []


def test_ls_remote_failure_is_unavailable_not_never_reached_remote(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-ls-1")
    step = _ship_step()
    with _github(dts, open_prs=[], all_prs=[], ls_error=OSError("ls-remote failed")):
        step.run(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.UNAVAILABLE
    assert dts.violations_for_invariant(db_session) == []


def test_github_error_on_one_workspace_does_not_stop_the_other(db_session: Session, tmp_path: Path):
    dts = _dts()
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    ws_a = make_workspace(db_session, slug="ship-a", repo_path=str(repo_a))
    ws_b = make_workspace(db_session, slug="ship-b", repo_path=str(repo_b))
    _tree(
        db_session,
        ws_a,
        child_external_id="lg-ship-a-child",
        root_external_id="lg-ship-a-root",
    )
    _root_b, child_b = _tree(
        db_session,
        ws_b,
        child_external_id="lg-ship-b-child",
        root_external_id="lg-ship-b-root",
    )
    head_b = "publish/integration/lg-ship-b-root"
    pr_b = _open_pr(head_b, SERVER_FAIL, CLIENT_FAIL, number=9)

    def open_for(repo_root: Path):
        if Path(repo_root) == repo_a:
            raise GitHubReadError("workspace A down")
        return _listing([pr_b])

    def all_for(repo_root: Path, *_args, **_kwargs):
        if Path(repo_root) == repo_a:
            raise GitHubReadError("workspace A down")
        return _listing([_listed(9, "OPEN", head_b)])

    with (
        mock.patch.object(dts, "list_open_pull_requests", side_effect=open_for),
        mock.patch.object(dts, "list_pull_requests", side_effect=all_for),
        mock.patch.object(dts, "run_git", return_value=_ls_remote(())),
    ):
        _ship_step().run(db_session)
        found = dts.detect_broken_ships(db_session)
    assert child_b.external_id in _ids(found)
    probe_a = dts.load_probe(db_session, ws_a.id)
    assert probe_a.listing_state is PrListingState.UNAVAILABLE


def test_done_with_failing_pr_leaves_ticket_state_done(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-still-done-1")
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    _detect(
        db_session,
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    )
    db_session.refresh(child)
    assert child.state is TicketState.DONE


# -- GET /api/done-ticket-ship --------------------------------------------------


def test_get_done_ticket_ship_never_calls_github(client, db_session: Session):
    with (
        mock.patch("loregarden.services.ci_checks.list_open_pull_requests") as open_gh,
        mock.patch("loregarden.services.github_pr_listing.list_pull_requests") as all_gh,
    ):
        response = client.get("/api/done-ticket-ship")
    assert response.status_code == 200
    open_gh.assert_not_called()
    all_gh.assert_not_called()
    body = response.json()
    lore = next(p for p in body["probes"] if p["workspace_slug"] == "loregarden")
    assert lore["listing_state"] == "unavailable"
    assert lore["error"] == "Ship probe has not run yet"
    assert lore["violations"] == []


def test_get_done_ticket_ship_returns_probe_violations(client, db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(
        db_session, workspace, child_external_id="lg-living-map-1624", title="Map the hive"
    )
    head = "publish/integration/lg-ship-root-1"
    pr = _open_pr(head, SERVER_FAIL, CLIENT_FAIL)
    step = _ship_step()
    with _github(
        dts,
        open_prs=[pr],
        all_prs=[_listed(733, "OPEN", head)],
    ):
        step.run(db_session)
    with mock.patch("loregarden.services.ci_checks.list_open_pull_requests") as open_gh:
        response = client.get("/api/done-ticket-ship")
    open_gh.assert_not_called()
    assert response.status_code == 200
    lore = next(p for p in response.json()["probes"] if p["workspace_slug"] == "loregarden")
    assert lore["listing_state"] == "complete"
    (hit,) = lore["violations"]
    assert hit["external_id"] == "lg-living-map-1624"
    assert hit["ticket_id"] == child.id
    assert hit["title"] == "Map the hive"
    assert hit["reason"] == "failing"
    assert hit["pr_number"] == 733
    assert hit["pr_url"] == "https://github.com/o/r/pull/733"
    assert hit["check_names"] == ["Server (Python)", "Client (TypeScript)"]


# -- Adversarial seams the named ACs leave open --------------------------------


def test_child_own_branch_failing_pr_is_ignored_when_tree_pr_is_green(db_session: Session):
    """Carrying candidates are the root's. A red PR on loregarden/<child> is not the ship."""
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-own-pr-1")
    tree_head = "publish/integration/lg-ship-root-1"
    child_head = "loregarden/lg-ship-own-pr-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[
            _open_pr(tree_head, SERVER_PASS, CLIENT_PASS, number=733),
            _open_pr(child_head, SERVER_FAIL, CLIENT_FAIL, number=999),
        ],
        all_prs=[
            _listed(733, "OPEN", tree_head),
            _listed(999, "OPEN", child_head),
        ],
    )
    assert child.external_id not in _ids(found)
    assert found == []


def test_tree_pr_number_is_used_not_the_childs_own_pr(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-own-pr-2")
    tree_head = "publish/integration/lg-ship-root-1"
    child_head = "loregarden/lg-ship-own-pr-2"
    found = _detect(
        db_session,
        dts,
        open_prs=[
            _open_pr(tree_head, SERVER_FAIL, CLIENT_FAIL, number=733),
            _open_pr(child_head, SERVER_FAIL, CLIENT_FAIL, number=999),
        ],
        all_prs=[
            _listed(733, "OPEN", tree_head),
            _listed(999, "OPEN", child_head),
        ],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].pr_number == 733


def test_open_pr_on_integration_head_without_publish_prefix_still_matches(db_session: Session):
    """A tree PR opened before publish/ still matches integration/<root> via branch_for_pr_head."""
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-nopub-1")
    head = "integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, SERVER_FAIL, CLIENT_FAIL)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].pr_number == 733


def test_publish_prefix_on_remote_is_not_never_reached_remote(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-pub-remote-1")
    found = _detect(
        db_session,
        dts,
        open_prs=[],
        all_prs=[],
        remote_heads=("publish/integration/lg-ship-root-1",),
    )
    assert found == []


def test_prefer_open_over_merged_when_two_candidates_have_prs(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-two-pr-1")
    own = "loregarden/lg-ship-root-1"
    integration = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(own, SERVER_FAIL, CLIENT_FAIL, number=733)],
        all_prs=[
            _listed(10, "MERGED", integration),
            _listed(733, "OPEN", own),
        ],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].pr_number == 733


def test_older_red_candidate_is_not_hidden_by_newer_green_pr(db_session: Session):
    # CHECKPOINT: any candidate with an OPEN failing required check is a
    # violation. OPEN-then-newest names the PR when more than one is red; it
    # must not drop a red head because the other candidate's newer PR is green.
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-rank-1")
    integration = "publish/integration/lg-ship-root-1"
    own = "loregarden/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[
            _open_pr(integration, SERVER_FAIL, CLIENT_FAIL, number=10),
            _open_pr(own, SERVER_PASS, CLIENT_PASS, number=733),
        ],
        all_prs=[
            _listed(10, "OPEN", integration),
            _listed(733, "OPEN", own),
        ],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].pr_number == 10


def test_standalone_done_with_failing_own_branch_pr_is_reported(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-solo-1",
        state=TicketState.DONE,
        title="Solo ship",
    )
    head = "loregarden/lg-ship-solo-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, SERVER_FAIL, CLIENT_FAIL, number=50)],
        all_prs=[_listed(50, "OPEN", head)],
    )
    assert _ids(found) == [ticket.external_id]
    assert found[0].pr_number == 50


def test_in_progress_root_is_not_reported_for_its_own_failing_pr(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    root, child = _tree(db_session, workspace, child_external_id="lg-ship-root-skip-1")
    head = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, SERVER_FAIL, CLIENT_FAIL)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert root.external_id not in _ids(found)
    assert _ids(found) == [child.external_id]


def test_open_pr_with_no_checks_is_a_pending_violation(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-nocheck-1")
    head = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].reason == "pending"


def test_skipped_non_required_with_passing_required_is_not_a_violation(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-skip-opt-1")
    head = "publish/integration/lg-ship-root-1"
    optional_skip = {
        "name": "optional-lint",
        "status": "COMPLETED",
        "conclusion": "SKIPPED",
        "isRequired": False,
    }
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, SERVER_PASS, CLIENT_PASS, optional_skip)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert found == []


def test_skipped_only_required_checks_are_not_failing_or_pending(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-skip-req-1")
    head = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, REQUIRED_SKIP)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert found == []


def test_check_names_use_status_context_when_name_is_empty(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-ctx-1")
    head = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, STATUS_FAIL)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].check_names == ["continuous-integration/travis-ci"]


def test_ci_run_result_evidence_is_the_roots_status_string(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-ev-1",
        root_ci=True,
        child_ci=True,
    )
    head = "publish/integration/lg-ship-root-1"
    found = _detect(
        db_session,
        dts,
        open_prs=[_open_pr(head, SERVER_FAIL, CLIENT_FAIL)],
        all_prs=[_listed(733, "OPEN", head)],
    )
    assert _ids(found) == [child.external_id]
    assert found[0].ci_run_result == CIStatus.FAILING.value


def test_list_open_pull_requests_once_per_workspace(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _tree(db_session, workspace, child_external_id="lg-ship-once-a")
    _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-once-b",
        root_external_id="lg-ship-once-root-b",
    )
    head_a = "publish/integration/lg-ship-root-1"
    head_b = "publish/integration/lg-ship-once-root-b"
    with (
        mock.patch.object(
            dts,
            "list_open_pull_requests",
            return_value=_listing(
                [
                    _open_pr(head_a, SERVER_FAIL, CLIENT_FAIL, number=1),
                    _open_pr(head_b, SERVER_FAIL, CLIENT_FAIL, number=2),
                ]
            ),
        ) as open_gh,
        mock.patch.object(
            dts,
            "list_pull_requests",
            return_value=_listing([_listed(1, "OPEN", head_a), _listed(2, "OPEN", head_b)]),
        ),
        mock.patch.object(dts, "run_git", return_value=_ls_remote(())),
    ):
        dts.detect_broken_ships(db_session)
    repos = [
        str(call.args[0] if call.args else call.kwargs.get("repo_root"))
        for call in open_gh.call_args_list
    ]
    assert len(repos) == len(set(repos)), repos


def test_detector_source_does_not_read_forbidden_seams():
    src = inspect.getsource(_dts())
    assert "settings.ci_enabled" not in src
    assert "ticket_for_branch" not in src
    assert "_candidate_branches" not in src
    assert "_TicketIndex" not in src
    assert "ci_poll_tick_seconds" not in src
    assert "published_branch(" not in src


def test_defect_report_only_still_calls_escalate(db_session: Session):
    workspace = _lore(db_session)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-def-esc-1",
        state=TicketState.DONE,
    )

    def detect(_session: Session) -> list[Violation]:
        return [
            Violation(
                subject_id=ticket.id,
                ticket_id=ticket.id,
                entered_at=utcnow(),
                detail="landed work is not committed",
            )
        ]

    inv = Invariant(
        name="landed_work_is_committed",
        ticket="lg-verdict-outcome-1",
        statement="landed work is committed",
        detect=detect,
        repair=None,
        reentry=InvariantReentry.DEFECT,
        since=date(2026, 1, 1),
        prevented_by="server/tests/test_invariants.py::test_placeholder",
        unrepaired_action="Commit the landed work.",
    )
    with mock.patch("loregarden.services.invariants.escalate_invariant_reentries") as escalate:
        run_invariant(db_session, inv)
    escalate.assert_called()


def test_retract_does_not_delete_other_invariants_findings(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-ship-keep-other-1",
        state=TicketState.DONE,
    )
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=MonitorArtifactKind.FINDING.value,
            title=FINDING_TITLE,
            content_json=json.dumps({"condition": "invariant_unrepaired"}),
        )
    )
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=MonitorArtifactKind.FINDING.value,
            title="invariant_unrepaired:landed_work_is_committed",
            content_json=json.dumps({"condition": "invariant_unrepaired"}),
        )
    )
    db_session.commit()
    with _github(
        dts,
        open_prs=[],
        all_prs=[_listed(1, "MERGED", "loregarden/lg-ship-keep-other-1")],
        remote_heads=("loregarden/lg-ship-keep-other-1",),
    ):
        _ship_step().run(db_session)
    titles = [
        f.title
        for f in db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    ]
    assert FINDING_TITLE not in titles
    assert "invariant_unrepaired:landed_work_is_committed" in titles


def test_complete_ask_retracts_only_tickets_no_longer_violating(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root_a, child_a = _tree(db_session, workspace, child_external_id="lg-ship-part-a")
    _root_b, child_b = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-part-b",
        root_external_id="lg-ship-part-root-b",
    )
    del _root_a, _root_b
    head_a = "publish/integration/lg-ship-root-1"
    head_b = "publish/integration/lg-ship-part-root-b"
    step = _ship_step()
    with _github(
        dts,
        open_prs=[
            _open_pr(head_a, SERVER_FAIL, CLIENT_FAIL, number=1),
            _open_pr(head_b, SERVER_FAIL, CLIENT_FAIL, number=2),
        ],
        all_prs=[_listed(1, "OPEN", head_a), _listed(2, "OPEN", head_b)],
    ):
        step.run(db_session)
    assert {f.ticket_id for f in _ship_findings(db_session)} == {child_a.id, child_b.id}
    with _github(
        dts,
        open_prs=[_open_pr(head_a, SERVER_FAIL, CLIENT_FAIL, number=1)],
        all_prs=[_listed(1, "OPEN", head_a), _listed(2, "MERGED", head_b)],
    ):
        step.run(db_session)
    assert {f.ticket_id for f in _ship_findings(db_session)} == {child_a.id}


def test_incomplete_after_never_reached_remote_does_not_retract_findings(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    root_nrr, nrr = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-inc-keep-nrr",
        root_state=TicketState.DONE,
    )
    root_red, red = _tree(
        db_session,
        workspace,
        child_external_id="lg-ship-inc-keep-red",
        root_external_id="lg-ship-inc-keep-root-2",
        root_state=TicketState.DONE,
    )
    step = _ship_step()
    with _github(
        dts,
        open_prs=[],
        all_prs=[],
        remote_heads=(),
        local_heads=(
            "integration/lg-ship-inc-keep-root-1",
            "integration/lg-ship-root-1",
            "integration/lg-ship-inc-keep-root-2",
        ),
    ):
        step.run(db_session)
    recorded = {root_nrr.id, nrr.id, root_red.id, red.id}
    assert {f.ticket_id for f in _ship_findings(db_session)} == recorded
    head = "publish/integration/lg-ship-inc-keep-root-2"
    with _github(
        dts,
        open_prs=[_open_pr(head, SERVER_FAIL, CLIENT_FAIL)],
        all_prs=[_listed(733, "OPEN", head)],
        open_state=PrListingState.INCOMPLETE,
        all_state=PrListingState.INCOMPLETE,
        remote_heads=(),
    ):
        step.run(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.INCOMPLETE
    assert _ids(probe.violations) == [root_red.external_id, red.external_id]
    assert nrr.external_id not in _ids(probe.violations)
    assert root_nrr.external_id not in _ids(probe.violations)
    assert {f.ticket_id for f in _ship_findings(db_session)} == recorded
    assert dts.violations_for_invariant(db_session) == []


def test_ls_remote_failure_keeps_last_known_violations(db_session: Session):
    dts = _dts()
    workspace = _lore(db_session)
    _root, child = _tree(db_session, workspace, child_external_id="lg-ship-ls-keep-1")
    head = "publish/integration/lg-ship-root-1"
    step = _ship_step()
    with _github(
        dts,
        open_prs=[_open_pr(head, SERVER_FAIL, CLIENT_FAIL)],
        all_prs=[_listed(733, "OPEN", head)],
    ):
        step.run(db_session)
    with _github(dts, open_prs=[], all_prs=[], ls_error=OSError("ls-remote failed")):
        step.run(db_session)
    probe = dts.load_probe(db_session, workspace.id)
    assert probe.listing_state is PrListingState.UNAVAILABLE
    assert _ids(probe.violations) == [child.external_id]
    assert dts.violations_for_invariant(db_session) == []


def test_get_done_ticket_ship_filters_by_workspace_slug(
    client, db_session: Session, tmp_path: Path
):
    dts = _dts()
    repo_a = tmp_path / "filter-a"
    repo_b = tmp_path / "filter-b"
    repo_a.mkdir()
    repo_b.mkdir()
    ws_a = make_workspace(db_session, slug="ship-filter-a", repo_path=str(repo_a))
    ws_b = make_workspace(db_session, slug="ship-filter-b", repo_path=str(repo_b))
    _tree(
        db_session,
        ws_a,
        child_external_id="lg-ship-fa-child",
        root_external_id="lg-ship-fa-root",
    )
    _root_b, child_b = _tree(
        db_session,
        ws_b,
        child_external_id="lg-ship-fb-child",
        root_external_id="lg-ship-fb-root",
    )
    del _root_b
    head_b = "publish/integration/lg-ship-fb-root"

    def open_for(repo_root: Path):
        if Path(repo_root) == repo_b:
            return _listing([_open_pr(head_b, SERVER_FAIL, CLIENT_FAIL, number=9)])
        return _listing([])

    def all_for(repo_root: Path, *_args, **_kwargs):
        if Path(repo_root) == repo_b:
            return _listing([_listed(9, "OPEN", head_b)])
        return _listing([])

    with (
        mock.patch.object(dts, "list_open_pull_requests", side_effect=open_for),
        mock.patch.object(dts, "list_pull_requests", side_effect=all_for),
        mock.patch.object(
            dts, "run_git", return_value=_ls_remote(("integration/lg-ship-fa-root",))
        ),
    ):
        _ship_step().run(db_session)
    with mock.patch("loregarden.services.ci_checks.list_open_pull_requests") as open_gh:
        response = client.get("/api/done-ticket-ship", params={"workspace": "ship-filter-b"})
    open_gh.assert_not_called()
    assert response.status_code == 200
    probes = response.json()["probes"]
    assert [p["workspace_slug"] for p in probes] == ["ship-filter-b"]
    assert probes[0]["violations"][0]["external_id"] == child_b.external_id
    assert probes[0]["violations"][0]["ticket_id"] == child_b.id
    assert ws_a.slug not in {p["workspace_slug"] for p in probes}
