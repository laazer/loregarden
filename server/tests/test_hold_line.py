"""lg-milestone-that-919: the hold line is composed against the width it is stored in.

The defect: `_name_prerequisites` joined *every* unmet prerequisite, and
`_finish_orchestration_run` then stored `message[:2000]`. Measured on the live
maximum (one dependent, 18 done-but-unlanded prerequisites) the 14th clause
ended `land it: loregarden_land_ticket lg-milestone-` and four prerequisites
vanished with no indication at all.

Two invariants, one module (`services/hold_line.py`):

* `truncate_for_storage` — the cut is token-safe, or it does not fire (R2);
* `render_hold_line` — the line is composed against `ERROR_MESSAGE_LIMIT`, so
  the cut has nothing to do (R3-R5).

These are unit tests by design: R1-AC3 requires both to be exercisable without
driving `BuiltinOrchestrator.execute`. The end-to-end proof at real volume lives
in `test_auto_mode_subtree.py` (R6).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from loregarden.models.domain import Ticket, TicketState, Workspace
from loregarden.services.dependency_readiness import UnmetPrerequisite, UnmetReason
from loregarden.services.hold_line import (
    CUT_MARKER,
    ERROR_MESSAGE_LIMIT,
    WAITING_NAMES,
    HeldChild,
    render_hold_line,
    truncate_for_storage,
)
from sqlmodel import Session
from tests.hold_line_helpers import (
    assert_plain_single_line,
    assert_reconciles,
    parse_hold_line,
)

#: Live maxima, measured read-only against the database 2026-10-13 (R6-AC1).
LIVE_ID_WIDTH = 29
LIVE_SLUG_WIDTH = 10
LIVE_TITLE_WIDTH = 127
#: A target branch at the width the live fixture resolves to.
LIVE_TARGET = "integration/lg-milestone-that-491"


def _pad(stem: str, width: int, filler: str = "z") -> str:
    assert len(stem) <= width, stem
    return stem + filler * (width - len(stem))


def _workspace(db_session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug)
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    return workspace


def _ticket(
    workspace_id: str, external_id: str, *, title: str = "Prerequisite", state=TicketState.BACKLOG
) -> Ticket:
    """An unpersisted ticket. `render_hold_line` reads fields and resolves the
    prerequisite's workspace slug by id; it never queries tickets."""
    return Ticket(external_id=external_id, workspace_id=workspace_id, title=title, state=state)


def _not_landed(workspace_id: str, external_id: str, *, target: str = LIVE_TARGET):
    return UnmetPrerequisite(
        _ticket(workspace_id, external_id, state=TicketState.DONE), UnmetReason.NOT_LANDED, target
    )


def _not_done(workspace_id: str, external_id: str, *, state=TicketState.BACKLOG):
    return UnmetPrerequisite(_ticket(workspace_id, external_id, state=state), UnmetReason.NOT_DONE)


# ---------------------------------------------------------------------------
# R1: one module owns the stored width
# ---------------------------------------------------------------------------

_SERVICES = Path(__import__("loregarden").__file__).resolve().parent / "services"


def test_the_module_exports_the_width_the_marker_and_the_cap() -> None:
    """R1-AC1. Both ends of the invariant import the same three values."""
    assert ERROR_MESSAGE_LIMIT == 2000
    assert CUT_MARKER == " …[cut]"
    assert len(CUT_MARKER) == 7
    assert WAITING_NAMES == 5


def test_held_child_pairs_a_ticket_with_its_unmet_prerequisites() -> None:
    """R1-AC1/R1-AC3. `_partition_children`'s `waiting` element type."""
    workspace_id = "w"
    entry = _not_done(workspace_id, "up-01")
    held = HeldChild(ticket=_ticket(workspace_id, "dep-01"), unmet=[entry])

    assert held.ticket.external_id == "dep-01"
    assert held.unmet == [entry]
    with pytest.raises((AttributeError, TypeError)):
        held.ticket = _ticket(workspace_id, "other")  # frozen (R1-AC1)


def test_hold_line_is_a_leaf_under_the_two_modules_that_import_it() -> None:
    """R1-AC1. A leaf is the only placement with one copy of the constant: a
    reverse import would be a cycle, since `builtin_orchestrator` imports
    `orchestration_callbacks`."""
    source = (_SERVICES / "hold_line.py").read_text()
    assert "builtin_orchestrator" not in source, source
    assert "orchestration_callbacks" not in source, source


def test_the_width_literal_exists_once_across_the_three_modules() -> None:
    """R1-AC2 / R2-AC5, scoped to the modules that own this invariant.

    The criterion's `grep -rn "2000" server/loregarden/services/` is narrowed
    here: 15 unrelated modules in that tree hold their own 2000 (a triage
    message cap, `blocking_issues[:2000]`, `reply_cap=12000`) and rewriting
    them is outside this ticket. What the criterion is about is the
    `error_message` width, and these are the three files that knew it.
    """
    for name in ("builtin_orchestrator.py", "orchestration_callbacks.py"):
        source = (_SERVICES / name).read_text()
        assert "2000" not in source, (
            f"{name} must not restate the width — import ERROR_MESSAGE_LIMIT (R1-AC2)"
        )
    assert "2000" in (_SERVICES / "hold_line.py").read_text()


def test_the_single_storage_site_applies_the_shared_truncation() -> None:
    """R1-AC2. The one place the limit is applied."""
    source = (_SERVICES / "orchestration_callbacks.py").read_text()
    assert "run.error_message = truncate_for_storage(message)" in source, source


def test_the_moved_names_are_gone_from_the_orchestrator() -> None:
    """R1-AC3, posture internal: moved, not copied, with no shim left behind."""
    source = (_SERVICES / "builtin_orchestrator.py").read_text()
    for moved in ("_WAITING_NAMES", "def _name_prerequisites", "def _why"):
        assert moved not in source, f"{moved} moved to hold_line.py (R1-AC3)"


def test_the_renderer_opens_no_new_user_visible_surface() -> None:
    """R7-AC2. No path added here writes `ticket.blocking_issues`."""
    source = (_SERVICES / "hold_line.py").read_text()
    assert "blocking_issues" not in source, source
    assert "record_blocking_issue" not in source, source


# ---------------------------------------------------------------------------
# R2: truncate_for_storage — the cut is token-safe or it does not fire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length", [0, 1, 500, ERROR_MESSAGE_LIMIT - 1, ERROR_MESSAGE_LIMIT])
def test_the_cut_is_a_no_op_when_the_message_fits(length: int) -> None:
    """R2-AC1. Including exactly at the limit, and the empty string."""
    message = "a" * length
    result = truncate_for_storage(message)

    assert result == message
    assert CUT_MARKER not in result


def test_a_message_one_character_over_is_cut_and_marked() -> None:
    """R2-AC2. Bounded, and the reader can see that it was cut."""
    result = truncate_for_storage("word " * 500)  # 2500 characters

    assert len(result) <= ERROR_MESSAGE_LIMIT
    assert result.endswith(CUT_MARKER)


def test_the_cut_lands_on_the_last_separator_and_drops_it() -> None:
    """R2-AC3, pinned concretely. `keep` is 1993; the last separator at or
    before it is the space at index 1991, and the `;` it follows is stripped."""
    message = "A" * 1990 + "; " + "B" * 500

    assert truncate_for_storage(message) == "A" * 1990 + CUT_MARKER


def test_the_cut_never_lands_inside_a_bracketed_clause() -> None:
    """R2-AC3. The separator inside `[…]` is not a candidate, so the cut falls
    back to the space before the group opened."""
    message = "A" * 1980 + " [tok " + "C" * 100 + "]" + " tail" * 100
    result = truncate_for_storage(message)

    assert result == "A" * 1980 + CUT_MARKER
    # CUT_MARKER contributes the only bracket pair left in the result.
    assert result.count("[") == result.count("]") == 1


def test_a_message_with_no_separator_at_all_is_still_bounded() -> None:
    """R2 step 4, the degenerate branch (A4). No message reaching this site has
    a 1993-character token, but the function must not depend on that."""
    result = truncate_for_storage("A" * 2500)

    assert result == "A" * (ERROR_MESSAGE_LIMIT - len(CUT_MARKER)) + CUT_MARKER
    assert len(result) == ERROR_MESSAGE_LIMIT


def test_the_cut_introduces_no_newline() -> None:
    """R2 constraint: no newline the message did not contain."""
    assert "\n" not in truncate_for_storage("word " * 500)


def test_no_identifier_is_ever_truncated_mid_token() -> None:
    """R2-AC4, the defect itself: the 14th clause ended
    `land it: loregarden_land_ticket lg-milestone-`."""
    ids = [_pad(f"lg-milestone-that-919-p{n:02d}", LIVE_ID_WIDTH) for n in range(1, 31)]
    slug = _pad("live-ws-aa", LIVE_SLUG_WIDTH, "a")
    message = "Child ticket waiting on a prerequisite (1): " + ", ".join(
        f"{slug}:{identifier} [done, not landed on {LIVE_TARGET}; "
        f"land it: loregarden_land_ticket {identifier}]"
        for identifier in ids
    )
    assert len(message) > ERROR_MESSAGE_LIMIT, "the fixture must reach the cut"

    result = truncate_for_storage(message)

    assert len(result) <= ERROR_MESSAGE_LIMIT
    assert result.count("[") == result.count("]")
    for tool in ("loregarden_land_ticket", "loregarden_get_ticket"):
        index = result.find(tool)
        while index != -1:
            tail = result[index + len(tool) :]
            assert tail.startswith(" "), f"{tool} must keep its argument: {result[-80:]!r}"
            named = tail[1:].split("]")[0].split(",")[0].split(" ")[0]
            assert named in ids, f"{tool} named a partial id {named!r}: {result[-80:]!r}"
            index = result.find(tool, index + 1)


# ---------------------------------------------------------------------------
# R3: the hold line's shape
# ---------------------------------------------------------------------------


def test_one_child_with_one_prerequisite_reads_exactly_as_specified(
    db_session: Session,
) -> None:
    """R3-AC1 / R3-AC4. No count-then-elide wording where there is nothing to
    elide, and the only change from today's line is the child's id (A5/A6)."""
    upstream = _workspace(db_session, "upstream")
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title="Dependent child"),
        unmet=[_not_done(upstream.id, "up-01")],
    )

    message = render_hold_line(db_session, [held])

    assert message == (
        "Child ticket waiting on a prerequisite (1): "
        "Dependent child [dep-01] (waits for upstream:up-01 [backlog])"
    )
    assert_plain_single_line(message)
    assert "more" not in message


def test_nothing_held_says_nothing(db_session: Session) -> None:
    """R3-AC2 / R3-AC4. A hold line never reports a count of 0."""
    assert render_hold_line(db_session, []) == ""


def test_a_not_landed_prerequisite_carries_the_remedy_verbatim(db_session: Session) -> None:
    """R3-AC1 / R3-AC6. `_why` is byte-identical to today's output: the remedy
    is not reshaped, because `loregarden_land_ticket` takes exactly one
    ticket_id (mcp/ticket_ops_tools.py:507-520)."""
    upstream = _workspace(db_session, "upstream")
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title="Dependent child"),
        unmet=[_not_landed(upstream.id, "lg-milestone-that-715", target="integration/x")],
    )

    message = render_hold_line(db_session, [held])

    assert message.endswith(
        "(waits for upstream:lg-milestone-that-715 "
        "[done, not landed on integration/x; "
        "land it: loregarden_land_ticket lg-milestone-that-715])"
    ), message


def test_a_prerequisite_whose_workspace_row_is_missing_renders_a_question_mark(
    db_session: Session,
) -> None:
    """R3 / K9, preserved from `_name_prerequisites`."""
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title="Dependent child"),
        unmet=[_not_done("no-such-workspace", "up-01")],
    )

    assert "?:up-01 [backlog]" in render_hold_line(db_session, [held])


# ---------------------------------------------------------------------------
# R4: deterministic, actionable-first prerequisite order
# ---------------------------------------------------------------------------


def _mixed_child(db_session: Session) -> tuple[HeldChild, list[str]]:
    a_ws = _workspace(db_session, "a-ws")
    b_ws = _workspace(db_session, "b-ws")
    unmet = [
        _not_done(b_ws.id, "t-00"),
        _not_landed(a_ws.id, "t-02"),
        _not_done(a_ws.id, "t-01"),
        _not_landed(b_ws.id, "t-00"),
        _not_landed(a_ws.id, "t-01"),
        _not_done(a_ws.id, "t-02"),
    ]
    expected = ["a-ws:t-01", "a-ws:t-02", "b-ws:t-00", "a-ws:t-01", "a-ws:t-02", "b-ws:t-00"]
    return HeldChild(ticket=_ticket("w", "dep-01", title="Dep"), unmet=unmet), expected


def test_every_unlanded_prerequisite_is_named_before_every_other_reason(
    db_session: Session,
) -> None:
    """R4-AC1/R4-AC2. NOT_LANDED first (one command clears it), then ascending
    `<slug>:<external_id>` by plain code-point comparison."""
    held, expected_keys = _mixed_child(db_session)

    parsed = parse_hold_line(render_hold_line(db_session, [held]))

    named = parsed.children[0].prerequisites
    assert [p.key for p in named] == expected_keys, named
    reasons = [p.is_not_landed for p in named]
    assert reasons == [True, True, True, False, False, False], named


def test_the_order_does_not_depend_on_the_order_the_entries_arrive_in(
    db_session: Session,
) -> None:
    """R4-AC4. `unmet_prerequisites_for_start` returns an arbitrary order —
    measured on the real fixture, it returned p03, p17, …, p18, p16."""
    held, _ = _mixed_child(db_session)
    baseline = render_hold_line(db_session, [held])

    for seed in range(5):
        shuffled = list(held.unmet)
        random.Random(seed).shuffle(shuffled)
        reordered = HeldChild(ticket=held.ticket, unmet=shuffled)
        assert render_hold_line(db_session, [reordered]) == baseline


def test_two_reads_of_the_same_inputs_agree(db_session: Session) -> None:
    """R4-AC3, the renderer's half."""
    held, _ = _mixed_child(db_session)

    assert render_hold_line(db_session, [held]) == render_hold_line(db_session, [held])


# ---------------------------------------------------------------------------
# R5: the line is composed against the cut, not beside it
# ---------------------------------------------------------------------------


def _live_width_child(
    workspace_id: str, index: int, prerequisite_count: int, *, title_width: int = 13
) -> HeldChild:
    """A held child at the live maxima: 29-character ids, a 10-character
    prerequisite workspace slug, and NOT_LANDED prerequisites (R6-AC1)."""
    stem = "lg-milestone-that-919"
    unmet = [
        _not_landed(workspace_id, _pad(f"{stem}-{index:01d}{n:02d}", LIVE_ID_WIDTH))
        for n in range(1, prerequisite_count + 1)
    ]
    return HeldChild(
        ticket=_ticket(
            "w",
            _pad(f"{stem}-d{index:02d}", LIVE_ID_WIDTH),
            title=_pad(f"Held child {index:02d}", title_width, "."),
        ),
        unmet=unmet,
    )


def test_every_named_child_carries_at_least_one_complete_prerequisite(
    db_session: Session,
) -> None:
    """R5-AC3, fair share: the first child holds 18 prerequisites and the rest
    hold 1 each. One child must not consume the budget the others need."""
    upstream = _workspace(db_session, _pad("live-ws-aa", LIVE_SLUG_WIDTH, "a"))
    children = [_live_width_child(upstream.id, 1, 18)] + [
        _live_width_child(upstream.id, n, 1) for n in range(2, 6)
    ]
    owed = {child.ticket.external_id: len(child.unmet) for child in children}

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    assert CUT_MARKER not in message
    assert_plain_single_line(message)
    parsed = parse_hold_line(message)
    assert len(parsed.children) == 5, f"all five fit their minimum clause: {parsed}"
    assert parsed.elided_children == 0
    assert_reconciles(
        parsed,
        total=5,
        prerequisite_counts=[owed[child.external_id] for child in parsed.children],
    )
    for child in parsed.children[1:]:
        assert len(child.prerequisites) == 1, f"a single-prerequisite child names it: {child}"
        assert child.elided_prerequisites == 0


def test_growth_is_round_robin_and_not_greedy_first(db_session: Session) -> None:
    """R5-AC2. Three children with ten live-width prerequisites each: the
    budget names about ten clauses in total, so round-robin spreads them within
    one of each other while greedy-first would give the first child eight and
    the others their admitted minimum."""
    upstream = _workspace(db_session, _pad("live-ws-aa", LIVE_SLUG_WIDTH, "a"))
    children = [_live_width_child(upstream.id, n, 10) for n in range(1, 4)]

    parsed = parse_hold_line(render_hold_line(db_session, children))

    assert len(parsed.children) == 3, parsed
    counts = [len(child.prerequisites) for child in parsed.children]
    assert min(counts) >= 1, counts
    assert max(counts) - min(counts) <= 1, f"round-robin, not greedy-first: {counts}"
    assert_reconciles(parsed, total=3, prerequisite_counts=[10, 10, 10])


def test_admission_stops_at_the_first_child_that_does_not_fit(db_session: Session) -> None:
    """R5-AC3 / A8. Skipping would make the named set depend on how long a
    title happens to be, which an operator cannot predict — so the second
    child's minimum not fitting ends admission, and the short third child is
    counted rather than named."""
    upstream = _workspace(db_session, "up")
    children = [
        HeldChild(
            ticket=_ticket("w", "dep-01", title="A" * 1800),
            unmet=[_not_done(upstream.id, "up-01")],
        ),
        HeldChild(
            ticket=_ticket("w", "dep-02", title="B" * 300),
            unmet=[_not_done(upstream.id, "up-02")],
        ),
        HeldChild(
            ticket=_ticket("w", "dep-03", title="Short child"),
            unmet=[_not_done(upstream.id, "up-03")],
        ),
    ]

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    parsed = parse_hold_line(message)
    assert [child.external_id for child in parsed.children] == ["dep-01"], (
        f"admission stops, it does not skip to dep-03: {parsed}"
    )
    assert parsed.elided_children == 2
    assert message.endswith("; +2 more"), message[-40:]


def test_the_name_cap_binds_even_when_budget_remains(db_session: Session) -> None:
    """R5-AC2. `WAITING_NAMES` and the budget are both ceilings."""
    upstream = _workspace(db_session, "up")
    children = [
        HeldChild(
            ticket=_ticket("w", f"dep-{n:02d}", title=f"Held child {n:02d}"),
            unmet=[_not_done(upstream.id, f"up-{n:02d}")],
        )
        for n in range(1, 8)
    ]

    message = render_hold_line(db_session, children)
    parsed = parse_hold_line(message)

    assert len(parsed.children) == WAITING_NAMES, parsed
    assert parsed.elided_children == 7 - WAITING_NAMES
    assert message.endswith("; +2 more"), message[-40:]
    assert len(message) < ERROR_MESSAGE_LIMIT


def test_the_line_is_bounded_at_a_volume_no_fixture_would_reach(db_session: Session) -> None:
    """R5-AC1, "for every input": a 500-character title and 50 live-width
    unlanded prerequisites on each of five children."""
    upstream = _workspace(db_session, _pad("live-ws-aa", LIVE_SLUG_WIDTH, "a"))
    children = [_live_width_child(upstream.id, n, 50, title_width=500) for n in range(1, 6)]

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    assert CUT_MARKER not in message, "bounded by budget, not by truncation (R5-AC1)"
    assert truncate_for_storage(message) == message
    assert_plain_single_line(message)
