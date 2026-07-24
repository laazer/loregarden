"""Adversarial / edge-case tests for `loregarden_create_ticket` (a9-create-ticket-mcp-tool).

Written during the test-break stage, before the implementation stage exists. All tests
here are expected to fail red today (either `ImportError`/"Unknown tool", or by failing
to import `TicketService`/`Workspace`) — that is the correct state until the handler is
registered in `loregarden/mcp/tools.py`.

These tests intentionally go past the happy-path/validation-delegation coverage already
pinned in `test_mcp_create_ticket.py` into dimensions the Test Breaker checklist calls
out specifically: concurrency/race conditions, boundary values, null/empty edge cases,
and determinism. See individual docstrings for the failure mode each test targets.
"""

from __future__ import annotations

import json
import threading

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from sqlmodel import Session, select


def _create(session, args: dict) -> dict:
    normalized = normalize_tool_arguments("loregarden_create_ticket", args)
    return json.loads(execute_tool(session, "loregarden_create_ticket", normalized))


def _milestone(session) -> Ticket:
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    milestone = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.work_item_type == WorkItemType.MILESTONE,
        )
    ).first()
    assert milestone, "seed produced no milestone to use as a parent"
    return milestone


# --- concurrency / race conditions ------------------------------------------
#
# TicketService.create_ticket serializes through a module-level `_create_ticket_lock`,
# a strong signal that concurrent duplicate-external_id creation is a known real hazard
# in this codebase, not a hypothetical. Any per-request-session wiring the MCP handler
# adds must not reopen that race (e.g. by reading "does this external_id exist" outside
# the lock, or by committing on a session the lock doesn't actually serialize around).


def test_concurrent_creates_with_the_same_explicit_external_id_never_duplicate(client, isolated_db):
    """Fire two concurrent MCP creates with an identical explicit external_id at the
    same milestone. Exactly one must win; the loser must get the clean duplicate-slug
    ValueError already pinned in test_mcp_create_ticket.py — not a second row, not a
    silently swallowed exception, not an unhandled IntegrityError from racing past the
    duplicate check before either commit lands."""
    with Session(isolated_db) as setup_session:
        milestone_id = _milestone(setup_session).id

    outcomes: list[tuple[str, object]] = []
    barrier = threading.Barrier(2)

    def attempt(n: int) -> None:
        barrier.wait(timeout=5)
        with Session(isolated_db) as session:
            try:
                result = _create(
                    session,
                    {
                        "workspace_slug": "loregarden",
                        "title": f"Racer {n}",
                        "work_item_type": "feature",
                        "parent": milestone_id,
                        "external_id": "racer-shared-slug",
                    },
                )
                outcomes.append(("ok", result))
            except ValueError as exc:
                outcomes.append(("error", str(exc)))
            except Exception as exc:  # noqa: BLE001 - deliberately catching anything
                outcomes.append(("crash", exc))

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(outcomes) == 2, "both racing attempts must resolve, not hang"
    kinds = [kind for kind, _ in outcomes]
    assert "crash" not in kinds, f"racing creates must not raise unhandled errors: {outcomes}"
    assert kinds.count("ok") == 1, f"exactly one racer should win: {outcomes}"
    assert kinds.count("error") == 1, f"exactly one racer should get a clean error: {outcomes}"
    loser_message = next(msg for kind, msg in outcomes if kind == "error")
    assert "external_id already exists" in loser_message, loser_message

    with Session(isolated_db) as verify_session:
        rows = verify_session.exec(
            select(Ticket).where(Ticket.external_id == "racer-shared-slug")
        ).all()
        assert len(rows) == 1, f"duplicate external_id rows were persisted: {rows}"


# --- boundary conditions -----------------------------------------------------


@pytest.mark.parametrize("priority", [1, 3])
def test_priority_at_valid_boundaries_is_accepted(client, db_session, priority):
    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": f"Priority boundary {priority}",
            "work_item_type": "feature",
            "parent": milestone.id,
            "priority": priority,
        },
    )
    stored = db_session.get(Ticket, result["id"])
    assert stored.priority == priority


@pytest.mark.parametrize("priority", [0, -1, 4])
def test_priority_outside_valid_range_is_rejected(client, db_session, priority):
    """The pinned regression test in test_mcp_create_ticket.py only tries priority=9
    (comfortably out of range). Zero, negative, and the off-by-one upper boundary are
    the values most likely to slip through an off-by-one bounds check."""
    milestone = _milestone(db_session)
    with pytest.raises(ValueError, match="Priority must be between 1 and 3"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": f"Bad priority {priority}",
                "work_item_type": "feature",
                "parent": milestone.id,
                "priority": priority,
            },
        )


# --- null / empty values ------------------------------------------------------


def test_whitespace_only_title_is_rejected_like_an_empty_one(client, db_session):
    """TicketService strips title before checking it's non-empty. A whitespace-only
    title must hit the same "Title is required" rejection as an empty string — a
    handler that only checks `if not title` before stripping would let this through."""
    milestone = _milestone(db_session)
    with pytest.raises(ValueError, match="Title is required"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "   \t  ",
                "work_item_type": "feature",
                "parent": milestone.id,
            },
        )


def test_explicit_empty_string_parent_is_treated_as_no_parent(client, db_session):
    """`parent: ""` is a plausible payload from a client that always sends the field.
    It must behave exactly like omitting `parent` (i.e. this feature request is
    rejected for missing a parent) rather than the MCP layer trying to resolve the
    empty string as a ticket reference and raising a confusing "not found" error."""
    with pytest.raises(ValueError, match="requires a parent work item"):
        _create(
            db_session,
            {
                "workspace_slug": "loregarden",
                "title": "Empty string parent",
                "work_item_type": "feature",
                "parent": "",
            },
        )


def test_whitespace_only_explicit_external_id_falls_back_to_auto_slug(client, db_session):
    """TicketService computes `external_id.strip() or _next_external_id(...)` — a
    caller sending external_id="   " must land on auto-slugging, not on a ticket
    literally titled with a blank external_id."""
    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "Whitespace external id",
            "work_item_type": "feature",
            "parent": milestone.id,
            "external_id": "   ",
        },
    )
    assert result["external_id"].strip() == result["external_id"]
    assert result["external_id"] != ""


def test_empty_acceptance_criteria_list_round_trips_as_empty_not_none(client, db_session):
    """Sending acceptance_criteria=[] explicitly (clearing the field) must not be
    conflated with "not provided" and default to something else."""
    from loregarden.services.acceptance_criteria import load_criteria

    milestone = _milestone(db_session)
    result = _create(
        db_session,
        {
            "workspace_slug": "loregarden",
            "title": "Explicit empty acceptance criteria",
            "work_item_type": "feature",
            "parent": milestone.id,
            "acceptance_criteria": [],
        },
    )
    stored = db_session.get(Ticket, result["id"])
    assert load_criteria(stored.acceptance_criteria_json) == []


# --- determinism ---------------------------------------------------------------


def test_milestone_with_parent_rejection_message_is_identical_across_repeated_calls(
    client, db_session
):
    """Run the same invalid request twice; the rejection must be byte-identical both
    times. A handler that builds its error message from something unordered (e.g. a
    dict/set of validation issues) could pass once and fail intermittently."""
    milestone = _milestone(db_session)
    messages = []
    for _ in range(2):
        with pytest.raises(ValueError) as excinfo:
            _create(
                db_session,
                {
                    "workspace_slug": "loregarden",
                    "title": "Repeated bad milestone",
                    "work_item_type": "milestone",
                    "parent": milestone.id,
                },
            )
        messages.append(str(excinfo.value))
    assert messages[0] == messages[1], messages
