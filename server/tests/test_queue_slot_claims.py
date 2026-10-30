"""One answer to "is this slot busy with nothing, and may its claim be taken?".

The question was written out by hand three times — `queue_lanes._claimed_after`
for the release, `parallel_queue._within_reservation_grace` for the sweep, and
the lane invariant's detector would have been the third. The two that already
existed disagreed on a slot with no `assigned_at`, and the disagreement was only
unreachable because every claim writer happens to stamp it (lg-milestone-that-775).

These are unit tests over the extracted predicates: no session, no database, one
`AgentSlot` constructed in memory. The matrix is the one neither caller exercises
directly — a null claim time, a claim inside and outside the grace window, a claim
stamped before and after an entry's promotion, and an occupant id present.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from loregarden.models.domain import AgentSlot
from loregarden.services.queue_slot_claims import (
    RESERVATION_GRACE,
    claim_is_fresh,
    slot_is_busy_with_nothing,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 21, 12, 36, tzinfo=timezone.utc)


def _slot(
    *,
    is_available: bool = False,
    orchestration_run_id: str | None = None,
    run_id: str | None = None,
    assigned_at: datetime | None = NOW,
) -> AgentSlot:
    return AgentSlot(
        slot_number=1,
        is_available=is_available,
        current_orchestration_run_id=orchestration_run_id,
        current_run_id=run_id,
        assigned_at=assigned_at,
    )


# ---- is the slot busy with nothing in it? ----------------------------------


def test_a_busy_slot_naming_no_occupant_is_busy_with_nothing():
    assert slot_is_busy_with_nothing(_slot()) is True


def test_an_available_slot_is_never_busy_with_nothing():
    """Available is the ordinary empty lane, not the bad state."""
    assert slot_is_busy_with_nothing(_slot(is_available=True)) is False


def test_a_slot_naming_an_orchestration_is_not_busy_with_nothing():
    assert slot_is_busy_with_nothing(_slot(orchestration_run_id="orch_2848df")) is False


def test_a_slot_naming_an_agent_run_is_not_busy_with_nothing():
    """A dispatched single stage holds its lane through `current_run_id`, which
    is the column the first version of the occupancy read forgot."""
    assert slot_is_busy_with_nothing(_slot(run_id="run_1")) is False


# ---- may the claim be taken? ------------------------------------------------


def test_a_claim_with_no_time_on_it_is_treated_as_fresh():
    """The disagreement this extraction resolves, and the direction it resolves in.

    `_claimed_after` answered False (the lane released a slot that might hold a
    live reservation) and `_within_reservation_grace` answered False too (the
    sweep reclaimed it). Both chose "take it". A claim we cannot date is a claim
    we cannot prove is dead, so the extracted predicate takes the stricter
    answer: do not take it.
    """
    assert claim_is_fresh(_slot(assigned_at=None), now=NOW) is True
    assert claim_is_fresh(_slot(assigned_at=None), not_claimed_after=NOW) is True
    assert claim_is_fresh(_slot(assigned_at=None)) is True


def test_a_claim_inside_the_reservation_grace_is_fresh():
    """`claim_free_slot` claims with both ids null and the caller fills them in on
    `bind`. A sweep landing in that window must not reclaim it."""
    slot = _slot(assigned_at=NOW - RESERVATION_GRACE + timedelta(seconds=1))
    assert claim_is_fresh(slot, now=NOW) is True


def test_a_claim_outside_the_reservation_grace_is_not_fresh():
    slot = _slot(assigned_at=NOW - RESERVATION_GRACE - timedelta(seconds=1))
    assert claim_is_fresh(slot, now=NOW) is False


def test_a_claim_stamped_before_the_entry_was_promoted_is_not_fresh():
    """This is the entry's own claim with its ids already cleared: release it."""
    promoted_at = NOW
    slot = _slot(assigned_at=promoted_at - timedelta(seconds=5))
    assert claim_is_fresh(slot, not_claimed_after=promoted_at) is False


def test_a_claim_stamped_after_the_entry_was_promoted_is_fresh():
    """A later reservation took this lane. Releasing it would admit past the pool."""
    promoted_at = NOW
    slot = _slot(assigned_at=promoted_at + timedelta(seconds=5))
    assert claim_is_fresh(slot, not_claimed_after=promoted_at) is True


def test_an_entry_that_was_never_promoted_cannot_show_a_later_claim():
    """No promotion time is nothing to compare against, so the claim is not shown
    to be someone else's — the lane releases, as it does today."""
    assert claim_is_fresh(_slot(), not_claimed_after=None) is False


def test_a_slot_with_an_occupant_is_judged_by_its_claim_time_alone():
    """The predicate answers one question. Whether an occupant is present is the
    other predicate's job, and the two must not be quietly folded together."""
    slot = _slot(
        orchestration_run_id="orch_2848df",
        assigned_at=NOW - RESERVATION_GRACE - timedelta(seconds=1),
    )
    assert claim_is_fresh(slot, now=NOW) is False
    assert slot_is_busy_with_nothing(slot) is False


def test_both_windows_apply_when_both_are_given():
    """Either reason to leave a claim alone is enough; neither overrides the other."""
    recent = _slot(assigned_at=NOW - timedelta(seconds=1))
    assert claim_is_fresh(recent, now=NOW, not_claimed_after=NOW + timedelta(hours=1)) is True
    stale = _slot(assigned_at=NOW - timedelta(hours=1))
    assert claim_is_fresh(stale, now=NOW, not_claimed_after=NOW - timedelta(hours=2)) is True
    assert claim_is_fresh(stale, now=NOW, not_claimed_after=NOW) is False


# ---- naive timestamps -------------------------------------------------------


@pytest.mark.parametrize(
    "assigned_at",
    [NOW - timedelta(hours=1), (NOW - timedelta(hours=1)).replace(tzinfo=None)],
    ids=["aware", "naive"],
)
def test_sqlites_naive_utc_reads_the_same_as_an_aware_datetime(assigned_at: datetime):
    """SQLite hands back naive datetimes. Both callers re-derived the naive->UTC
    rule by hand; the extracted version coerces through `core.timestamps.as_utc`,
    and a naive claim must not read as a *future* one against an aware now."""
    slot = _slot(assigned_at=assigned_at)
    assert claim_is_fresh(slot, now=NOW) is False
    assert claim_is_fresh(slot, not_claimed_after=NOW) is False
    assert claim_is_fresh(slot, not_claimed_after=NOW - timedelta(hours=2)) is True


def test_the_predicates_do_not_write():
    """Pure: the question may be asked from a read path without touching the row."""
    slot = _slot(assigned_at=NOW - timedelta(hours=1))
    before = slot.model_dump()
    slot_is_busy_with_nothing(slot)
    claim_is_fresh(slot, now=NOW, not_claimed_after=NOW)
    assert slot.model_dump() == before


# ---- exactly one definition -------------------------------------------------


def test_reservation_grace_is_defined_once_in_the_tree():
    """A second definition is how the two copies came to disagree. Importing it
    elsewhere is fine; defining it twice is the defect."""
    found = subprocess.run(  # noqa: S603
        ["grep", "-rn", "RESERVATION_GRACE =", "loregarden/"],
        cwd=REPO_ROOT / "server",
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in found.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"RESERVATION_GRACE is defined {len(lines)} times:\n" + "\n".join(lines)
    assert lines[0].startswith("loregarden/services/queue_slot_claims.py:"), lines[0]


@pytest.mark.parametrize(
    ("module", "gone"),
    [("queue_lanes", "_claimed_after"), ("parallel_queue", "_within_reservation_grace")],
)
def test_the_lane_and_the_sweep_hold_no_predicate_of_their_own(module: str, gone: str):
    """Both callers answer from the extracted module rather than re-deriving it.

    Checked at the source, because a copy that happens to agree today is exactly
    what this ticket is about: the drift is paid by whoever next shortens the
    grace and teaches only one of them.
    """
    source = (REPO_ROOT / "server/loregarden/services" / f"{module}.py").read_text()
    assert re.search(r"\bfrom loregarden\.services\.queue_slot_claims import\b", source), (
        f"{module}.py does not import the shared predicate"
    )
    assert f"def {gone}(" not in source, f"{module}.py still defines its own copy, {gone}"


def test_the_shared_predicate_does_not_close_an_import_cycle():
    """`queue_slot_claims` imports `parallel_queue` today, and `parallel_queue`
    now has to import back. Function-local imports are not the way out of that
    (CLAUDE.md: fix the cycle), so this imports each module first in a fresh
    interpreter — the order that fails is the one a real entry point hits.
    """
    for first in (
        "loregarden.services.parallel_queue",
        "loregarden.services.queue_slot_claims",
        "loregarden.services.queue_lanes",
        "loregarden.services.invariant_registry",
        "loregarden.services.reconciliation",
    ):
        done = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                f"import {first}; import loregarden.services.queue_lanes",
            ],
            cwd=REPO_ROOT / "server",
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"importing {first} first fails:\n{done.stderr}"


# ---- the truth table, every corner (test-break) -----------------------------


@pytest.mark.parametrize("is_available", [True, False], ids=["available", "busy"])
@pytest.mark.parametrize("orch", [None, "orch_1"], ids=["no-orch", "orch"])
@pytest.mark.parametrize("run", [None, "run_1"], ids=["no-run", "run"])
def test_busy_with_nothing_is_exactly_busy_and_neither_id(
    is_available: bool, orch: str | None, run: str | None
):
    """All eight corners, because the predicate is three booleans and a reader
    who gets one of them backwards still passes a single-case test. `and not`
    read as `or not` agrees on six of these."""
    slot = _slot(is_available=is_available, orchestration_run_id=orch, run_id=run)
    assert slot_is_busy_with_nothing(slot) is (not is_available and not orch and not run)


@pytest.mark.parametrize(
    ("orch", "run"),
    [("", None), (None, ""), ("", "")],
    ids=["empty-orch", "empty-run", "both-empty"],
)
def test_an_empty_string_occupant_id_is_no_occupant(orch: str | None, run: str | None):
    """Both callers read occupancy as `slot.current_orchestration_run_id or
    slot.current_run_id`, so an id written as `''` is already no occupant. An
    extraction switching to `is None` would call such a lane occupied and leave
    it busy forever — the state this ticket exists to end, with a new way in.
    """
    assert slot_is_busy_with_nothing(_slot(orchestration_run_id=orch, run_id=run)) is True


# ---- boundaries, exactly ----------------------------------------------------


def test_a_claim_exactly_one_grace_old_is_not_fresh():
    """`_within_reservation_grace` compares `now - assigned_at < RESERVATION_GRACE`,
    strictly, so the instant the grace is up the claim is collectable. Pinned
    because the extraction must not move the window by one tick in either
    direction while nobody is looking."""
    assert claim_is_fresh(_slot(assigned_at=NOW - RESERVATION_GRACE), now=NOW) is False


def test_a_claim_stamped_at_the_very_moment_of_promotion_is_not_fresh():
    """`_claimed_after` compares `assigned_at > promoted_at`, strictly: a claim
    stamped at the promotion is the promotion's own claim, so it is this entry's
    residue and the lane releases it. `>=` here would wedge the common case."""
    assert claim_is_fresh(_slot(assigned_at=NOW), not_claimed_after=NOW) is False


# ---- which window applies, and when ----------------------------------------


def test_the_grace_applies_only_when_a_now_is_given():
    """The lane's release asks one question — "is this claim someone else's?" —
    and must not inherit the sweep's grace: today `_claimed_after` is all it
    consults, and `add_to_lane` + an immediate completion stamps a claim seconds
    old. An implementation that defaults `now` to the wall clock would call that
    claim fresh and never release the lane, which is the bug, restored.

    Real wall-clock instants, so a default of `utcnow()` cannot coincide with
    the frozen `NOW` the rest of this module uses.
    """
    just_now = datetime.now(timezone.utc)
    slot = _slot(assigned_at=just_now)
    assert claim_is_fresh(slot, not_claimed_after=just_now) is False
    assert claim_is_fresh(slot, now=just_now) is True, "inside the grace when asked"


# ---- timezone coercion, both arguments --------------------------------------


@pytest.mark.parametrize("claim_naive", [False, True], ids=["claim-aware", "claim-naive"])
@pytest.mark.parametrize("window_naive", [False, True], ids=["window-aware", "window-naive"])
def test_the_promotion_comparison_coerces_both_sides(claim_naive: bool, window_naive: bool):
    """`promoted_at` comes off SQLite naive too, and the caller hands it straight
    in. All four pairings must agree, or the answer depends on which row the
    session happened to have cached as an aware object.

    A comparison between a naive and an aware datetime does not read False — it
    raises TypeError, so a coercion applied to one side only fails loudly here
    and silently never, which is the point of covering the matrix.
    """
    claim = NOW - timedelta(minutes=5)
    window = NOW
    slot = _slot(assigned_at=claim.replace(tzinfo=None) if claim_naive else claim)
    not_after = window.replace(tzinfo=None) if window_naive else window
    assert claim_is_fresh(slot, not_claimed_after=not_after) is False
    assert claim_is_fresh(slot, not_claimed_after=not_after - timedelta(hours=1)) is True


@pytest.mark.parametrize("now_naive", [False, True], ids=["now-aware", "now-naive"])
def test_the_grace_comparison_coerces_the_now_it_is_given(now_naive: bool):
    now = NOW.replace(tzinfo=None) if now_naive else NOW
    assert claim_is_fresh(_slot(assigned_at=NOW - timedelta(seconds=1)), now=now) is True
    assert (
        claim_is_fresh(_slot(assigned_at=NOW - RESERVATION_GRACE - timedelta(seconds=1)), now=now)
        is False
    )


def test_an_offset_claim_is_converted_rather_than_relabelled():
    """`as_utc` *converts* an aware value; `replace(tzinfo=utc)` relabels it.
    The two differ by the whole offset, so a slot claimed an hour ago in +09:00
    reads as eight hours in the future under the relabelling — fresh forever,
    and a lane nothing ever reclaims."""
    an_hour_ago = (NOW - timedelta(hours=1)).astimezone(timezone(timedelta(hours=9)))
    assert an_hour_ago.utcoffset() == timedelta(hours=9)
    assert claim_is_fresh(_slot(assigned_at=an_hour_ago), now=NOW) is False
    assert claim_is_fresh(_slot(assigned_at=an_hour_ago), not_claimed_after=NOW) is False


# ---- shape of the answer ----------------------------------------------------


def test_the_predicates_answer_without_a_session():
    """ "Pure predicates over an AgentSlot" is a shape, not a style note: the
    invariant's detector and the sweep ask the question mid-read, and a
    predicate that could query would make the answer depend on the session's
    cache. Checked at the signature so a session parameter cannot be slipped in
    behind a default.
    """
    for predicate in (slot_is_busy_with_nothing, claim_is_fresh):
        params = inspect.signature(predicate).parameters
        assert "session" not in params, f"{predicate.__name__} takes a session"
        assert [p.kind for p in params.values()][0] is inspect.Parameter.POSITIONAL_OR_KEYWORD

    windows = inspect.signature(claim_is_fresh).parameters
    assert set(windows) == {"slot", "now", "not_claimed_after"}
    for name in ("now", "not_claimed_after"):
        assert windows[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert windows[name].default is None, f"{name} must default to 'window not asked for'"


def test_the_same_question_twice_gets_the_same_answer():
    """Determinism: nothing in here may read a clock of its own. A predicate
    that called `utcnow()` internally would answer differently as the suite
    ran, which is the flake that gets rerun instead of diagnosed."""
    slot = _slot(assigned_at=NOW - RESERVATION_GRACE + timedelta(milliseconds=1))
    answers = {claim_is_fresh(slot, now=NOW) for _ in range(50)}
    assert answers == {True}
    assert {slot_is_busy_with_nothing(slot) for _ in range(50)} == {True}


# ---- the third caller -------------------------------------------------------


def test_the_invariant_detector_holds_no_predicate_of_its_own():
    """AC5 names three callers; `test_the_lane_and_the_sweep_hold_no_predicate_of_their_own`
    checks two. The detector is the one that did not exist yet, and a detector
    that re-derives "busy with nothing, claim not fresh" is the third copy this
    extraction exists to prevent.
    """
    source = (REPO_ROOT / "server/loregarden/services/invariant_registry.py").read_text()
    assert re.search(r"\bfrom loregarden\.services\.queue_slot_claims import\b", source), (
        "invariant_registry.py does not import the shared predicate"
    )


@pytest.mark.parametrize(
    "module",
    ["queue_lanes", "parallel_queue", "invariant_registry", "queue_slot_claims"],
)
def test_no_caller_writes_the_grace_window_out_by_hand(module: str):
    """A second *definition* is caught by the grep above; this catches the
    second *expression* — `timedelta(seconds=60)` inline — which is the same
    drift wearing a different name and the grep would never see it.
    """
    source = (REPO_ROOT / "server/loregarden/services" / f"{module}.py").read_text()
    literals = re.findall(r"timedelta\(\s*seconds\s*=\s*60\s*\)", source)
    allowed = 1 if module == "queue_slot_claims" else 0
    assert len(literals) == allowed, (
        f"{module}.py writes the reservation grace out as a literal {len(literals)} time(s); "
        "import RESERVATION_GRACE"
    )
