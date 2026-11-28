"""Grounded knowledge claims: symbol fingerprints that survive code motion.

Ticket lg-code-knowledge-368, requirements R1 (the fingerprint), R3 (resolve
and verdict) and the storage half of R2. The modules under test —
`services.symbol_map.fingerprint` and `services.symbol_map.grounding` — do not
exist until the implement stage, so this whole file is a collection error
until then. That is what this stage produces.

**Every repository is built by a fixture**, in `tests/symbol_map_repos.py` or
in the one scenario fixture below, never in a test body: a broken setup inside
a test body fails in the call phase and is indistinguishable from a healthy
failure.

The read-path halves of the ticket live elsewhere — `test_inherited_wisdom.py`
for the briefing (S1), `test_mcp_memory_grounding.py` for the write-time
refusal and the search reply (S2/S3), `test_memory_store.py` for the anchor
column and the note frontmatter.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest
from loregarden.db.migrations import MIGRATIONS
from loregarden.db.versions import symbol_map as symbol_map_migration
from loregarden.db.versions import symbol_map_fingerprint as fingerprint_migration
from loregarden.models.domain import (
    ExtractorTier,
    Freshness,
    GroundingVerdict,
    SymbolMapSymbol,
    Workspace,
)
from loregarden.services import symbol_map as symbol_map_package
from loregarden.services.symbol_map import (
    SymbolLocation,
    build_symbol_map,
    definitions_for,
    outline_for,
)
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.fingerprint import FINGERPRINT_SCHEME, fingerprint_for
from loregarden.services.symbol_map.grounding import (
    GroundingCheck,
    SymbolAnchor,
    claim_verdicts,
    decode_anchors,
    encode_anchors,
    resolve_anchors,
    verdicts_for,
)
from loregarden.services.symbol_map.sources import head_sha
from sqlalchemy import text
from sqlmodel import Session, select
from tests.import_scan import imported_names
from tests.symbol_map_repos import (  # noqa: F401 - fixtures resolved by name
    GOLDEN_FINGERPRINTS,
    GROUNDING_BASE_LINES,
    GROUNDING_DRIFTED_LOCATIONS,
    TS_ANCHOR_NAME,
    commit_unindexed_change,
    drift_grounding_repo,
    golden_repo_fixture,
    grounding_repo_fixture,
    migrated_engine_fixture,
    recorded_sql_fixture,
    session_fixture,
    workspace_fixture,
)

PACKAGE_ROOT = Path(symbol_map_package.__file__).parent

#: The four anchors the drift scenario resolves, one per non-UNVERIFIABLE verdict.
ANCHORED = (
    "pkg.alpha.steady",
    "pkg.alpha.edited",
    "pkg.alpha.renamed_away",
    "pkg.alpha.moving_house",
)


def _key(anchor: SymbolAnchor) -> str:
    return f"{anchor.qualified_name}@{anchor.fingerprint}"


def _one(source: str) -> str:
    """The fingerprint of the LAST top-level statement of *source*."""
    return fingerprint_for(ast.unparse(ast.parse(source).body[-1]))


# ==========================================================================
# R1 / criterion 1 — the fingerprint itself, over strings
# ==========================================================================

_BASE = "def route(ticket, stage):\n    return ticket.id + stage\n"


def test_the_scheme_is_sym1_and_rides_in_the_stored_value():
    """A deliberate scheme change must read as UNVERIFIABLE (regroundable),
    never as CHANGED (false drift), which is only possible if the prefix is
    stored beside the digest rather than implied."""
    value = _one(_BASE)

    assert FINGERPRINT_SCHEME == "sym1"
    scheme, _, digest = value.partition(":")
    assert scheme == FINGERPRINT_SCHEME
    assert len(digest) == 32
    assert set(digest) <= set("0123456789abcdef")


def test_the_verdict_enum_has_exactly_the_five_members_the_spec_names():
    """D3. Five states, and their VALUES are a wire format: they reach an agent
    through `loregarden_search_memory`'s JSON reply, so a rename is a break."""
    assert {member.value for member in GroundingVerdict} == {
        "grounded",
        "moved",
        "changed",
        "missing",
        "unverifiable",
    }


def test_the_digest_is_sha256_over_the_scheme_line_and_the_unparsed_node():
    """Recomputed here rather than imported: the definition IS the contract,
    and a test that calls the function to predict the function proves nothing."""
    node_source = ast.unparse(ast.parse(_BASE).body[0])
    expected = hashlib.sha256(
        (FINGERPRINT_SCHEME + "\n" + node_source).encode("utf-8")
    ).hexdigest()[:32]

    assert fingerprint_for(node_source) == f"{FINGERPRINT_SCHEME}:{expected}"


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("moved down the file", "X = 1\nY = 2\n\n\n" + _BASE),
        (
            "gained comments",
            "def route(ticket, stage):\n    # explain the sum\n    return ticket.id + stage  # t\n",
        ),
        ("gained blank lines", "def route(ticket, stage):\n\n\n    return ticket.id + stage\n\n"),
        (
            "reformatted",
            "def route(\n    ticket,\n    stage,\n):\n    return (\n"
            "        ticket.id\n        + stage\n    )\n",
        ),
    ],
)
def test_a_symbol_that_only_moves_or_is_reformatted_keeps_its_fingerprint(label, source):
    """Criterion 1's UNCHANGED half. Comments are not in the AST and formatting
    is re-rendered canonically, so this is immunity by construction; motion is
    invisible because no line number is in the input."""
    assert _one(source) == _one(_BASE), label


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("one statement of the body edited", "def route(ticket, stage):\n    return ticket.id\n"),
        ("the symbol renamed", "def apply_route(ticket, stage):\n    return ticket.id + stage\n"),
    ],
)
def test_editing_the_body_or_renaming_the_symbol_changes_the_fingerprint(label, source):
    """Criterion 1's CHANGED half. The symbol's own NAME is in the key (D2): a
    renamed symbol is a different unit, not the same one moved."""
    assert _one(source) != _one(_BASE), label


def test_a_docstring_edit_changes_the_fingerprint():
    """Accepted and recorded (risk 4): the docstring is the contract. A COMMENT
    edit does not, which is the case criterion 1 actually names — pinned above."""
    docstringed = (
        'def route(ticket, stage):\n    """New contract."""\n    return ticket.id + stage\n'
    )

    assert _one(docstringed) != _one(_BASE)


def test_the_fingerprint_module_reaches_nothing_but_hashlib():
    """R1's constraint: `hashlib` and the scheme constant only. A pure module
    is what lets the golden test below mean what it says — no I/O, nothing from
    the rest of the package, nothing that could vary by checkout."""
    imported = imported_names((PACKAGE_ROOT / "fingerprint.py").read_text(encoding="utf-8"))

    assert imported <= {"hashlib", "__future__"}


# ==========================================================================
# Criterion 1 — the golden hexes, over a real build
# ==========================================================================


def test_the_golden_fingerprints_are_unchanged(session, workspace, golden_repo):
    """An interpreter whose `ast.unparse` renders differently reds CI here
    rather than silently re-fingerprinting a stored corpus (risk 2).

    Eight symbols, one of each shape `python_ast` records plus a decorated and
    an async one. If this fails after an interpreter upgrade the fix is a
    deliberate scheme bump, not a re-recorded table: the old corpus must read
    UNVERIFIABLE, which `test_an_unknown_scheme_prefix_is_unverifiable` pins.
    """
    build_symbol_map(session, workspace.id, golden_repo)

    rows = session.exec(select(SymbolMapSymbol)).all()
    built = {row.qualified_name: row.fingerprint for row in rows}

    assert built == GOLDEN_FINGERPRINTS


def test_every_python_node_the_map_records_carries_a_fingerprint(session, workspace, golden_repo):
    """R1: `python_ast.py` sets it for every node it records — class, function,
    method and module constant alike. A tier that fingerprints only functions
    would leave a class anchor permanently UNVERIFIABLE."""
    build_symbol_map(session, workspace.id, golden_repo)

    rows = session.exec(select(SymbolMapSymbol)).all()

    assert rows
    assert all(row.fingerprint.startswith(f"{FINGERPRINT_SCHEME}:") for row in rows)
    assert len({row.kind for row in rows}) >= 4


def test_a_typescript_symbol_stores_an_empty_fingerprint(session, workspace, grounding_repo):
    """D5. The regex tier cannot bound a body, and hashing a guessed span would
    report CHANGED for a symbol an unrelated declaration was appended below."""
    build_symbol_map(session, workspace.id, grounding_repo)

    rows = session.exec(
        select(SymbolMapSymbol).where(SymbolMapSymbol.qualified_name == TS_ANCHOR_NAME)
    ).all()

    assert [row.fingerprint for row in rows] == [""]


def test_the_location_value_readers_hand_back_carries_the_fingerprint(
    session, workspace, grounding_repo
):
    """R1 storage: `SymbolLocation` gains `fingerprint`, and `end_line` is still
    NOT added — `ast.unparse` needs no extent."""
    build_symbol_map(session, workspace.id, grounding_repo)

    rows = outline_for(session, workspace.id, grounding_repo, "pkg/alpha.py").rows

    assert {field.name for field in dataclass_fields(SymbolLocation)} >= {"fingerprint"}
    assert "end_line" not in {field.name for field in dataclass_fields(SymbolLocation)}
    assert all(row.fingerprint.startswith(f"{FINGERPRINT_SCHEME}:") for row in rows)


# ==========================================================================
# R1 storage — the append-only migration
# ==========================================================================

_FINGERPRINT_INDEX = "ix_symbol_map_symbol_fingerprint"


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _indexes(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[1]
            for row in conn.exec_driver_sql(f"PRAGMA index_list({table})")
            if not str(row[1]).startswith("sqlite_autoindex")
        }


def test_the_migration_adds_the_column_and_the_index(migrated_engine):
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        fingerprint_migration.m_symbol_map_fingerprint(conn)

    assert "fingerprint" in _columns(migrated_engine, "symbol_map_symbols")
    assert _FINGERPRINT_INDEX in _indexes(migrated_engine, "symbol_map_symbols")


def test_running_the_fingerprint_migration_twice_is_a_no_op(migrated_engine):
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        fingerprint_migration.m_symbol_map_fingerprint(conn)
    before = (
        _columns(migrated_engine, "symbol_map_symbols"),
        _indexes(migrated_engine, "symbol_map_symbols"),
    )

    with migrated_engine.begin() as conn:
        fingerprint_migration.m_symbol_map_fingerprint(conn)

    assert (
        _columns(migrated_engine, "symbol_map_symbols"),
        _indexes(migrated_engine, "symbol_map_symbols"),
    ) == before


def test_a_partial_fingerprint_migration_is_completed_rather_than_failed(migrated_engine):
    """The ALTER and the CREATE INDEX guard themselves separately, so a run
    that added the column and died before the index completes next time."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        fingerprint_migration.m_symbol_map_fingerprint(conn)
    with migrated_engine.begin() as conn:
        conn.execute(text(f"DROP INDEX {_FINGERPRINT_INDEX}"))

    with migrated_engine.begin() as conn:
        fingerprint_migration.m_symbol_map_fingerprint(conn)

    assert _FINGERPRINT_INDEX in _indexes(migrated_engine, "symbol_map_symbols")


def test_the_migration_backfills_nothing(migrated_engine):
    """The table is a derived index, rebuilt wholesale. A backfill would have to
    invent a fingerprint for a row whose source it never read."""
    # A real `workspaces` row: `symbol_map_partitions.workspace_id` is a
    # foreign key and this engine enforces them, so a literal 'w' makes the
    # SETUP fail in a way indistinguishable from a migration that backfilled.
    with Session(migrated_engine) as setup:
        setup.add(Workspace(id="w", slug="backfill", name="backfill", repo_path=""))
        setup.commit()

    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        conn.exec_driver_sql(
            "INSERT INTO symbol_map_partitions "
            "(id, workspace_id, checkout_identity, checkout_root, head_sha, built_at) "
            "VALUES ('p', 'w', 'i', '/tmp/x', 'sha', '2026-11-18T00:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO symbol_map_files "
            "(id, partition_id, path, language, extractor_tier, parse_status, "
            "parse_error, indexed_sha, index_built_at) "
            "VALUES ('f', 'p', 'a.py', 'python', 'python_ast', 'parsed', '', 'sha', "
            "'2026-11-18T00:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO symbol_map_symbols "
            "(id, file_id, partition_id, name, qualified_name, kind, line, parent) "
            "VALUES ('s', 'f', 'p', 'old', 'a.old', 'function', 1, '')"
        )

    with migrated_engine.begin() as conn:
        fingerprint_migration.m_symbol_map_fingerprint(conn)

    with migrated_engine.connect() as conn:
        stored = conn.exec_driver_sql("SELECT fingerprint FROM symbol_map_symbols").scalar()
    assert stored == ""


def test_the_fingerprint_migration_is_registered_once_after_the_symbol_map_migration():
    ids = [mid for mid, _ in MIGRATIONS]
    registered = [mid for mid in ids if "symbol_map_fingerprint" in mid]

    assert len(registered) == 1, "exactly one symbol-map-fingerprint migration id"
    assert ids.index(registered[0]) > ids.index("20261117_symbol_map")


# ==========================================================================
# R3 / criterion 3 — resolve, and the five verdicts
# ==========================================================================


@pytest.fixture(name="drifted")
def drifted_fixture(session, workspace, grounding_repo):
    """Anchors resolved against the tree as it was, then the code moves on.

    The whole scenario is one fixture because the ORDER is the point: anchors
    are stamped at write time, the tree drifts, and only then is a verdict
    asked for. A test body doing the drift would make a broken scaffold
    indistinguishable from a real verdict regression.
    """
    build_symbol_map(session, workspace.id, grounding_repo)
    anchors = resolve_anchors(session, workspace.id, grounding_repo, list(ANCHORED))
    drift_grounding_repo(grounding_repo)
    build_symbol_map(session, workspace.id, grounding_repo)
    return anchors


def test_resolve_stamps_the_fingerprint_path_identity_and_tier_it_observed(
    session, workspace, grounding_repo
):
    """R2's anchor record, and criterion 2's write-time half. `checkout_identity`
    is on every anchor so 'the code changed' stays distinguishable from 'it was
    stamped against another tree' (risk 5)."""
    build_symbol_map(session, workspace.id, grounding_repo)

    [anchor] = resolve_anchors(session, workspace.id, grounding_repo, ["pkg.alpha.steady"])

    path, _line = GROUNDING_BASE_LINES["pkg.alpha.steady"]
    assert anchor.qualified_name == "pkg.alpha.steady"
    assert anchor.path == path
    assert anchor.checkout_identity == identify(grounding_repo)
    assert anchor.extractor_tier == ExtractorTier.PYTHON_AST
    assert anchor.fingerprint == (
        definitions_for(session, workspace.id, grounding_repo, "steady").rows[0].fingerprint
    )


def test_the_anchor_codec_round_trips(session, workspace, grounding_repo):
    """`encode_anchors`/`decode_anchors` are the column's only writer and reader,
    so a key either of them renames is a corpus nothing can read back."""
    build_symbol_map(session, workspace.id, grounding_repo)
    anchors = resolve_anchors(session, workspace.id, grounding_repo, list(ANCHORED))

    assert decode_anchors(encode_anchors(anchors)) == anchors


def test_an_untouched_symbol_is_grounded(session, workspace, grounding_repo, drifted):
    """Step 4. `steady` only moved down a line, which the fingerprint cannot see."""
    check = verdicts_for(session, workspace.id, grounding_repo, drifted).rows[
        _key(next(a for a in drifted if a.qualified_name == "pkg.alpha.steady"))
    ]

    assert check.verdict is GroundingVerdict.GROUNDED
    assert check.location == GROUNDING_DRIFTED_LOCATIONS["pkg.alpha.steady"]


def test_a_symbol_whose_body_was_edited_is_changed(session, workspace, grounding_repo, drifted):
    """Step 5: the qualified name is still there, the fingerprint is not."""
    check = verdicts_for(session, workspace.id, grounding_repo, drifted).rows[
        _key(next(a for a in drifted if a.qualified_name == "pkg.alpha.edited"))
    ]

    assert check.verdict is GroundingVerdict.CHANGED
    assert check.location == ""


def test_a_symbol_moved_to_another_file_is_moved_with_its_new_location(
    session, workspace, grounding_repo, drifted
):
    """Criterion 3's named case, and the reason the path is NOT in the key (D2).

    `moving_house` left `pkg/alpha.py` for `pkg/gamma.py` byte-identical. Its
    qualified name — which embeds the module path — misses; its fingerprint
    does not. MISSING here would be the regression the last paragraph of the
    ticket exists to prevent.
    """
    check = verdicts_for(session, workspace.id, grounding_repo, drifted).rows[
        _key(next(a for a in drifted if a.qualified_name == "pkg.alpha.moving_house"))
    ]

    assert check.verdict is GroundingVerdict.MOVED
    assert check.location == GROUNDING_DRIFTED_LOCATIONS["pkg.alpha.moving_house"]
    assert check.qualified_name == "pkg.alpha.moving_house"


def test_a_renamed_symbol_is_missing_rather_than_moved(session, workspace, grounding_repo, drifted):
    """Step 7. The name is in the key, so `gone_forever` carries neither the old
    qualified name nor the old fingerprint — it is a different unit (D2)."""
    check = verdicts_for(session, workspace.id, grounding_repo, drifted).rows[
        _key(next(a for a in drifted if a.qualified_name == "pkg.alpha.renamed_away"))
    ]

    assert check.verdict is GroundingVerdict.MISSING
    assert check.location == ""


def test_every_verdict_names_its_symbol_and_reasons_only_when_unverifiable(
    session, workspace, grounding_repo, drifted
):
    """UX S1's floor: a verdict word with no symbol beside it does not satisfy
    the criterion. And `reason` is non-empty EXACTLY when UNVERIFIABLE — a
    reason on a decided verdict would read as a caveat on a fact."""
    checks = verdicts_for(session, workspace.id, grounding_repo, drifted).rows

    assert len(checks) == len(ANCHORED)
    for check in checks.values():
        assert check.qualified_name
        assert bool(check.reason) is (check.verdict is GroundingVerdict.UNVERIFIABLE)


# ==========================================================================
# R3 / criterion 3 — UNVERIFIABLE, which is never collapsed into MISSING
# ==========================================================================


def test_no_partition_is_unverifiable_and_says_there_is_no_map(
    session, workspace, grounding_repo, tmp_path
):
    """Step 1, NO_PARTITION. The fail-safe direction of D4: a checkout with no
    map briefs exactly as today rather than demoting every anchored claim."""
    build_symbol_map(session, workspace.id, grounding_repo)
    anchors = resolve_anchors(session, workspace.id, grounding_repo, ["pkg.alpha.steady"])
    # Another checkout entirely: the identity is derived from the resolved root,
    # so this one finds no partition rather than inheriting the mapped one's.
    other_root = tmp_path / "elsewhere"
    other_root.mkdir()

    read = verdicts_for(session, workspace.id, other_root, anchors)

    assert read.status.freshness is Freshness.NO_PARTITION
    check = read.rows[_key(anchors[0])]
    assert check.verdict is GroundingVerdict.UNVERIFIABLE
    assert check.reason == "no symbol map for this checkout"


def test_a_sha_mismatch_is_unverifiable_and_names_both_shas(session, workspace, grounding_repo):
    """Step 1, SHA_MISMATCH. The map describes a tree the agent is not standing
    on, so no verdict was COMPUTED — which is not the same fact as MISSING."""
    build_symbol_map(session, workspace.id, grounding_repo)
    anchors = resolve_anchors(session, workspace.id, grounding_repo, ["pkg.alpha.steady"])
    indexed = head_sha(grounding_repo)
    now = commit_unindexed_change(grounding_repo)

    read = verdicts_for(session, workspace.id, grounding_repo, anchors)

    assert read.status.freshness is Freshness.SHA_MISMATCH
    check = read.rows[_key(anchors[0])]
    assert check.verdict is GroundingVerdict.UNVERIFIABLE
    assert check.reason == f"the map describes {indexed}, this checkout is {now}"


def test_an_unknown_scheme_prefix_is_unverifiable(session, workspace, grounding_repo):
    """Step 2, and risk 2's second guard: a deliberate scheme bump makes the old
    corpus REGROUNDABLE rather than reporting false drift on every claim."""
    build_symbol_map(session, workspace.id, grounding_repo)
    [stamped] = resolve_anchors(session, workspace.id, grounding_repo, ["pkg.alpha.steady"])
    anchor = SymbolAnchor(
        qualified_name=stamped.qualified_name,
        fingerprint="sym2:" + "0" * 32,
        path=stamped.path,
        checkout_identity=stamped.checkout_identity,
        extractor_tier=stamped.extractor_tier,
    )

    check = verdicts_for(session, workspace.id, grounding_repo, [anchor]).rows[_key(anchor)]

    assert check.verdict is GroundingVerdict.UNVERIFIABLE
    assert check.reason == "unknown fingerprint scheme 'sym2'"


def test_a_tier_that_produces_no_fingerprint_is_unverifiable(session, workspace, grounding_repo):
    """Step 3. An anchor stored before 386 lands cannot be checked, and saying
    MISSING would tell an agent its still-present symbol had been deleted."""
    build_symbol_map(session, workspace.id, grounding_repo)
    anchor = SymbolAnchor(
        qualified_name=TS_ANCHOR_NAME,
        fingerprint="",
        path="web/thing.ts",
        checkout_identity=identify(grounding_repo),
        extractor_tier=ExtractorTier.REGEX_FALLBACK,
    )

    check = verdicts_for(session, workspace.id, grounding_repo, [anchor]).rows[_key(anchor)]

    assert check.verdict is GroundingVerdict.UNVERIFIABLE
    assert check.reason == "this anchor's extractor tier produces no fingerprint"


def test_freshness_is_checked_before_the_scheme_prefix(
    session, workspace, grounding_repo, tmp_path
):
    """The algorithm is an ORDERED rule. With no partition there is nothing to
    compare against, so 'no map' is the honest reason even for an anchor whose
    scheme this build also does not know."""
    other_root = tmp_path / "nowhere"
    other_root.mkdir()
    anchor = SymbolAnchor(
        qualified_name="pkg.alpha.steady",
        fingerprint="sym2:" + "0" * 32,
        path="pkg/alpha.py",
        checkout_identity=identify(grounding_repo),
        extractor_tier=ExtractorTier.PYTHON_AST,
    )

    check = verdicts_for(session, workspace.id, other_root, [anchor]).rows[_key(anchor)]

    assert check.reason == "no symbol map for this checkout"


# ==========================================================================
# R3 — claim-level severity, and the batching contract
# ==========================================================================


def test_a_claims_verdict_is_the_most_severe_of_its_anchors(
    session, workspace, grounding_repo, drifted
):
    """MISSING > CHANGED > UNVERIFIABLE > MOVED > GROUNDED. One stale anchor is
    enough to demote the claim: a claim is only as current as its worst part."""
    by_name = {a.qualified_name: a for a in drifted}
    claims = [
        ("all-fine", encode_anchors([by_name["pkg.alpha.steady"]])),
        (
            "one-moved",
            encode_anchors([by_name["pkg.alpha.steady"], by_name["pkg.alpha.moving_house"]]),
        ),
        (
            "one-changed",
            encode_anchors([by_name["pkg.alpha.moving_house"], by_name["pkg.alpha.edited"]]),
        ),
        (
            "one-missing",
            encode_anchors([by_name["pkg.alpha.edited"], by_name["pkg.alpha.renamed_away"]]),
        ),
    ]

    graded = claim_verdicts(session, workspace.id, grounding_repo, claims)

    assert graded["all-fine"].verdict is GroundingVerdict.GROUNDED
    assert graded["one-moved"].verdict is GroundingVerdict.MOVED
    assert graded["one-changed"].verdict is GroundingVerdict.CHANGED
    assert graded["one-missing"].verdict is GroundingVerdict.MISSING


def test_unverifiable_outranks_moved_so_an_unknown_never_reads_as_a_known_move(
    session, workspace, grounding_repo, drifted
):
    """D4's fail-safe direction, at claim level: an anchor nothing could decide
    must not be swallowed by a sibling anchor that merely moved."""
    by_name = {a.qualified_name: a for a in drifted}
    unverifiable = SymbolAnchor(
        qualified_name=TS_ANCHOR_NAME,
        fingerprint="",
        path="web/thing.ts",
        checkout_identity=identify(grounding_repo),
        extractor_tier=ExtractorTier.REGEX_FALLBACK,
    )
    claims = [("mixed", encode_anchors([by_name["pkg.alpha.moving_house"], unverifiable]))]

    graded = claim_verdicts(session, workspace.id, grounding_repo, claims)

    assert graded["mixed"].verdict is GroundingVerdict.UNVERIFIABLE
    assert graded["mixed"].reason
    assert len(graded["mixed"].checks) == 2


def test_a_claim_with_no_anchors_gets_no_entry_at_all(session, workspace, grounding_repo, drifted):
    """Not a GROUNDED entry and not an UNVERIFIABLE one. An unanchored claim
    was never grounded, so it has nothing to say and every read path must be
    able to tell that from a claim whose anchors all passed."""
    graded = claim_verdicts(
        session,
        workspace.id,
        grounding_repo,
        [("ungrounded", ""), ("empty-list", "[]"), ("anchored", encode_anchors(drifted[:1]))],
    )

    assert set(graded) == {"anchored"}


def test_verdicts_are_batched_rather_than_queried_per_anchor(
    session, workspace, grounding_repo, drifted, recorded_sql
):
    """R3's BATCHED contract. The briefing asks for up to five claims' anchors on
    the path that assembles every non-verify prompt in the system; one query per
    anchor is how a read-time annotation becomes a per-prompt cost nobody
    budgeted."""
    # Bound before the measurement: `workspace` was expired by the build's
    # commit, so reading `.id` inside the window records an ORM refresh of
    # an unrelated row as if `verdicts_for` had issued it.
    workspace_id = workspace.id

    recorded_sql.clear()
    verdicts_for(session, workspace_id, grounding_repo, drifted[:1])
    for_one = len(recorded_sql)

    recorded_sql.clear()
    verdicts_for(session, workspace_id, grounding_repo, drifted)
    for_four = len(recorded_sql)

    assert for_one == for_four
    assert for_four <= 5


# ==========================================================================
# Criterion 3 — no LLM, no network
# ==========================================================================

_FORBIDDEN_IMPORTS = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "http",
    "socket",
    "openai",
    "anthropic",
    "loregarden.agents",
    "loregarden.mcp",
    "loregarden.services.cli_agent_runner",
    "loregarden.services.agent_turn_runner",
)


def test_grounding_reaches_no_http_client_and_no_agent_module():
    """Criterion 3's import-closure assertion. The verdict is a symbol-map
    lookup: a module that CAN reach an agent is one refactor away from asking
    one, and the whole claim of this ticket is that nobody has to."""
    source = (PACKAGE_ROOT / "grounding.py").read_text(encoding="utf-8")

    for name in imported_names(source):
        for forbidden in _FORBIDDEN_IMPORTS:
            assert not (name == forbidden or name.startswith(f"{forbidden}.")), (
                f"grounding.py imports {name}"
            )


def test_the_moved_tie_break_is_documented_where_the_collision_is_accepted():
    """Risk 3: a trivial body collides in the partition-wide fingerprint lookup,
    so MOVED can name the wrong one of two locations. The verdict is still
    right and the `(path, line)` order makes the choice deterministic — and the
    module's docstring is where that is admitted rather than discovered."""
    doc = (PACKAGE_ROOT / "grounding.py").read_text(encoding="utf-8").split('"""')[1]

    assert "path" in doc and "line" in doc


def test_the_package_exports_the_two_new_entry_points():
    """R3: the package's "exactly the N functions" line grows by this ticket's
    two. Consumers import from `services.symbol_map` and nothing deeper, so a
    function the package does not export is one every caller reaches around.

    The whole set is asserted rather than only the two, so a later ticket
    cannot add a third entry point without this test naming it."""
    assert set(symbol_map_package.__all__) == {
        "build_symbol_map",
        "claim_verdicts",
        "definitions_for",
        "map_status",
        "outline_for",
        "refresh_at_run_entry",
        "refresh_symbol_map",
        "resolve_anchors",
        "suggest_names",
    }


def test_the_check_value_is_frozen():
    """A verdict handed to a read path must not be editable by it: a briefing
    that could rewrite a CHANGED into a GROUNDED is the soft-invalidation
    contract of ticket 182 undone in one line."""
    with pytest.raises(Exception):  # noqa: B017 - dataclasses raise FrozenInstanceError
        GroundingCheck(
            verdict=GroundingVerdict.GROUNDED, qualified_name="a.b", location="", reason=""
        ).verdict = GroundingVerdict.MISSING
