"""Every state invariant the reconciler enforces, and the adapters that feed it.

Adding a fix for a bad state? Register it here (`services.invariants` says
why): a detector, a repair, and either the test that proves the guard
(`DEFECT`) or the reason the state is entered legitimately (`EXPECTED`).
`test_invariants.py` fails a registration missing any of them, and the reconcile
pass picks it up from `INVARIANTS` by name (`reconciliation.PERIODIC_STEPS`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import (
    InvariantReentry,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.block_settlement import settle_unclassified_blocks, unclassified_blocks
from loregarden.services.closed_workflow_sweep import close_workflows, open_workflows
from loregarden.services.dependency_release import releasable_tickets, release_tickets
from loregarden.services.gate_parked_sweep import block_parked, parked_by_gate
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.queue_repair import held_but_blocked, rearm_held
from loregarden.services.reference_survey.inbox import cleared_cards, close_cards
from loregarden.services.squash_twin_sweep import record_twins, twinned_branches
from loregarden.services.temp_workspaces import archive_workspaces, temp_workspaces
from loregarden.services.terminal_stage_sweep import (
    finish_cleared_landings,
    tickets_whose_landing_cleared,
)
from loregarden.services.ticket_dependencies import ancestor_waits, drop_ancestor_waits
from loregarden.services.tree_publish_scheduler import (
    close_held_cards,
    published_cards_held,
)
from loregarden.services.unasked_awaits import block_unasked_awaits, unasked_awaits
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _tickets(session: Session, violations: Sequence[Violation]) -> list[Ticket]:
    tickets = (session.get(Ticket, v.subject_id) for v in violations)
    return [ticket for ticket in tickets if ticket is not None]


def _unsettled_blocks(session: Session) -> list[Violation]:
    """`entered_at` is the ticket's `updated_at`: a block has no timestamp of its own."""
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=ticket.updated_at,
            detail=f"{ticket.external_id} on '{ticket.workflow_stage_key}': "
            f"{ticket.blocking_issues}",
        )
        for ticket in unclassified_blocks(session)
    ]


def _settle_blocks(session: Session, violations: Sequence[Violation]) -> int:
    return settle_unclassified_blocks(session, _tickets(session, violations))


def _gate_parked(session: Session) -> list[Violation]:
    """`entered_at` is when the parking orchestration finished."""
    return [
        Violation(
            subject_id=item.ticket.id,
            ticket_id=item.ticket.id,
            entered_at=item.run.finished_at,
            detail=f"{item.ticket.external_id} parked at '{item.ticket.workflow_stage_key}' "
            f"by an exhausted gate ({item.run.run_code})",
        )
        for item in parked_by_gate(session)
    ]


def _block_gate_parked(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return block_parked(
        session, [item for item in parked_by_gate(session) if item.ticket.id in wanted]
    )


def _cleared_landings(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=None,
            detail=f"{ticket.external_id}'s landing would now succeed",
        )
        for ticket in tickets_whose_landing_cleared(session)
    ]


def _finish_landings(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: finishing is a write to git, and the
    # read that justified it must be this pass's.
    wanted = {v.subject_id for v in violations}
    tickets = [t for t in tickets_whose_landing_cleared(session) if t.id in wanted]
    return len(finish_cleared_landings(session, tickets))


def _held_cards(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.card.id,
            ticket_id=item.root.id,
            entered_at=None,
            detail=f"{item.root.external_id}: {item.held}",
        )
        for item in published_cards_held(session)
    ]


def _close_cards(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return close_held_cards(
        session, [item for item in published_cards_held(session) if item.card.id in wanted]
    )


def _landed_with_uncommitted_work(session: Session) -> list[Violation]:
    """Done tickets whose live worktree still holds their own uncommitted work.

    Only trees that outlived their ticket: `worktree_lifecycle` keeps a dirty
    tree rather than delete work, so these are exactly the trees it kept.
    `entered_at` is the ticket's `resolved_at`, the landing that left them.
    """
    rows = session.exec(
        select(Ticket, Worktree)
        .join(Worktree, col(Worktree.ticket_id) == col(Ticket.id))
        .where(
            Ticket.state == TicketState.DONE,
            Worktree.state == WorktreeState.ACTIVE,
            col(Worktree.chat_session_id).is_(None),
        )
    ).all()
    found: list[Violation] = []
    for ticket, worktree in rows:
        workspace = session.get(Workspace, ticket.workspace_id)
        if workspace is None:
            continue
        try:
            paths = uncommitted_at_landing(session, ticket, workspace)
        except RuntimeError:
            # Not "clean": logged where the handler shows it, and read again
            # next pass. One unreadable tree must not hide every other one.
            logger.warning("Could not read %s's worktree", ticket.external_id, exc_info=True)
            continue
        if paths:
            found.append(
                Violation(
                    subject_id=worktree.id,
                    ticket_id=ticket.id,
                    entered_at=ticket.resolved_at,
                    detail=f"{ticket.external_id}: {len(paths)} file(s) uncommitted in "
                    f"{worktree.worktree_path}: {', '.join(paths)}",
                )
            )
    return found


EVERY_BLOCK_IS_SETTLED = Invariant(
    name="every_block_is_settled",
    ticket="lg-workflow-integrity-802",
    statement="every blocked ticket has a block kind and a recorded repair decision",
    detect=_unsettled_blocks,
    repair=_settle_blocks,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 9, 24),
    prevented_by=(
        "server/tests/test_block_settlement.py"
        "::test_every_writer_claiming_to_settle_actually_calls_the_helper"
    ),
)

EXHAUSTED_GATES_BLOCK = Invariant(
    name="exhausted_gates_block_their_ticket",
    ticket="lg-milestone-that-1432",
    statement=(
        "no ticket waits at a pending stage behind an orchestration an exhausted gate "
        "ended SUCCEEDED"
    ),
    detect=_gate_parked,
    repair=_block_gate_parked,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 23),
    prevented_by=(
        "server/tests/test_transition_gate_reroute.py"
        "::test_autofix_agent_fallback_retries_inline_then_pauses_after_max_attempts"
    ),
)

LANDING_BLOCKS_CLEAR = Invariant(
    name="landing_blocks_clear_with_their_cause",
    ticket="lg-milestone-that-928",
    statement="no ticket stays blocked by a landing that would now succeed",
    detect=_cleared_landings,
    repair=_finish_landings,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 20),
    expected_because=(
        "a landing conflict is cleared outside the control plane: resolved and merged by "
        "hand, or the target branch moved under it"
    ),
)

PUBLISH_CARDS_CLOSE = Invariant(
    name="publish_cards_close_once_landed",
    ticket="lg-milestone-that-1032",
    statement="no publish card stays pending once its base holds the tree's work",
    detect=_held_cards,
    repair=_close_cards,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 20),
    expected_because="a published PR is merged on GitHub by a person, not by the control plane",
)


def _cleared_survey_cards(session: Session) -> list[Violation]:
    """`entered_at` is the later clone or dispatch that cleared the card's cause."""
    return [
        Violation(
            subject_id=item.card.id,
            ticket_id=None,
            entered_at=item.cleared_at,
            detail=f"{item.card.title}: {item.because}",
        )
        for item in cleared_cards(session)
    ]


def _close_survey_cards(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    closed = 0
    for item in cleared_cards(session):
        if item.card.id in wanted:
            closed += close_cards(
                session, [item.card], reason=f"Closed by the survey: {item.because}."
            )
    return closed


SURVEY_CARDS_CLOSE = Invariant(
    name="survey_cards_close_with_their_cause",
    ticket="lg-reference-sources-1613",
    statement=(
        "no reference-survey exception card stays pending once a later clone or dispatch "
        "on its source cleared what it reported"
    ),
    detect=_cleared_survey_cards,
    repair=_close_survey_cards,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 27),
    prevented_by=(
        "server/tests/test_survey_card_clearing.py"
        "::test_every_success_path_closes_the_cards_it_clears"
    ),
)


def _twinned(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.ticket.id,
            ticket_id=item.ticket.id,
            entered_at=None,
            detail=f"{item.branch} holds {item.twin.on_branch[:12]}, a copy of "
            f"{item.twin.on_target[:12]} on {item.target} with none of its history",
        )
        for item in twinned_branches(session)
    ]


def _record_twins(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: recording moves a ref, and the read
    # that justified it must be this pass's.
    wanted = {v.subject_id for v in violations}
    return record_twins(
        session, [item for item in twinned_branches(session) if item.ticket.id in wanted]
    )


TICKET_BRANCHES_HOLD_HISTORY = Invariant(
    name="ticket_branches_hold_their_targets_history",
    ticket="lg-external-harness-1318",
    statement=(
        "no open ticket's branch holds a squash of its integration branch in place of "
        "the branch's history"
    ),
    detect=_twinned,
    repair=_record_twins,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 20),
    prevented_by=(
        "server/tests/test_publish_tree.py"
        "::test_the_root_pushes_its_integration_branch_and_records_the_tip"
    ),
)


def _releasable(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=None,
            detail=f"{ticket.external_id} ({ticket.state.value}) waits on nothing unmet",
        )
        for ticket in releasable_tickets(session)
    ]


def _release(session: Session, violations: Sequence[Violation]) -> int:
    return len(release_tickets(session, _tickets(session, violations)))


HELD_TICKETS_RESUME = Invariant(
    name="held_tickets_resume_when_their_prerequisites_land",
    ticket="lg-milestone-that-1315",
    statement=(
        "no ticket held or parked on prerequisites stays waiting once every one of them "
        "is done and landed"
    ),
    detect=_releasable,
    repair=_release,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 21),
    expected_because=(
        "a prerequisite landing is the event the hold waits for; nothing resumes the held "
        "ticket at that moment, so every release starts here"
    ),
)


def _temp_workspaces(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=workspace.id,
            ticket_id=None,
            entered_at=workspace.created_at,
            detail=f"workspace {workspace.slug} is on {workspace.repo_path}, a temp directory",
        )
        for workspace in temp_workspaces(session)
    ]


def _archive_temp_workspaces(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return archive_workspaces(session, [ws for ws in temp_workspaces(session) if ws.id in wanted])


LIVE_WORKSPACES_HAVE_REAL_REPOS = Invariant(
    name="live_workspaces_have_real_repositories",
    ticket="lg-milestone-that-1319",
    statement="no unarchived workspace in the live database is on a temp-directory repo",
    detect=_temp_workspaces,
    repair=_archive_temp_workspaces,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 20),
    prevented_by=(
        "server/tests/test_workspace_repo_guard.py"
        "::test_every_writer_of_a_temp_repo_to_the_live_database_is_refused"
    ),
)


def _open_workflows(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.ticket.id,
            ticket_id=item.ticket.id,
            entered_at=item.entered_at,
            detail=f"{item.ticket.external_id} is {item.ticket.state.value} with "
            f"{', '.join(item.open_stages) or 'its cursor'} open "
            f"('{item.ticket.workflow_stage_key}/{item.ticket.workflow_stage_status.value}')",
        )
        for item in open_workflows(session)
    ]


def _close_workflows(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return close_workflows(session, [w for w in open_workflows(session) if w.ticket.id in wanted])


CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS = Invariant(
    name="closed_tickets_have_closed_workflows",
    ticket="lg-workflow-integrity-1328",
    statement="no done or wont_do ticket has a stage pending, running, awaiting or blocked",
    detect=_open_workflows,
    repair=_close_workflows,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 20),
    prevented_by=(
        "server/tests/test_closed_workflow_guard.py"
        "::test_every_writer_leaves_a_closed_ticket_with_a_closed_workflow"
    ),
)


def _ancestor_waits(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.edge.id,
            ticket_id=item.dependent.id,
            entered_at=item.edge.created_at,
            detail=(f"{item.dependent.external_id} waits on ancestor {item.ancestor.external_id}"),
        )
        for item in ancestor_waits(session)
    ]


def _drop_ancestor_waits(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return drop_ancestor_waits(
        session, [item for item in ancestor_waits(session) if item.edge.id in wanted]
    )


NO_TICKET_DEPENDS_ON_AN_ANCESTOR = Invariant(
    name="no_ticket_depends_on_an_ancestor",
    ticket="lg-milestone-that-1604",
    statement=(
        "no ticket waits on its parent or any further ancestor; a parent is done only "
        "when its children are, so that edge deadlocks both"
    ),
    detect=_ancestor_waits,
    repair=_drop_ancestor_waits,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 26),
    prevented_by=("server/tests/test_ticket_dependencies.py::test_add_dependency_rejects_a_parent"),
)


def _unasked_awaits(session: Session) -> list[Violation]:
    """`entered_at` is the ticket's `updated_at`: a stage row has no timestamp of its own."""
    return [
        Violation(
            subject_id=item.ticket.id,
            ticket_id=item.ticket.id,
            entered_at=item.ticket.updated_at,
            detail=f"{item.ticket.external_id} awaits a person at '{item.stage_key}' "
            "with no pending approval asking anyone",
        )
        for item in unasked_awaits(session)
    ]


def _block_unasked_awaits(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return block_unasked_awaits(
        session, [item for item in unasked_awaits(session) if item.ticket.id in wanted]
    )


AWAITING_STAGES_ARE_ASKED = Invariant(
    name="awaiting_stages_have_a_pending_approval",
    ticket="lg-workflow-integrity-1625",
    statement=(
        "no open ticket has a stage awaiting a person without a pending approval at that "
        "stage asking them"
    ),
    detect=_unasked_awaits,
    repair=_block_unasked_awaits,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 27),
    prevented_by=(
        "server/tests/test_unasked_awaits.py"
        "::test_no_manual_writer_sets_awaiting_without_an_approval"
    ),
)


LANDED_WORK_IS_COMMITTED = Invariant(
    name="landed_work_is_committed",
    ticket="lg-milestone-that-1314",
    statement="no done ticket's worktree holds its own uncommitted work",
    detect=_landed_with_uncommitted_work,
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 21),
    prevented_by=(
        "server/tests/test_landing_uncommitted.py"
        "::test_a_ticket_with_uncommitted_work_does_not_land"
    ),
    unrepaired_action=(
        "Open the worktree the finding names. If the files are the ticket's work, commit them "
        "to its branch and land it again; if not, discard them with `git checkout -- <path>`. "
        "A clean tree is removed by the next startup's worktree sweep."
    ),
)


def _held_but_blocked(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=entry.repairing_since,
            detail=f"{ticket.external_id} reads blocked on '{ticket.workflow_stage_key}' while "
            f"lane {entry.slot_number} is held for its {entry.repair_route} repair",
        )
        for entry, ticket in held_but_blocked(session)
    ]


def _rearm_held(session: Session, violations: Sequence[Violation]) -> int:
    return rearm_held(session, _tickets(session, violations))


HELD_REPAIRS_READ_IN_PROGRESS = Invariant(
    name="held_repairs_read_in_progress",
    ticket="lg-queue-execution-1656",
    statement=(
        "no ticket whose lane is held for a repair (other than an interruption) reads blocked"
    ),
    detect=_held_but_blocked,
    repair=_rearm_held,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 29),
    prevented_by="server/tests/test_lane_repair_hold.py::test_every_lane_hold_re_arms_its_stage",
)


#: Ordered as `reconciliation.PERIODIC_STEPS` schedules them.
INVARIANTS: tuple[Invariant, ...] = (
    EXHAUSTED_GATES_BLOCK,
    EVERY_BLOCK_IS_SETTLED,
    LANDING_BLOCKS_CLEAR,
    PUBLISH_CARDS_CLOSE,
    SURVEY_CARDS_CLOSE,
    TICKET_BRANCHES_HOLD_HISTORY,
    LIVE_WORKSPACES_HAVE_REAL_REPOS,
    CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS,
    AWAITING_STAGES_ARE_ASKED,
    HELD_TICKETS_RESUME,
    NO_TICKET_DEPENDS_ON_AN_ANCESTOR,
    LANDED_WORK_IS_COMMITTED,
    HELD_REPAIRS_READ_IN_PROGRESS,
)
