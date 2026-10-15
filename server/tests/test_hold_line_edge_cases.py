"""lg-milestone-that-919, test-break: the corners the designed suite leaves open.

`test_hold_line.py` pins the two algorithms on hand-picked inputs. These are
the adversarial cases around them — the ones where an implementation that
passes every example can still be wrong:

* **the cut** (R2): unbalanced brackets (K5), a run of trailing separators, a
  message that is not a hold line at all (K8), idempotence, and a seeded fuzz
  over the invariants rather than over examples;
* **the order** (R4-AC2): plain code-point comparison, which a `.lower()` or a
  `casefold` key passes every example of and still gets wrong;
* **the budget** (R5): the `WAITING_NAMES` boundary, a child that exhausts its
  prerequisites mid-growth (a round-robin that does not notice never
  terminates), a child nothing can name, a title carrying the grammar's own
  delimiters (K5), and non-ASCII widths (A1: the limit counts characters).
"""

from __future__ import annotations

import random

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

LIVE_ID_WIDTH = 29
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


def _ticket(workspace_id: str, external_id: str, *, title: str = "T", state=TicketState.BACKLOG):
    return Ticket(external_id=external_id, workspace_id=workspace_id, title=title, state=state)


def _not_landed(workspace_id: str, external_id: str, *, target: str = LIVE_TARGET):
    return UnmetPrerequisite(
        _ticket(workspace_id, external_id, state=TicketState.DONE), UnmetReason.NOT_LANDED, target
    )


def _not_done(workspace_id: str, external_id: str):
    return UnmetPrerequisite(_ticket(workspace_id, external_id), UnmetReason.NOT_DONE)


# ---------------------------------------------------------------------------
# R2: the cut, under inputs nobody composed
# ---------------------------------------------------------------------------


def test_the_cut_strips_a_whole_run_of_trailing_separators(self_check: None = None) -> None:
    """R2 step 5 is three strips, not one: `rstrip().rstrip(";,").rstrip()`.

    A single `rstrip(";, ")` would also pass `test_the_cut_lands_on_the_last_
    separator_and_drops_it`, so that example does not pin the step. Here the
    cut lands on the `,` at 1992, leaving `…AAA ;` — which needs the space
    stripped, then the `;`, then the space before it.
    """
    message = "A" * 1990 + " ;,; " + "B" * 600

    assert truncate_for_storage(message) == "A" * 1990 + CUT_MARKER


def test_an_unbalanced_bracket_does_not_defeat_the_cut() -> None:
    """K5. An unclosed `[` inside the keep window makes every later separator
    read as 'inside a group', so the last balanced candidate is the one before
    the bracket opened. The criteria's invariants must still hold: bounded,
    marked, and a prefix of the input with nothing invented.

    How *much* survives is not pinned here, and deliberately: measured against
    the spec's algorithm this input keeps 27 of 3026 characters. That is a real
    defect, but no acceptance criterion of this ticket makes it false, so it is
    filed and reproduced in `test_open_defect_repros.py` rather than asserted
    against here — pinning the collapse would make it a guarantee, which is the
    mistake K1 records about the inherited `== 2000`.
    """
    message = "Child ticket waiting [unclosed " + "word " * 600

    result = truncate_for_storage(message)

    assert len(result) <= ERROR_MESSAGE_LIMIT
    assert result.endswith(CUT_MARKER)
    assert message.startswith(result[: -len(CUT_MARKER)])
    assert not result[: -len(CUT_MARKER)].endswith((" ", ";", ","))


def test_a_stray_closing_bracket_does_not_make_the_depth_count_negative() -> None:
    """K5, the mirror case. `message[:i].count("[") == message[:i].count("]")`
    is an equality, so a `]` with no `[` makes every later index read as
    unbalanced the other way. Bounded and marked either way.
    """
    message = "done] " + "word " * 600

    result = truncate_for_storage(message)

    assert len(result) <= ERROR_MESSAGE_LIMIT
    assert result.endswith(CUT_MARKER)
    assert message.startswith(result[: -len(CUT_MARKER)])


def test_the_cut_is_idempotent() -> None:
    """Cutting an already-cut message changes nothing: it fits, so R2 step 1
    returns it unchanged and no second marker is appended."""
    once = truncate_for_storage("word " * 600)

    assert truncate_for_storage(once) == once
    assert once.count(CUT_MARKER) == 1


def test_a_message_that_is_not_a_hold_line_is_cut_at_a_line_boundary() -> None:
    """K8. `truncate_for_storage` is reached by every terminal orchestration
    message — agent error text, gate output, a traceback — not only hold lines.
    A newline is whitespace, so it is a legal cut point, and the result must
    carry no newline the message did not have.
    """
    message = "\n".join(f'  File "module_{n:03d}.py", line {n}, in handler' for n in range(80))
    assert len(message) > ERROR_MESSAGE_LIMIT

    result = truncate_for_storage(message)

    assert len(result) <= ERROR_MESSAGE_LIMIT
    assert result.endswith(CUT_MARKER)
    assert result.count("\n") <= message.count("\n")
    assert message.startswith(result[: -len(CUT_MARKER)])
    assert not result[: -len(CUT_MARKER)].endswith(("\n", " "))


_FUZZ_ALPHABET = [
    "lg-milestone-that-919-p07zzzzzzzz",
    "[done, not landed on integration/x; land it: loregarden_land_ticket lg-ms-1]",
    "; ",
    ", ",
    " ",
    "\n",
    "— loregarden_get_ticket lg-ms-2",
    "Held child ........",
    "需要落地的子工单",
]


@pytest.mark.parametrize("seed", range(25))
def test_the_cut_holds_its_invariants_over_a_fuzzed_corpus(seed: int) -> None:
    """R2-AC1/AC2/AC3 as invariants rather than examples.

    Every property the criteria state, over messages assembled from the pieces
    a real hold line is made of, including the ones a hand-written example
    would not think to combine: a bracketed clause split across the keep
    boundary, a separator run, non-ASCII, a newline.
    """
    rng = random.Random(seed)
    message = "".join(rng.choice(_FUZZ_ALPHABET) for _ in range(rng.randint(1, 120)))

    result = truncate_for_storage(message)

    if len(message) <= ERROR_MESSAGE_LIMIT:
        assert result == message
        assert CUT_MARKER not in result
        return
    assert len(result) <= ERROR_MESSAGE_LIMIT
    assert result.endswith(CUT_MARKER)
    # Nothing is invented: the kept head is a prefix of the input.
    head = result[: -len(CUT_MARKER)]
    assert message.startswith(head), (head[-60:], message[len(head) - 60 : len(head) + 10])
    assert result.count("\n") <= message.count("\n")
    if message.count("[") == message.count("]"):
        assert head.count("[") == head.count("]"), head[-120:]
    assert truncate_for_storage(result) == result


# ---------------------------------------------------------------------------
# R4-AC2: plain code-point order, including the sentinel slug
# ---------------------------------------------------------------------------


def test_the_order_is_code_point_and_not_case_folded(db_session: Session) -> None:
    """R4-AC2 spells the comparison out — 'plain Python string `<`
    (code-point order), no case-folding, no locale'. Every example in the
    designed suite is lower-case, so a `key=str.lower` implementation passes
    all of them. Here `Zeta-ws` (0x5A) must precede `alpha-ws` (0x61), which
    case-folding reverses.
    """
    upper = _workspace(db_session, "Zeta-ws")
    lower = _workspace(db_session, "alpha-ws")
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title="Dep"),
        unmet=[_not_done(lower.id, "t-01"), _not_done(upper.id, "t-01")],
    )

    parsed = parse_hold_line(render_hold_line(db_session, held and [held]))

    assert [p.key for p in parsed.children[0].prerequisites] == ["Zeta-ws:t-01", "alpha-ws:t-01"]


def test_a_missing_workspace_sorts_at_the_face_value_of_its_question_mark(
    db_session: Session,
) -> None:
    """R4-AC2 / K9. The slug for a prerequisite whose workspace row is gone is
    the literal `?` and it is sorted like any other key: U+003F is below every
    letter, so it leads. (K9's parenthetical — 'sorts after letters' — is
    simply wrong about ASCII; the normative rule is the code-point comparison,
    and this pins that rather than the aside.)
    """
    known = _workspace(db_session, "a-ws")
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title="Dep"),
        unmet=[_not_done(known.id, "t-01"), _not_done("no-such-workspace", "t-01")],
    )

    parsed = parse_hold_line(render_hold_line(db_session, [held]))

    assert [p.key for p in parsed.children[0].prerequisites] == ["?:t-01", "a-ws:t-01"]


# ---------------------------------------------------------------------------
# R5: the budget's boundaries
# ---------------------------------------------------------------------------


def _short_children(db_session: Session, workspace: Workspace, count: int) -> list[HeldChild]:
    return [
        HeldChild(
            ticket=_ticket("w", f"dep-{n:02d}", title=f"Held child {n:02d}"),
            unmet=[_not_done(workspace.id, f"up-{n:02d}")],
        )
        for n in range(1, count + 1)
    ]


@pytest.mark.parametrize("count", [1, 2, WAITING_NAMES])
def test_at_or_below_the_name_cap_every_child_is_named_and_nothing_is_elided(
    db_session: Session, count: int
) -> None:
    """R3-AC4 / R5-AC4 at the boundary. The designed suite checks 1 and 7; the
    off-by-one that matters is `>= WAITING_NAMES` instead of `>`, which emits
    `; +0 more` at exactly five. `count=2` covers the children-elision
    reserve's `max(total - 1, 0)` at its smallest non-zero value.
    """
    workspace = _workspace(db_session, "up")
    children = _short_children(db_session, workspace, count)

    message = render_hold_line(db_session, children)

    assert_plain_single_line(message)
    assert "more" not in message, message
    parsed = parse_hold_line(message)
    assert len(parsed.children) == count
    assert parsed.elided_children == 0
    assert_reconciles(parsed, total=count, prerequisite_counts=[1] * count)


def test_a_child_that_runs_out_of_prerequisites_does_not_stall_the_growth_loop(
    db_session: Session,
) -> None:
    """R5-AC2. Round-robin over children with unequal counts: the two-clause
    child is exhausted after one round and must be stepped over, not treated
    as 'this round gave nobody a clause'. An implementation that ends the loop
    on the first child that cannot take one leaves the other two at their
    admitted minimum; one that never notices the exhausted child does not
    terminate at all.
    """
    upstream = _workspace(db_session, "up-ws")
    children = [
        HeldChild(
            ticket=_ticket("w", _pad(f"dep-{n:02d}", LIVE_ID_WIDTH), title=f"Held child {n:02d}"),
            unmet=[
                _not_landed(upstream.id, _pad(f"up-{n:02d}-{m:02d}", LIVE_ID_WIDTH))
                for m in range(1, owed + 1)
            ],
        )
        for n, owed in enumerate([2, 12, 12], start=1)
    ]

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    assert CUT_MARKER not in message
    parsed = parse_hold_line(message)
    assert_reconciles(parsed, total=3, prerequisite_counts=[2, 12, 12])
    counts = [len(child.prerequisites) for child in parsed.children]
    assert counts[0] == 2, f"the small child is finished, not starved: {counts}"
    assert abs(counts[1] - counts[2]) <= 1, f"round-robin between the two big ones: {counts}"
    # Measured against the spec's algorithm: [2, 4, 4]. The floor of three is
    # what separates it from an implementation that ends the whole growth loop
    # at the exhausted child ([2, 2, 2]) rather than stepping over it.
    assert min(counts[1], counts[2]) >= 3, f"growth continued past the small child: {counts}"


def test_a_child_nothing_can_name_leaves_a_bounded_line(db_session: Session) -> None:
    """R5-AC1 'for every input', at the degenerate end: one held child whose
    minimum clause is wider than the whole column. Admission admits nobody.
    The line must still fit, stay on one line, and lead with its count — it
    must not emit a half-written clause or raise.
    """
    upstream = _workspace(db_session, "up")
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title="Z" * 2500),
        unmet=[_not_done(upstream.id, "up-01")],
    )

    message = render_hold_line(db_session, [held])

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    assert CUT_MARKER not in message
    assert truncate_for_storage(message) == message
    assert message.startswith("Child ticket waiting on a prerequisite (1): ")
    assert_plain_single_line(message)
    assert "+1 more" in message, f"the child it could not name is still counted: {message!r}"


def test_a_held_child_with_no_unmet_prerequisites_writes_no_dangling_clause(
    db_session: Session,
) -> None:
    """A `HeldChild` only reaches the renderer with at least one unmet
    prerequisite — but `unmet` is a plain list and nothing in the type says so,
    and R5-AC3's `(waits for )` is exactly what an unguarded `", ".join([])`
    produces. Whether such a child is named or counted is the implementer's
    call; emitting a clause with nothing in it is not.
    """
    upstream = _workspace(db_session, "up")
    children = [
        HeldChild(ticket=_ticket("w", "dep-01", title="Empty child"), unmet=[]),
        HeldChild(
            ticket=_ticket("w", "dep-02", title="Real child"),
            unmet=[_not_done(upstream.id, "up-02")],
        ),
    ]

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT
    assert_plain_single_line(message)
    assert message.startswith("Child ticket waiting on a prerequisite (2): ")


def test_a_title_carrying_the_grammar_delimiters_is_inserted_verbatim(
    db_session: Session,
) -> None:
    """K5, and the constraint it carries for the implementer: titles are
    inserted verbatim — nothing is escaped, quoted or normalized, which is
    also what `detect-defensive-normalization` would reject. The line stays
    bounded and on one line; that a machine reader could mis-parse it is
    accepted, because nothing in production parses it.
    """
    upstream = _workspace(db_session, "up")
    title = "Fix [the thing]; (waits for nobody) — really"
    held = HeldChild(
        ticket=_ticket("w", "dep-01", title=title),
        unmet=[_not_done(upstream.id, "up-01")],
    )

    message = render_hold_line(db_session, [held])

    assert title in message, f"the title is not rewritten: {message!r}"
    assert len(message) <= ERROR_MESSAGE_LIMIT
    assert truncate_for_storage(message) == message


def test_the_limit_counts_characters_and_not_bytes(db_session: Session) -> None:
    """A1. `error_message` is a Python `str` on SQLite with no declared length
    and the cut it budgets is character-sliced, so the budget is characters.
    A title of CJK and emoji is three to four bytes per character; an
    implementation that budgeted `len(x.encode())` would under-fill here, and
    one that cut by bytes could split a character. Neither may happen.
    """
    upstream = _workspace(db_session, "需要")
    children = [
        HeldChild(
            ticket=_ticket("w", f"dep-{n:02d}", title="工单" * 150 + "🧱" * 50),
            unmet=[_not_landed(upstream.id, f"上游-{n:02d}")],
        )
        for n in range(1, 4)
    ]

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    assert truncate_for_storage(message) == message
    assert_plain_single_line(message)
    parsed = parse_hold_line(message)
    # Measured: 1485 characters and 3735 bytes. All three children are named
    # on a character budget; on a byte budget the first would exhaust it.
    assert len(message.encode()) > ERROR_MESSAGE_LIMIT, "the fixture must separate the two"
    assert len(parsed.children) == 3, f"a character budget fits all three: {parsed}"
    assert parsed.elided_children == 0


# ---------------------------------------------------------------------------
# R5-AC1 as an invariant: the renderer's own output never reaches the cut
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(20))
def test_the_composed_line_never_needs_truncating_over_a_fuzzed_matrix(
    db_session: Session, seed: int
) -> None:
    """R5-AC1/AC3/AC4/AC6 over a randomized matrix instead of three fixtures.

    Children from 1 to 8, each 1 to 20 prerequisites of mixed reason, titles
    from 1 to 400 characters. Every property the criteria state must hold at
    every draw: the line fits by budget (the cut is a no-op on it), every
    named child carries a whole prerequisite, both elision counts reconcile
    with what the fixture actually owes, and the children named are a *prefix*
    of the held order — which is what distinguishes A8's 'admission stops'
    from 'admission skips'.
    """
    rng = random.Random(seed)
    upstream = _workspace(db_session, f"ws-{seed:02d}")
    children = []
    for n in range(1, rng.randint(1, 8) + 1):
        owed = rng.randint(1, 20)
        children.append(
            HeldChild(
                ticket=_ticket(
                    "w",
                    _pad(f"dep-{n:02d}", LIVE_ID_WIDTH),
                    title="T" * rng.randint(1, 400) + f"-{n:02d}",
                ),
                unmet=[
                    (_not_landed if rng.random() < 0.5 else _not_done)(
                        upstream.id, _pad(f"up-{n:02d}-{m:02d}", LIVE_ID_WIDTH)
                    )
                    for m in range(1, owed + 1)
                ],
            )
        )
    owed_by_id = {child.ticket.external_id: len(child.unmet) for child in children}

    message = render_hold_line(db_session, children)

    assert len(message) <= ERROR_MESSAGE_LIMIT, len(message)
    assert truncate_for_storage(message) == message, "bounded by budget, not by the cut (R5-AC1)"
    assert_plain_single_line(message)
    parsed = parse_hold_line(message)
    named_ids = [child.external_id for child in parsed.children]
    assert len(named_ids) <= WAITING_NAMES
    assert named_ids == [c.ticket.external_id for c in children][: len(named_ids)], (
        f"admission stops at the first child that does not fit, it does not skip: {named_ids}"
    )
    assert_reconciles(
        parsed, total=len(children), prerequisite_counts=[owed_by_id[i] for i in named_ids]
    )
