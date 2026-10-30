"""Every state invariant the reconciler enforces, and the adapters that feed it.

Adding a fix for a bad state? Register it here (`services.invariants` says
why): a detector, a repair, and either the test that proves the guard
(`DEFECT`) or the reason the state is entered legitimately (`EXPECTED`).
`test_invariants.py` fails a registration missing any of them, and the reconcile
pass picks it up from `INVARIANTS` by name (`reconciliation.PERIODIC_STEPS`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime, timezone

from loregarden.models.domain import (
    AgentSlot,
    Artifact,
    ArtifactKind,
    InvariantReentry,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.block_settlement import settle_unclassified_blocks, unclassified_blocks
from loregarden.services.checkouts_without_their_branch import checkouts_without_their_branch
from loregarden.services.cleaned_worktree_rows import cleaned_rows_with_their_checkout
from loregarden.services.closed_workflow_sweep import close_workflows, open_workflows
from loregarden.services.dependency_release import releasable_tickets, release_tickets
from loregarden.services.done_ticket_ship import (
    violations_for_invariant as _recorded_ship_violations,
)
from loregarden.services.gate_parked_sweep import block_parked, parked_by_gate
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.land_verification import RECEIPT_PASSED
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.orphaned_worktree_rows import (
    rows_without_their_checkout,
    settle_rows_without_their_checkout,
)
from loregarden.services.parallel_queue import (
    last_lane_entry,
    record_ghost_reclaim,
    release_slot,
)
from loregarden.services.queue_repair import held_but_blocked, rearm_held
from loregarden.services.queue_slot_claims import claim_is_fresh, slot_is_busy_with_nothing
from loregarden.services.reference_survey.inbox import cleared_cards, close_cards
from loregarden.services.squash_twin_sweep import record_twins, twinned_branches
from loregarden.services.stale_parked_at_rows import (
    clear_vanished_parked_at,
    rows_whose_parked_at_is_gone,
)
from loregarden.services.temp_workspaces import archive_workspaces, temp_workspaces
from loregarden.services.terminal_stage_sweep import (
    finish_cleared_landings,
    tickets_whose_landing_cleared,
)
from loregarden.services.ticket_dependencies import ancestor_waits, drop_ancestor_waits
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.tree_publish_scheduler import (
    close_held_cards,
    published_cards_held,
)
from loregarden.services.unasked_awaits import block_unasked_awaits, unasked_awaits
from loregarden.services.unresolved_ledger import (
    detect_unaudited_open_shrink as _detect_open_shrink_findings,
)
from loregarden.services.unresolved_ledger import (
    repair_unaudited_open_shrink as _repair_open_shrink_findings,
)
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


def _ghost_lanes(session: Session) -> list[AgentSlot]:
    """Slots that read busy, name no occupant, and whose claim may be taken.

    The two legal ways a slot holds no ids are excluded by the same predicate
    the lane and the sweep answer from, which is what makes `DEFECT` an honest
    policy here rather than a guard that cries wolf:

    - a slot just taken by `claim_free_slot` and not yet bound — both ids are
      null on purpose, and it is inside `RESERVATION_GRACE`;
    - a slot held for repair — it names its occupant, so it is not busy with
      *nothing* at all, and the hold `reconcile_slots` recognises by that id
      survives this pass untouched.

    "Busy with an occupant that finished" is deliberately not here: that is the
    ordinary residue `reconcile_slots` has swept since before 775, and a
    detector merging the two would report a DEFECT re-entry every time a run
    exited uncleanly.
    """
    now = utcnow()
    return [
        slot
        for slot in session.exec(
            select(AgentSlot)
            .where(AgentSlot.is_available == False)  # noqa: E712
            .order_by(col(AgentSlot.slot_number))
        ).all()
        if slot_is_busy_with_nothing(slot) and not claim_is_fresh(slot, now=now)
    ]


def _busy_lanes_with_no_occupant(session: Session) -> list[Violation]:
    """`entered_at` is `assigned_at`: when the lane was taken is the only date a
    slot carries, and it is the right one — the claim is what was never undone."""
    violations: list[Violation] = []
    for slot in _ghost_lanes(session):
        last = last_lane_entry(session, slot.slot_number)
        violations.append(
            Violation(
                subject_id=slot.id,
                ticket_id=last.ticket_id if last else None,
                entered_at=slot.assigned_at,
                detail=(
                    f"lane {slot.slot_number} reads busy with no occupant "
                    f"(claimed {slot.assigned_at}); nothing can release it"
                ),
            )
        )
    return violations


def _reclaim_busy_lanes(session: Session, violations: Sequence[Violation]) -> int:
    """Give back exactly the lanes this pass detected, re-reading each first.

    The existing ghost reclaim, narrowed to these subjects — not a call to
    `reconcile_slots`, which would also reclaim rows this pass never detected
    and never reported, while the count said it repaired one. Re-detecting is
    what keeps a reservation that took the lane between the two reads: detect
    and repair are two reads of a world that moves.
    """
    wanted = {v.subject_id for v in violations}
    repaired = 0
    for slot in _ghost_lanes(session):
        if slot.id not in wanted:
            continue
        release_slot(session, slot)
        record_ghost_reclaim(session, slot)
        repaired += 1
    return repaired


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
        workspace = workspace_for_ticket(session, ticket)
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


#: When the land-verification receipt's writer landed, and so the line between
#: backlog and re-entry for `LAND_VERIFICATION_RAN`. One constant, shared by the
#: registration and by the detector's own bound: two copies is two answers to
#: "what is backlog?", and the one that drifts is the detector's — which is the
#: one that decides whether eighty old landings are reported.
#:
#: Measured on the live database read-only 2026-10-23: 1,844 tickets, 474
#: `done`, 80 `done` with a `landed_sha`, and 0 land-verification receipts of any
#: kind, because no landing had ever written one. Unbounded, the first reconcile
#: tick after boot would upsert 80 `invariant_unrepaired` findings and
#: auto-create one escalation Bug listing all 80 by external id — and
#: `run_invariant` re-reports the pre-`since` backlog on EVERY pass for a
#: report-only invariant, so it is not self-limiting.
#:
#: The date is the day AFTER this change lands, not the day of it. A landing
#: earlier on the SAME day predates the writer by hours and has no receipt, so a
#: bound of the landing day reports it as re-entry: measured against a copy of
#: the live database, six loregarden landings from 00:29 to 07:28 on 2026-10-23
#: fell at or after a 2026-10-23 bound, which is six findings and an escalation
#: Bug on the first pass where AC10 requires zero. `Invariant.since` is a date
#: and `invariants._since` reads it as that day's midnight, so the first whole
#: day every landing can carry a receipt is the first day in range.
LAND_VERIFICATION_SINCE = date(2026, 10, 24)


def _has_a_passing_receipt(bodies: Sequence[str | None]) -> bool:
    """Whether any of these receipt bodies says the suite passed.

    `content_json` is a text column every `loregarden_attach_artifact` caller
    and every hand repair can write, so this is a `json.loads` and a subscript
    over operator-supplied text. A body that cannot be read is NOT a passing
    verdict and must not take the pass down with it: `run_invariant` is one step
    of `PERIODIC_STEPS`, and a detector that raises reports nothing for any
    ticket from then on while the surface says "no violations" — a guard failing
    that reads exactly like a guard working.

    The comparison is exact. `status` is one of three words this ticket's own
    writer controls and the client's `PASSING` set matches them exactly, so
    `.strip().lower()` here would be defensive normalization — and would clear a
    landing whose receipt the client itself colours `warn`.
    """
    for body in bodies:
        try:
            status = json.loads(body or "").get("status")
        except (ValueError, AttributeError):
            # Unreadable, or JSON that is not an object: no verdict, so this
            # receipt clears nothing and the landing stays reported below.
            continue
        if status == RECEIPT_PASSED:
            return True
    return False


def _landed_without_a_verified_suite(session: Session) -> list[Violation]:
    """Done tickets whose recorded landing has no passing land-verification receipt.

    Pure database reads (plus the workspace's own orchestration profile): `state
    == DONE`, a non-empty `landed_sha`, no `ArtifactKind.LAND_VERIFICATION`
    artifact whose `content_json.status` is `passed`, and a `resolved_at` at or
    after `LAND_VERIFICATION_SINCE`. It runs no suite and shells out to nothing,
    deliberately: the reconcile tick is 30s, the suite is 901s, collection alone
    is 21s, and `test_invariants.py` runs every detector inside pytest. R3's
    receipt is the record this reads instead.

    Three conditions are easy to misread, so all three are stated:

    - A ticket with no `landed_sha` is never a violation. There is no landing to
      judge; the Outputs tab shows its "No outputs yet" empty state, and a
      detector that read that as a failure would report every done ticket closed
      without landing.
    - An UNDATED landing (`resolved_at is None`) is left alone. That INVERTS
      `invariants._entered_after`'s documented "unknown entry time counts as
      re-entry" default, on purpose: `resolved_at` is stamped when a ticket
      resolves, so a landing this detector cannot date is by construction one
      that predates the receipt writer and `since`. Erring towards silence is
      the opposite of the framework's default, which is why it says so here
      rather than leaving a reader to meet it as a bug.
    - A landing in a workspace that verifies NOTHING is not a per-landing
      violation, because the invariant's `DEFECT` re-entry says "a writer is
      supposed to make this unreachable" and in that workspace no writer is.
      `land_verifier` returns `GateOutcome.DISABLED` when `land_verify_commands`
      is empty and `record_land_verification` writes that verdict as `disabled`,
      never `passed` — so every landing in a workspace on `default.yaml`'s empty
      list would be a permanent violation that report-only never clears, one
      finding per landing and one escalation Bug per workspace, for a workspace
      sitting in its documented off state.

      That off state is still reported, once: `_workspace_verifies_no_landing`
      is ONE workspace-scoped violation naming the workspace and how many
      landings it covers. One finding, not N, and emptying
      `land_verify_commands` therefore cannot make this detector silent — which
      is the blindness the plan stage warned the substitute detector had.

    Condition "no PASSING receipt" is not "no receipt": a verified landing
    re-landed later writes a `disabled`/`already_contained` row beside the
    passing one, and reading "the latest receipt" would turn that into a
    violation.

    `entered_at` is the ticket's `resolved_at` — `Ticket` carries no landing
    timestamp, and `_landed_with_uncommitted_work` makes the same choice for the
    same reason.
    """
    since = datetime(
        LAND_VERIFICATION_SINCE.year,
        LAND_VERIFICATION_SINCE.month,
        LAND_VERIFICATION_SINCE.day,
        tzinfo=timezone.utc,
    )
    landed = [
        ticket
        for ticket in session.exec(
            select(Ticket).where(
                col(Ticket.state) == TicketState.DONE,
                col(Ticket.landed_sha) != "",
            )
        ).all()
        if _resolved_at_or_after(ticket, since)
    ]
    if not landed:
        return []
    by_workspace: dict[str | None, list[Ticket]] = {}
    for ticket in landed:
        by_workspace.setdefault(ticket.workspace_id, []).append(ticket)
    violations: list[Violation] = []
    judged: list[Ticket] = []
    for tickets in by_workspace.values():
        workspace = workspace_for_ticket(session, tickets[0])
        if workspace is not None and not resolve_git_automation(workspace).land_verify_commands:
            violations.append(_workspace_verifies_no_landing(workspace, tickets))
            continue
        # A landing with no workspace row to resolve a profile from is judged
        # per-landing: its arming cannot be read either way, and the framework's
        # default for what a detector cannot determine is to report it.
        judged.extend(tickets)
    if not judged:
        return violations
    receipts: dict[str, list[str | None]] = {}
    for ticket_id, body in session.exec(
        select(col(Artifact.ticket_id), col(Artifact.content_json)).where(
            col(Artifact.kind) == ArtifactKind.LAND_VERIFICATION,
            col(Artifact.ticket_id).in_([ticket.id for ticket in judged]),
        )
    ).all():
        receipts.setdefault(ticket_id, []).append(body)
    violations.extend(
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=ticket.resolved_at,
            detail=(
                f"{ticket.external_id} landed on {ticket.landed_branch} at "
                f"{ticket.landed_sha[:12]} with no passing land_verification receipt"
            ),
        )
        for ticket in judged
        if not _has_a_passing_receipt(receipts.get(ticket.id, []))
    )
    return violations


def _workspace_verifies_no_landing(workspace: Workspace, landings: Sequence[Ticket]) -> Violation:
    """The one violation for a workspace whose profile verifies no landing at all.

    Workspace-scoped by `subject_id`, so `record_findings` upserts one finding
    and `escalate_invariant_reentries` files one Bug however many landings the
    workspace has. It still has to hang on a ticket (`artifacts.ticket_id` is
    NOT NULL, and `invariants._report` only logs a ticketless violation), so it
    hangs on the most recent landing it covers, named in the detail beside the
    count — the landing an operator would open first.
    """
    most_recent = max(landings, key=lambda ticket: (_at_utc(ticket.resolved_at), ticket.id))
    return Violation(
        subject_id=workspace.id,
        ticket_id=most_recent.id,
        entered_at=most_recent.resolved_at,
        detail=(
            f"workspace {workspace.slug} verifies no landing: land_verify_commands is empty "
            f"in its orchestration profile, so each of its {len(landings)} landing(s) since "
            f"{LAND_VERIFICATION_SINCE.isoformat()} wrote a 'disabled' receipt and none can "
            f"ever pass (most recent: {most_recent.external_id} on "
            f"{most_recent.landed_branch} at {most_recent.landed_sha[:12]})"
        ),
    )


def _resolved_at_or_after(ticket: Ticket, since: datetime) -> bool:
    """Whether the ticket resolved at or after `since`; False when undated."""
    if ticket.resolved_at is None:
        return False
    return _at_utc(ticket.resolved_at) >= since


def _at_utc(value: datetime | None) -> datetime:
    """One timestamp comparable with another: SQLite hands back naive UTC, and a
    row written in the same process is aware, so ordering two landings without
    this raises rather than answering."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


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
    statement="no publish or UX-answers card stays pending once its base holds the tree's work",
    detect=_held_cards,
    repair=_close_cards,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 20),
    expected_because="a published PR is merged on GitHub by a person, not by the control plane",
)

EVERY_BUSY_LANE_HAS_AN_OCCUPANT = Invariant(
    name="every_busy_lane_has_an_occupant",
    ticket="lg-milestone-that-775",
    statement="no execution slot reads busy with no occupant",
    detect=_busy_lanes_with_no_occupant,
    repair=_reclaim_busy_lanes,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 9, 21),
    prevented_by=(
        "server/tests/test_queue_lanes.py"
        "::test_completion_releases_the_slot_the_entry_names_even_with_its_ids_cleared"
    ),
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


def _detect_unaudited_open_shrink(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.ticket_id,
            ticket_id=item.ticket_id,
            entered_at=item.entered_at,
            detail=item.detail,
        )
        for item in _detect_open_shrink_findings(session)
    ]


def _repair_unaudited_open_shrink(session: Session, violations: Sequence[Violation]) -> int:
    return _repair_open_shrink_findings(
        session, [item.ticket_id for item in violations if item.ticket_id]
    )


UNRESOLVED_OPEN_SHRINK_IS_AUDITED = Invariant(
    name="unresolved_open_shrink_is_audited",
    ticket="lg-verdict-outcome-136",
    statement=(
        "a name present on the previous unresolved_open snapshot and absent from the "
        "latest must have an unresolved_clear or unresolved_waiver row between them"
    ),
    detect=_detect_unaudited_open_shrink,
    repair=_repair_unaudited_open_shrink,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 28),
    prevented_by=(
        "server/tests/test_stage_verdict_writers.py::test_every_unresolved_writer_persists"
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
        "server/tests/test_stage_end_commit.py"
        "::test_succeeded_writing_stage_is_on_head_before_next_stage"
    ),
    unrepaired_action=(
        "Open the worktree the finding names. If the files are the ticket's work, commit them "
        "to its branch and land it again; if not, discard them with `git checkout -- <path>`. "
        "A clean tree is removed by the next startup's worktree sweep."
    ),
)

LAND_VERIFICATION_RAN = Invariant(
    name="land_verification_ran",
    ticket="lg-milestone-that-1419",
    statement="every recorded landing has a passing land-verification receipt",
    detect=_landed_without_a_verified_suite,
    # Report-only: only a person can run a suite and judge what it says. A
    # repair here would be the control plane deciding its own landing passed.
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=LAND_VERIFICATION_SINCE,
    prevented_by=(
        "server/tests/test_land_verification_receipt.py"
        "::test_every_landing_that_records_a_landed_sha_writes_a_receipt"
    ),
    unrepaired_action=(
        "Run `uv run --directory server pytest -q -n auto` against the ticket's landed "
        "branch. If it is red, reopen the ticket with the failing test named; if it is "
        "green, record the verdict on the ticket."
    ),
)


def _rows_without_their_checkout(session: Session) -> list[Violation]:
    """`entered_at` is None: nothing stamps the moment a checkout left.

    `created_at` is when the row was cut, which is not when the directory went
    — and a `DEFECT`-style "entered after the guard landed" reading off it
    would call every pre-existing row a re-entry. The policy here is
    `EXPECTED` anyway, so the state is repaired rather than dated.

    The finding names the path, because that is what a person goes and looks
    at — and the row and its ticket too, because a row recording no path at
    all is also a violation, and "go and look at ''" is not something anyone
    can act on.
    """
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=None,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) is active and records a "
            f"checkout at '{row.worktree_path}', which is not there",
        )
        for row in rows_without_their_checkout(session)
    ]


def _settle_rows_without_their_checkout(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: settling a row is a write, and the
    # reading that justified it must be this pass's.
    wanted = {v.subject_id for v in violations}
    return settle_rows_without_their_checkout(
        session, [row for row in rows_without_their_checkout(session) if row.id in wanted]
    )


ACTIVE_WORKTREE_ROWS_HAVE_THEIR_CHECKOUT = Invariant(
    name="active_worktree_rows_have_their_checkout",
    ticket="lg-external-harness-1452",
    statement="no active worktrees row records a checkout that is not on disk",
    detect=_rows_without_their_checkout,
    repair=_settle_rows_without_their_checkout,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 23),
    expected_because=(
        "an operator removes a checkout by hand, outside the control plane — `git worktree "
        "remove` in a terminal leaves the row with nothing to speak for, and no writer can "
        "guard against it"
    ),
)


def _rows_whose_parked_at_is_gone(session: Session) -> list[Violation]:
    """`entered_at` is None: nothing stamps the moment a park copy left.

    The finding names both paths, because that is what a person goes and looks
    at — and the row and its ticket too, because a vanished locator with no
    ticket attached is still a false record.
    """
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=None,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) records parked_at "
            f"'{row.parked_at}', which is gone, while the checkout at "
            f"'{row.worktree_path}' is present",
        )
        for row in rows_whose_parked_at_is_gone(session)
    ]


def _clear_vanished_parked_at(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: clearing parked_at is a write, and
    # the reading that justified it must be this pass's. A PRESENT parked_at
    # at repair time is skipped — that locator is live.
    wanted = {v.subject_id for v in violations}
    return clear_vanished_parked_at(
        session, [row for row in rows_whose_parked_at_is_gone(session) if row.id in wanted]
    )


WORKTREE_PARKED_AT_NAMES_A_LIVE_PATH = Invariant(
    name="worktree_parked_at_names_a_live_path",
    ticket="lg-external-harness-1416",
    statement="no worktrees row records a parked_at that is gone while its checkout is present",
    detect=_rows_whose_parked_at_is_gone,
    repair=_clear_vanished_parked_at,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 25),
    expected_because=(
        "directory_release._restore's ERROR record asks a person to move it back by hand "
        "before the next sweep; doing that leaves parked_at naming a path that is gone "
        "while worktree_path is present"
    ),
    prevented_by=(
        "server/tests/test_worktree_lifecycle.py"
        "::test_every_writer_of_parked_at_sets_it_only_from_an_unrestored_release"
    ),
)


def _cleaned_rows_with_their_checkout(session: Session) -> list[Violation]:
    """`entered_at` is the row's `cleaned_at`: the moment a writer said it was gone."""
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=row.cleaned_at,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) is cleanup and records a "
            f"checkout at '{row.worktree_path}', which is still there and owned by no row",
        )
        for row in cleaned_rows_with_their_checkout(session)
    ]


CLEANED_WORKTREE_ROWS_LEAVE_NO_CHECKOUT = Invariant(
    name="cleaned_worktree_rows_leave_no_checkout",
    ticket="lg-external-harness-954",
    statement="no cleanup worktrees row records a checkout that is still on disk",
    detect=_cleaned_rows_with_their_checkout,
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 16),
    prevented_by=(
        "server/tests/test_remove_checkout_refusal.py"
        "::test_a_removal_that_raises_answers_remove_raised"
    ),
    unrepaired_action=(
        "Open the directory the finding names. If it holds work, commit or move it; then remove "
        "it (`git worktree remove --force <path>`, or delete the directory). Nothing owns it, so "
        "no sweep will, and the finding clears once it is gone."
    ),
)


def _checkouts_without_their_branch(session: Session) -> list[Violation]:
    """`entered_at` is None: nothing stamps the moment a branch was deleted."""
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=None,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) has a checkout at "
            f"'{row.worktree_path}' but its branch '{row.branch}' no longer exists",
        )
        for row in checkouts_without_their_branch(session)
    ]


CHECKOUTS_KEEP_THEIR_BRANCH = Invariant(
    name="checkouts_keep_their_branch",
    ticket="lg-external-harness-1166",
    statement="no worktrees row has a checkout on disk whose recorded branch is gone",
    detect=_checkouts_without_their_branch,
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 16),
    prevented_by=(
        "server/tests/test_stage_fanout_service.py"
        "::test_a_losing_branch_survives_a_failed_worktree_removal"
    ),
    unrepaired_action=(
        "Open the checkout the finding names. If its HEAD holds commits you want, recreate the "
        "branch there (`git branch <name> HEAD`); otherwise remove the checkout. Nothing else "
        "references those commits, so the next `git gc` may take them."
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


DONE_TICKET_SHIP_IS_OBSERVABLE = Invariant(
    # Spelled out, not imported: `pr_fix_coverage_check` reads names off this
    # file without importing it. test_done_ticket_ship finds it by
    # `done_ticket_ship.INVARIANT_NAME`, so the two cannot drift.
    name="done_ticket_ship_is_observable",
    ticket="lg-verdict-outcome-1630",
    statement=(
        "no done ticket's carrying pull request is open with a failing or pending required "
        "check, and no done ticket's published branch never reached a remote"
    ),
    # The recorded probe, not a live `gh` pass: `reconciliation` asks GitHub once
    # per tick through `done_ticket_ship`, and a probe that could not ask
    # contributes nothing here rather than reading as every ship green.
    detect=_recorded_ship_violations,
    repair=None,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 28),
    expected_because=(
        "a ticket's state is derived from its stage map alone, so done is reached before any "
        "remote check has run; the gate stage grades a local worktree"
    ),
    unrepaired_action=(
        "Open the pull request the finding names and fix or merge it; if the finding says the "
        "branch never reached remote, push the published branch from the ticket's PR tab."
    ),
)


#: Ordered as `reconciliation.PERIODIC_STEPS` schedules them.
INVARIANTS: tuple[Invariant, ...] = (
    EVERY_BUSY_LANE_HAS_AN_OCCUPANT,
    HELD_REPAIRS_READ_IN_PROGRESS,
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
    LAND_VERIFICATION_RAN,
    ACTIVE_WORKTREE_ROWS_HAVE_THEIR_CHECKOUT,
    WORKTREE_PARKED_AT_NAMES_A_LIVE_PATH,
    CLEANED_WORKTREE_ROWS_LEAVE_NO_CHECKOUT,
    CHECKOUTS_KEEP_THEIR_BRANCH,
    UNRESOLVED_OPEN_SHRINK_IS_AUDITED,
    DONE_TICKET_SHIP_IS_OBSERVABLE,
)
