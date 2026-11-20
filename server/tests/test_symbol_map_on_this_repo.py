"""The symbol map built against this checkout (lg-code-knowledge-281, R7).

Two things only, both of which need the real repository and neither of which a
`tmp_path` fixture can prove:

1. **R7.2 — the build writes nothing into the tree.** Two wrong shapes pass
   review. Asserting `git status --porcelain == ""` against the working
   checkout fails by construction during an orchestration run, which has
   uncommitted work; asserting it against a throwaway fixture repo proves the
   build wrote nothing *there*, which is not the claim. So the porcelain is
   captured with an explicit `cwd` BEFORE and AFTER a real build and the two
   outputs are asserted EQUAL — an empty diff, not an empty status.

2. **R7.1 / R7.3 — the AC11 measurement, re-produced.** Build time, row count
   and one partition's on-disk bytes are measured into evidence before the
   migration is written, because 481 v4 section 4 names that measurement as
   the only thing that reverses the per-checkout partition key the migration
   encodes. A number that lives only in an evidence artifact goes stale
   silently; the assertions below are how it cannot.

Kept to this file, and to these assertions, so the fast suite stays
`tmp_path`-only and the pre-push selection graph is honest about what the slow
one costs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import (
    Freshness,
    ParseStatus,
    SymbolMapFile,
    SymbolMapPartition,
    Workspace,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.symbol_map import (
    build_symbol_map,
    definitions_for,
    map_status,
    outline_for,
    refresh_symbol_map,
)
from loregarden.services.symbol_map import refresh as refresh_module
from loregarden.services.symbol_map.checkout_identity import identify
from sqlmodel import Session, select

#: MEASURED, not estimated. One partition built on this repository at HEAD
#: ca54966801 on 2026-11-17, into a scratch SQLite carrying the final schema
#: before the migration was written (AC11 / R7.1, evidence artifact
#: f983c877-d074-4f83-bc3b-56f672d2e0ae):
#:
#:     files_seen 3,479   symbol_rows 33,583   unsupported 448
#:     parse_failures 0   elapsed 7.9 s        partition 18.78 MB
#:
#: The plan's section 0 estimate of 45-50k symbols was an upper bound over
#: TypeScript declarations that counted indented and commented-out ones the
#: shipped tier does not claim; its 1.9-2.7 s timed extraction alone, with no
#: git read, no 3,479 file reads and no SQLite write.
#:
#: Each bound below fails on a different regression, and each is wide enough
#: that ordinary growth does not fail the suite: a collapsed file
#: enumeration, an extractor that stops producing rows, a build slow enough
#: to be felt on a run's critical path (7x headroom, because this suite runs
#: under xdist on a shared machine), and a row width that would make
#: per-checkout keying the wrong trade.
EXPECTED_MIN_FILES = 3_000
EXPECTED_MIN_SYMBOLS = 28_000
EXPECTED_MAX_SECONDS = 60.0
#: Bytes added to the database by one partition. 18.78 MB measured; the
#: ceiling is the term that prices per-checkout keying across the ~83 live
#: checkouts, so a threefold regression here is a design question and not a
#: slow test. Most of the present width is the 36-character uuid4 string
#: primary keys, three per symbol row, plus three indexes on the symbol
#: table — narrowing them is additive and does not reverse the partition key.
EXPECTED_MAX_PARTITION_BYTES = 60 * 1024 * 1024


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session: Session) -> Workspace:
    ws = Workspace(slug="this-checkout", name="this-checkout", repo_path="")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="other_workspace")
def other_workspace_fixture(session: Session) -> Workspace:
    """A second workspace, so the AC10 measurement can hold an ancestor
    partition for the seeded case without the unseeded case finding it.

    Seeding looks for a partition of the SAME workspace, so "no ancestor" and
    "an ancestor at this commit" are two workspaces and not two moments.
    """
    ws = Workspace(slug="this-checkout-ancestor", name="this-checkout-ancestor", repo_path="")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


#: The checkout holding THIS file: `server/tests/x.py` -> up three.
#:
#: Deliberately not `settings.repo_root`. That field is pydantic-settings
#: under `env_prefix="LOREGARDEN_"`, so `LOREGARDEN_REPO_ROOT` — which the
#: running dev server exports, pointing at the PRIMARY checkout, and which
#: `land_verification.run_verify_command` passes straight through, since
#: `scrubbed_git_env` strips only git's own location vars — silently
#: redirects every assertion in this file at another branch's source. The
#: failure that surfaced it was loud (a symbol this branch added, looked up
#: in a checkout that does not have it). The three that did not are the
#: reason this constant exists: the AC5 porcelain delta and the AC11
#: measurement bounds both stayed GREEN against the wrong tree, proving
#: nothing about the branch under test. A test that asserts against its own
#: branch's source must locate that source the one way no environment can
#: override.
THIS_CHECKOUT = Path(__file__).resolve().parents[2]


@pytest.fixture(name="repo_root")
def repo_root_fixture() -> Path:
    if not (THIS_CHECKOUT / ".git").exists():
        pytest.skip("this checkout is not a git repository")
    return THIS_CHECKOUT


def _porcelain(root: Path) -> list[str]:
    """`git status --porcelain` with an explicit cwd.

    Explicit because `run_git` defaults `cwd=None` — the process's working
    directory — and a test that reads the porcelain of whatever directory
    pytest happened to start in is asserting nothing about this build.
    """
    result = run_git(
        ["status", "--porcelain"], cwd=root, check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return sorted(result.stdout.splitlines())


def _database_bytes(session: Session) -> int:
    conn = session.connection()
    page_count = conn.exec_driver_sql("PRAGMA page_count").scalar_one()
    page_size = conn.exec_driver_sql("PRAGMA page_size").scalar_one()
    return int(page_count) * int(page_size)


def test_a_build_in_this_checkout_leaves_git_status_exactly_as_it_found_it(
    session, workspace, repo_root
):
    """AC5 / R7.2. A delta, not an empty status: during the implement stage
    this worktree legitimately holds the implementer's own new files, and a
    test that demanded a clean tree would be deleted rather than satisfied.

    Verified already safe on one axis: `data/` is gitignored, so the database
    cannot dirty the status. What this catches is a cache, a temp file or a
    log the build decides to write beside the source it is reading.
    """
    before = _porcelain(repo_root)

    build_symbol_map(session, workspace.id, repo_root)

    assert _porcelain(repo_root) == before


def test_the_build_over_this_checkout_reproduces_the_recorded_measurement(
    session, workspace, repo_root
):
    """AC11 / R7.1, R7.3. The numbers attached as evidence, asserted where
    they cannot go stale. Each bound fails on a different regression: a
    collapsed file enumeration, an extractor that stops producing rows, a
    build slow enough to be felt on a run's critical path, and a row width
    that would make per-checkout keying the wrong trade.
    """
    bytes_before = _database_bytes(session)

    report = build_symbol_map(session, workspace.id, repo_root)

    assert report.files_seen >= EXPECTED_MIN_FILES
    assert report.symbol_rows >= EXPECTED_MIN_SYMBOLS
    assert report.elapsed_seconds < EXPECTED_MAX_SECONDS
    assert report.head_sha
    assert report.checkout_identity == identify(repo_root)
    assert _database_bytes(session) - bytes_before < EXPECTED_MAX_PARTITION_BYTES


def test_the_stored_rows_agree_with_the_report_on_this_checkouts_scale(
    session, workspace, repo_root
):
    """The same counts, read back from the database rather than from the
    object the build returned. A report computed from the extraction and never
    compared to the write cannot tell a successful build from a partial one.
    """
    report = build_symbol_map(session, workspace.id, repo_root)
    partition = session.exec(
        select(SymbolMapPartition).where(
            SymbolMapPartition.workspace_id == workspace.id,
            SymbolMapPartition.checkout_identity == identify(repo_root),
        )
    ).one()
    rows = session.exec(
        select(SymbolMapFile).where(SymbolMapFile.partition_id == partition.id)
    ).all()

    assert len(rows) == report.files_seen
    assert partition.head_sha == report.head_sha
    assert sum(1 for row in rows if row.parse_status == ParseStatus.UNSUPPORTED_LANGUAGE) == (
        report.unsupported_files
    )
    assert sum(1 for row in rows if row.parse_status == ParseStatus.PARSE_FAILED) == (
        report.parse_failures
    )
    assert report.parse_failures == 0, (
        "verified at HEAD a4d45f9f8f: no tracked source in this checkout fails to parse "
        "under the 3.11 this suite runs on. A non-zero count here is either a real "
        "unparseable file or the interpreter guard (R3.5) failing to fire."
    )


def test_a_symbol_this_suite_can_name_is_findable_at_its_real_line(session, workspace, repo_root):
    """The end-to-end claim, stated against a definition the test can verify
    independently: the map's line number must be the line the file actually
    holds. A map that is confidently wrong about a line is worse than no map,
    which is the whole reason the sha stamp exists.
    """
    build_symbol_map(session, workspace.id, repo_root)
    target = "server/loregarden/services/symbol_map/checkout_identity.py"

    read = definitions_for(session, workspace.id, repo_root, "identify")
    outline = outline_for(session, workspace.id, repo_root, target)

    sites = [row for row in read.rows if row.path == target]
    assert len(sites) == 1
    line = sites[0].line
    source_lines = (repo_root / target).read_text(encoding="utf-8").splitlines()
    assert source_lines[line - 1].startswith("def identify(")
    assert [row.line for row in outline.rows] == sorted(row.line for row in outline.rows)


# ==========================================================================
# lg-code-knowledge-284, AC10 — what the refresh adds to a dispatch
#
# Three numbers, measured here rather than estimated, because the ticket's
# whole trade turns on which of them is on the critical path:
#
#   cold without seed   a checkout nothing has mapped, full parse
#   cold with seed      a fresh worktree copying an ancestor's rows, then
#                       re-analyzing only what moved since that ancestor
#   warm incremental    the ordinary case, a handful of edited files
#
# **The decision this records.** The cold case STAYS on the critical path.
# It is paid once per worktree, seeding is what keeps it off the dominant
# path (`symbol_map_partitions` held 0 rows in production against ~94 live
# worktrees, and worktrees are cut per ticket), and moving it to a thread or
# a queue is out of scope for this ticket by name. If the measured
# cold-with-seed number is one anyone would feel after seeding, the follow-up
# ticket is filed WITH the number on it — not a budget raised here.
#
# The budgets below are ceilings, not measurements: wide enough that ordinary
# repository growth does not fail the suite, tight enough that each one fails
# on a different regression. The implementer re-measures all three on this
# checkout and attaches them with `loregarden_attach_evidence`, together with
# one read-only count of `symbol_map_partitions` against live worktrees.
# ==========================================================================

#: 7.9 s measured for a full build at HEAD ca54966801; the same 7x headroom
#: EXPECTED_MAX_SECONDS carries, because this suite runs under xdist on a
#: shared machine. A cold unseeded refresh IS a full build, so a number above
#: this is a refresh doing more work than the build it replaces.
COLD_WITHOUT_SEED_BUDGET_SECONDS = 60.0
#: Seeding copies rows inside SQLite and parses only the delta from the
#: ancestor. Against an ancestor at the SAME commit the delta is empty, so
#: this is the row copy and two git reads and nothing else.
COLD_WITH_SEED_BUDGET_SECONDS = 25.0
#: The dominant case once a worktree has been dispatched into once. This is
#: the number that would be felt, because it is paid on every stage.
WARM_INCREMENTAL_BUDGET_SECONDS = 10.0
#: A realistic changed-file set for the warm case: a stage that edited a
#: dozen modules is an ordinary implement round in this repository.
WARM_CHANGED_FILES = 12


@pytest.fixture(name="measurement_worktree")
def measurement_worktree_fixture(tmp_path: Path, repo_root: Path):
    """A second checkout of THIS repository, detached at the same commit.

    Two checkouts is the only way to measure the seeded cold path honestly:
    seeding is keyed on `checkout_identity`, so one root cannot play both
    parts. Built in a fixture and removed in its teardown — a `git worktree
    add` that leaks leaves an entry in this checkout's admin data that every
    later `git worktree list` carries.
    """
    target = tmp_path / "measured-checkout"
    added = run_git(
        ["worktree", "add", "-q", "--detach", str(target), "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        pytest.skip(f"could not cut a worktree of this checkout: {added.stderr.strip()}")
    try:
        yield target
    finally:
        run_git(
            ["worktree", "remove", "--force", str(target)],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )


def _touch_sources(root: Path, count: int) -> list[str]:
    """Append a comment to *count* tracked Python modules, and leave them UNCOMMITTED.

    Uncommitted is the realistic shape, not a shortcut: the delta oracle
    diffs the stored sha against the WORKING TREE precisely because every
    mid-stage edit this control plane makes is uncommitted, so a warm pass
    measured over committed edits would measure the easier half of its own
    contract.

    It also keeps git's hooks out of a latency measurement. A freshly cut
    worktree has no `server/.venv`, so a `git commit` here runs lefthook's
    pre-commit, which fails on the missing environment and — on the gates
    that do find one — builds a throwaway virtualenv inside the tree being
    timed.
    """
    paths = sorted(
        str(path.relative_to(root))
        for path in (root / "server" / "loregarden" / "services").glob("*.py")
    )[:count]
    assert len(paths) == count, f"only {len(paths)} modules to edit"
    for relative in paths:
        with (root / relative).open("a", encoding="utf-8") as handle:
            handle.write("\n# measured edit (lg-code-knowledge-284)\n")
    return paths


def test_the_three_refresh_latencies_on_this_checkout_stay_inside_their_budgets(
    session, workspace, other_workspace, repo_root, measurement_worktree
):
    """AC10. The measurement, asserted where it cannot go stale.

    Each budget fails on a different regression: a cold refresh that does more
    work than the full build it replaces; a seed that re-parses instead of
    copying; and a warm pass whose cost scales with the repository rather than
    with the diff. The last is the one that would be felt, because it is paid
    on every stage of every ticket.
    """
    cold = refresh_symbol_map(session, workspace.id, measurement_worktree)
    assert cold.ok is True
    assert cold.cold is True and cold.seeded is False

    build_symbol_map(session, other_workspace.id, repo_root)
    seeded = refresh_symbol_map(session, other_workspace.id, measurement_worktree)
    assert seeded.ok is True
    assert seeded.cold is True and seeded.seeded is True
    assert seeded.files_reanalyzed == 0, (
        "the ancestor is at the same commit, so nothing moved — a non-zero count "
        "here is the mtime trap AC12 names, arriving by another route"
    )

    edited = _touch_sources(measurement_worktree, WARM_CHANGED_FILES)
    warm = refresh_symbol_map(session, other_workspace.id, measurement_worktree)

    assert warm.ok is True
    assert warm.files_reanalyzed == len(edited)
    assert cold.elapsed_ms / 1000 < COLD_WITHOUT_SEED_BUDGET_SECONDS
    assert seeded.elapsed_ms / 1000 < COLD_WITH_SEED_BUDGET_SECONDS
    assert warm.elapsed_ms / 1000 < WARM_INCREMENTAL_BUDGET_SECONDS
    assert seeded.elapsed_ms < cold.elapsed_ms, (
        "seeding is in scope precisely because it is cheaper than a cold build; "
        "if it is not, the ~98-worktree cold path is still the dominant one"
    )
    assert refresh_module.COLD_BUILD_BUDGET_MS > 0


def test_a_refresh_of_this_checkout_leaves_git_status_exactly_as_it_found_it(
    session, workspace, measurement_worktree
):
    """AC8, on a real checkout and as a DELTA rather than an empty status.

    The `tmp_path` half of this claim lives in `test_symbol_map_refresh.py`;
    this half catches a cache, a temp file or a log the refresh decides to
    write beside the source it is reading, at this repository's scale.
    """
    before = _porcelain(measurement_worktree)

    refresh_symbol_map(session, workspace.id, measurement_worktree)

    assert _porcelain(measurement_worktree) == before


def test_a_refreshed_checkout_reads_back_as_fresh_at_its_real_lines(
    session, workspace, measurement_worktree
):
    """The end-to-end claim for the writer, stated against a definition the
    test verifies independently: after a refresh the map must say MATCHES and
    the line it names must be the line the file holds."""
    refresh_symbol_map(session, workspace.id, measurement_worktree)
    target = "server/loregarden/services/symbol_map/checkout_identity.py"

    assert map_status(session, workspace.id, measurement_worktree).freshness is (Freshness.MATCHES)
    sites = [
        row
        for row in definitions_for(session, workspace.id, measurement_worktree, "identify").rows
        if row.path == target
    ]
    assert len(sites) == 1
    source_lines = (measurement_worktree / target).read_text(encoding="utf-8").splitlines()
    assert source_lines[sites[0].line - 1].startswith("def identify(")
