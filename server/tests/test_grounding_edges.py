"""Adversarial edges of the grounding verdict (lg-code-knowledge-368).

`test_symbol_fingerprints.py` measures the ticket's stated cases, each with one
clean cause. This module measures the corners the 7-step rule *has* but nobody
wrote down, which is where an ordered rule either holds its order or quietly
reports the wrong fact:

* one fingerprint in two files, so step 6 has a tie to break;
* one qualified name on two rows, the non-matching one written first, so step
  4 must match ANY row rather than the first it reads;
* a symbol edited in place whose original body survives in another file, so
  step 5 must outrank step 6;
* a workspace that is not the one the map was built for;
* anchor lists that are empty, reversed, repeated or structurally corrupt.

Three of these would be a crash or a wrong fact in production and none of them
is reachable from the scenario the test-design stage built, because every
symbol in that scenario has exactly one cause of drift.

Every repository is built by a fixture (`tests/symbol_map_repos.py`): a broken
setup inside a test body fails in the call phase and is indistinguishable from
a healthy failure.
"""

from __future__ import annotations

import ast

import pytest
from loregarden.models.domain import ExtractorTier, Freshness, GroundingVerdict
from loregarden.services.symbol_map import build_symbol_map
from loregarden.services.symbol_map.fingerprint import FINGERPRINT_SCHEME, fingerprint_for
from loregarden.services.symbol_map.grounding import (
    SymbolAnchor,
    claim_verdicts,
    encode_anchors,
    resolve_anchors,
    verdicts_for,
)
from tests.symbol_map_repos import (  # noqa: F401 - fixtures resolved by name
    EDGE_ANCHORS,
    EDGE_MOVED_LOCATION,
    GOLDEN_FINGERPRINTS,
    drift_edges_repo,
    edges_repo_fixture,
    grounding_repo_fixture,
    other_workspace_fixture,
    recorded_sql_fixture,
    session_fixture,
    workspace_fixture,
)

#: A verdict that says the claim still describes the code in front of the
#: reader. Nothing uncertain, corrupt or out-of-scope may ever read as one of
#: these: a false GROUNDED is the only failure of this feature that is worse
#: than having no feature.
_READS_AS_CURRENT = (GroundingVerdict.GROUNDED, GroundingVerdict.MOVED)


def _key(anchor: SymbolAnchor) -> str:
    return f"{anchor.qualified_name}@{anchor.fingerprint}"


def _check(session, workspace_id, root, anchor):
    return verdicts_for(session, workspace_id, root, [anchor]).rows[_key(anchor)]


@pytest.fixture(name="edges")
def edges_fixture(session, workspace, edges_repo):
    """Three anchors stamped against the tree as it was, then the drift.

    One fixture rather than three because the order is the point, and because
    a test body doing the drift would make a broken scaffold indistinguishable
    from a real verdict regression.
    """
    build_symbol_map(session, workspace.id, edges_repo)
    anchors = resolve_anchors(session, workspace.id, edges_repo, list(EDGE_ANCHORS))
    drift_edges_repo(edges_repo)
    build_symbol_map(session, workspace.id, edges_repo)
    return {anchor.qualified_name: anchor for anchor in anchors}


# ==========================================================================
# Two causes at once — the ordered rule under collision
# ==========================================================================


def test_a_fingerprint_in_two_files_is_moved_to_the_first_by_path_and_line(
    session, workspace, edges_repo, edges
):
    """Risk 3, which the ticket ACCEPTS and whose guard is determinism.

    `duplicated` left `pkg/beta.py` for `pkg/aaa.py` and `pkg/zzz.py`
    byte-identical, so the partition-wide fingerprint lookup finds two rows.
    The accepted outcome is that MOVED may name the wrong one of the two; the
    unaccepted one is a crash (`.one()` on two rows), a MISSING, or a location
    that depends on which row the database happened to hand back. Until now
    nothing in the suite put two rows in front of that lookup at all.
    """
    check = _check(session, workspace.id, edges_repo, edges["pkg.beta.duplicated"])

    assert check.verdict is GroundingVerdict.MOVED
    assert check.location == EDGE_MOVED_LOCATION


def test_the_colliding_location_is_the_same_after_the_map_is_rebuilt(
    session, workspace, edges_repo, edges
):
    """The map is a derived index rebuilt wholesale, so every row id changes on
    every build. A lookup ordered by anything but `(path, line)` can therefore
    name one file today and the other tomorrow, with no code change in between
    — a verdict that moves on its own is worse than one that is merely wrong.
    """
    first = _check(session, workspace.id, edges_repo, edges["pkg.beta.duplicated"]).location

    build_symbol_map(session, workspace.id, edges_repo)
    second = _check(session, workspace.id, edges_repo, edges["pkg.beta.duplicated"]).location

    assert first == second == EDGE_MOVED_LOCATION


def test_a_qualified_name_defined_twice_is_grounded_when_either_row_matches(
    session, workspace, edges_repo, edges
):
    """Step 4 says "a row with that qualified_name whose fingerprint is equal",
    not "the row". `pkg/beta.py` defines `doubled` twice and the EDITED copy is
    written first, so an implementation that compares the first row it reads
    reports CHANGED for a symbol whose original body is still in the file —
    and sends a reader to repair a claim that needs no repair.

    Overloads, platform branches and `if TYPE_CHECKING` blocks all produce a
    duplicated qualified name in real trees; this is not a synthetic shape.
    """
    check = _check(session, workspace.id, edges_repo, edges["pkg.beta.doubled"])

    assert check.verdict is GroundingVerdict.GROUNDED
    assert check.location.startswith("pkg/beta.py:")


def test_a_symbol_edited_in_place_is_changed_even_though_a_copy_survives(
    session, workspace, edges_repo, edges
):
    """Step 5 outranks step 6, which only matters when both could fire.

    `forked` kept its qualified name with a new body AND its original body
    landed in `pkg/delta.py`. MOVED here would tell a reader "the claim is
    still true, open this other file", when the code the claim describes was
    edited under its own name — the single wrong fact this rule's order exists
    to prevent.
    """
    check = _check(session, workspace.id, edges_repo, edges["pkg.beta.forked"])

    assert check.verdict is GroundingVerdict.CHANGED
    assert check.location == ""


# ==========================================================================
# Scope, shape and order of the batch
# ==========================================================================


def test_an_anchor_is_unverifiable_in_a_workspace_that_is_not_its_own(
    session, workspace, other_workspace, edges_repo, edges
):
    """The partition is keyed by (workspace, checkout identity). A lookup that
    filtered on fingerprint and forgot the workspace would ground a claim in
    one repository against a symbol in another — and would do it most often
    for exactly the trivial bodies risk 3 already names.
    """
    read = verdicts_for(session, other_workspace.id, edges_repo, list(edges.values()))

    assert read.status.freshness is Freshness.NO_PARTITION
    for check in read.rows.values():
        assert check.verdict is GroundingVerdict.UNVERIFIABLE
        assert check.reason


def test_asking_about_no_anchors_decides_nothing_and_does_not_fail(
    session, workspace, edges_repo, edges
):
    """The empty batch. Every read path calls this on whatever the corpus
    returned, and today that is 0 anchored claims in 614 nodes — so the empty
    case is not an edge, it is the only case running in production."""
    read = verdicts_for(session, workspace.id, edges_repo, [])

    assert read.rows == {}


def test_the_verdicts_do_not_depend_on_the_order_the_anchors_arrive_in(
    session, workspace, edges_repo, edges
):
    """A batched implementation builds one lookup per call and then walks the
    anchors against it. If the walk mutates that lookup — popping a matched
    row, say — the second anchor to ask about a shared row gets a different
    answer, and the corpus order a claim happened to be stored in decides it.
    """
    forward = verdicts_for(session, workspace.id, edges_repo, list(edges.values())).rows
    backward = verdicts_for(session, workspace.id, edges_repo, list(reversed(edges.values()))).rows

    assert forward == backward


def test_the_same_anchor_twice_decides_the_same_thing_once(session, workspace, edges_repo, edges):
    """The mapping is keyed by `qualified_name@fingerprint`, so a claim that
    names one symbol twice collapses to one entry rather than raising on a
    duplicate key or returning two conflicting ones."""
    anchor = edges["pkg.beta.doubled"]

    read = verdicts_for(session, workspace.id, edges_repo, [anchor, anchor])

    assert set(read.rows) == {_key(anchor)}
    assert read.rows[_key(anchor)].verdict is GroundingVerdict.GROUNDED


def test_two_stampings_of_one_symbol_each_get_their_own_verdict(
    session, workspace, edges_repo, edges
):
    """Two claims written months apart anchor the same qualified name at two
    fingerprints. They are different facts — one may still hold while the
    other does not — so the key carries the fingerprint and both survive."""
    current = edges["pkg.beta.doubled"]
    stale = SymbolAnchor(
        qualified_name=current.qualified_name,
        fingerprint=f"{FINGERPRINT_SCHEME}:" + "0" * 32,
        path=current.path,
        checkout_identity=current.checkout_identity,
        extractor_tier=current.extractor_tier,
    )

    read = verdicts_for(session, workspace.id, edges_repo, [current, stale])

    assert set(read.rows) == {_key(current), _key(stale)}
    assert read.rows[_key(current)].verdict is GroundingVerdict.GROUNDED
    assert read.rows[_key(stale)].verdict is GroundingVerdict.CHANGED


@pytest.mark.parametrize(
    "fingerprint",
    [
        "deadbeef",
        ":" + "0" * 32,
        f"{FINGERPRINT_SCHEME}",
        f"{FINGERPRINT_SCHEME}:",
        "SYM1:" + "0" * 32,
    ],
    ids=["no-scheme", "empty-scheme", "no-digest-at-all", "empty-digest", "wrong-case-scheme"],
)
def test_a_malformed_fingerprint_never_reads_as_current(
    session, workspace, edges_repo, edges, fingerprint
):
    """Criterion 3's floor, over the shapes step 2 does not enumerate.

    A stored value this build cannot interpret may be UNVERIFIABLE (nothing
    could be decided) or CHANGED (the map has that name and not that content).
    It may never be GROUNDED or MOVED: every one of these is a value no
    `fingerprint_for` ever produced, so vouching for it means a corrupt or
    hand-edited column can mint a verdict saying the code is unchanged.
    """
    current = edges["pkg.beta.doubled"]
    anchor = SymbolAnchor(
        qualified_name=current.qualified_name,
        fingerprint=fingerprint,
        path=current.path,
        checkout_identity=current.checkout_identity,
        extractor_tier=current.extractor_tier,
    )

    check = _check(session, workspace.id, edges_repo, anchor)

    assert check.verdict not in _READS_AS_CURRENT


# ==========================================================================
# Corrupt stored anchors, and the batch they share
# ==========================================================================


@pytest.mark.parametrize(
    "corrupt",
    ["{", "null", "17", '{"qualified_name": "pkg.beta.doubled"}', '["pkg.beta.doubled"]'],
    ids=["truncated", "null", "scalar", "object-not-list", "list-of-strings"],
)
def test_a_claim_with_corrupt_anchors_never_reads_as_current_and_poisons_no_sibling(
    session, workspace, edges_repo, edges, corrupt
):
    """`claim_verdicts` is handed the `anchors_json` column verbatim, and that
    column is on a per-workspace SQLite file in a synced vault that operators
    edit by hand. A row half-written by a sync conflict must not do either of
    the two things that are easy to ship: vouch for its own claim, or take
    every other claim in the same briefing down with it.

    The verdict for the corrupt claim itself is the implementer's call — no
    entry, or UNVERIFIABLE with a reason — but it is not GROUNDED, and the
    healthy claim beside it still decides.
    """
    healthy = encode_anchors([edges["pkg.beta.doubled"]])

    graded = claim_verdicts(
        session, workspace.id, edges_repo, [("corrupt", corrupt), ("healthy", healthy)]
    )

    assert graded["healthy"].verdict is GroundingVerdict.GROUNDED
    assert graded.get("corrupt") is None or graded["corrupt"].verdict not in _READS_AS_CURRENT


def test_a_full_page_of_claims_costs_what_one_claim_costs(
    session, workspace, edges_repo, edges, recorded_sql
):
    """R3's BATCHED contract at S2's stated volume: `loregarden_search_memory`
    returns up to 20 hits, and every one of them may be anchored. The existing
    batching test measures four anchors inside one call; this measures twenty
    CLAIMS, which is the other axis and the one a per-claim loop around a
    batched helper still gets wrong.
    """
    anchors = encode_anchors(list(edges.values()))
    # Bound before the measurement: `workspace` was expired by the build's
    # commit, so reading `.id` inside the window records an ORM refresh of
    # an unrelated row as if `verdicts_for` had issued it.
    workspace_id = workspace.id

    recorded_sql.clear()
    claim_verdicts(session, workspace_id, edges_repo, [("only", anchors)])
    for_one = len(recorded_sql)

    recorded_sql.clear()
    claim_verdicts(session, workspace_id, edges_repo, [(f"c{i}", anchors) for i in range(20)])
    for_twenty = len(recorded_sql)

    assert for_one == for_twenty


# ==========================================================================
# The hash itself, under inputs the fixture modules do not contain
# ==========================================================================


def test_the_fingerprint_is_stable_across_calls_in_one_process():
    """Determinism validation. A fingerprint seeded by anything that varies
    within a process — `id()`, a set iteration, a dict ordered by insertion of
    something unordered — reports CHANGED for code nobody touched, which is the
    noise the ticket says trains everyone to ignore the verdict."""
    source = ast.unparse(ast.parse("def route(t, s):\n    return t.id + s\n").body[0])

    assert len({fingerprint_for(source) for _ in range(10)}) == 1


@pytest.mark.parametrize(
    "source",
    ["", "x", "def café(ticket):\n    return ticket.id\n", "A = '\\U0001f600'\n"],
    ids=["empty", "one-char", "non-ascii-name", "astral-literal"],
)
def test_the_fingerprint_is_well_formed_for_sources_no_fixture_contains(source):
    """R1 hashes `.encode("utf-8")`, so a non-ASCII identifier or an astral
    literal must hash rather than raise. The empty string is the degenerate
    input a caller reaches by unparsing a node it failed to find."""
    value = fingerprint_for(source)

    scheme, _, digest = value.partition(":")
    assert scheme == FINGERPRINT_SCHEME
    assert len(digest) == 32


def test_the_golden_corpus_holds_eight_distinct_fingerprints():
    """A digest that collapsed — a truncation bug, a constant, a hash over the
    name alone — still satisfies every "unchanged" assertion in the suite and
    grounds every claim against every symbol. Distinctness is the cheap check
    that the stored value carries the body at all."""
    assert len(set(GOLDEN_FINGERPRINTS.values())) == len(GOLDEN_FINGERPRINTS)


def test_resolving_a_name_the_map_does_not_hold_is_refused_rather_than_invented(
    session, workspace, grounding_repo
):
    """`resolve_anchors` is the write-time half, and the whole point of R5 is
    that it refuses. A version that returned a short list, or an anchor with an
    empty fingerprint, would store a claim the reader can never check — which
    reads as GROUNDED-by-silence forever."""
    build_symbol_map(session, workspace.id, grounding_repo)

    with pytest.raises(Exception) as raised:  # noqa: B017 - the type is pinned in test_mcp_memory_grounding
        resolve_anchors(
            session, workspace.id, grounding_repo, ["pkg.alpha.steady", "pkg.alpha.nope"]
        )

    assert "pkg.alpha.nope" in str(raised.value)


def test_resolve_stamps_one_anchor_per_name_in_the_order_it_was_asked(
    session, workspace, grounding_repo
):
    """The anchors are rendered into note frontmatter as a list, so their order
    is user-visible and must be the author's, not the database's."""
    build_symbol_map(session, workspace.id, grounding_repo)
    asked = ["pkg.alpha.moving_house", "pkg.alpha.edited", "pkg.alpha.steady"]

    anchors = resolve_anchors(session, workspace.id, grounding_repo, asked)

    assert [anchor.qualified_name for anchor in anchors] == asked
    assert all(anchor.extractor_tier is ExtractorTier.PYTHON_AST for anchor in anchors)
