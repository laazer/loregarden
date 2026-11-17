"""Behavioral git signals: churn, co-change, authored-line ownership, bug-fix history.

Ticket lg-code-knowledge-299, spec requirements R1-R12. The package under test
is `loregarden.services.code_signals`; it does not exist until the implement
stage, so every test here is red until then. That is the point of the stage.

**Every repository used by a test is built by a fixture, never in a test body.**
A broken setup inside a test body fails in the call phase and is
indistinguishable from a healthy failure; a fixture that breaks is an ERROR,
which nothing absorbs. This repo has already shipped a module that proved
nothing while looking green for exactly that reason (CLAUDE.md, "A finding is a
claim until it is runnable"). The fixture shape follows
`tests/test_git_boundary.py`.

Fixture repos commit at explicit `GIT_COMMITTER_DATE` / `GIT_AUTHOR_DATE`
offsets from now, so the window tests sit far from the 180-day boundary and
cannot flake on it. `run_git` is used rather than bare `subprocess` so an
ambient `GIT_DIR` from a hook context cannot rebind a fixture repo to the
checkout it fired for.
"""

from __future__ import annotations

import ast
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from loregarden.config import settings
from loregarden.db.migrations import MIGRATIONS
from loregarden.db.versions import code_signals as code_signals_migration
from loregarden.models.domain import (
    EmptyBasis,
    FileCoChangePair,
    FileGitSignal,
    PathKind,
    SignalKind,
    Workspace,
)
from loregarden.services.code_signals import (
    GitSignalsParseError,
    GitSignalsReadError,
    SignalConfig,
    compute_git_signals,
    git_history,
    refresh_workspace_signals,
    signals_for_path,
    top_files_by,
)
from loregarden.services.code_signals import classify as classify_module
from loregarden.services.code_signals import compute as compute_module
from loregarden.services.code_signals import config as config_module
from loregarden.services.code_signals.classify import classify_path, is_fix_subject
from loregarden.services.code_signals.git_history import parse_log_stream
from loregarden.services.git_subprocess import run_git
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

PACKAGE_ROOT = Path(git_history.__file__).parent

ALICE = "alice@example.com"
BOB = "bob@example.com"
ZOE = "zoe@example.com"


# --------------------------------------------------------------------------
# Repository scaffolding. Used only by fixtures.
# --------------------------------------------------------------------------


def _git(
    root: Path,
    *args: str,
    when: str | None = None,
    author: str | None = None,
    check: bool = True,
):
    """One git command in *root*, with git's repo-binding env vars scrubbed.

    *check* is a parameter because a fixture sometimes needs a command that
    is MEANT to fail — a merge that conflicts on purpose. Spelling that as a
    bare call that raises would make the fixture an ERROR and the repository
    shape it was building unreachable.
    """
    env = dict(os.environ)
    if when is not None:
        env["GIT_COMMITTER_DATE"] = when
        env["GIT_AUTHOR_DATE"] = when
    if author is not None:
        env["GIT_AUTHOR_EMAIL"] = author
        env["GIT_COMMITTER_EMAIL"] = author
    return run_git(args, cwd=root, env=env, check=check, capture_output=True, text=True)


def _when(days_ago: float) -> str:
    """A git date string that many days before now, in UTC."""
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return f"@{int(moment.timestamp())} +0000"


def _init(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _lines(count: int, tag: str, start: int = 0) -> str:
    return "".join(f"{tag}-{start + i}\n" for i in range(count))


def _write(root: Path, rel: str, text_body: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text_body, encoding="utf-8")


def _append(root: Path, rel: str, count: int, tag: str) -> None:
    target = root / rel
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(existing + _lines(count, tag, start=existing.count("\n")), encoding="utf-8")


def _commit(root: Path, message: str, *, author: str, days_ago: float) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message, when=_when(days_ago), author=author)


# --------------------------------------------------------------------------
# Fixtures: one repository shape per behaviour under test.
# --------------------------------------------------------------------------


@pytest.fixture(name="config")
def config_fixture() -> SignalConfig:
    return SignalConfig()


@pytest.fixture(name="authored_repo")
def authored_repo_fixture(tmp_path: Path) -> Path:
    """Eight in-window commits with a hand-computed signal table.

    The expected values are asserted in `EXPECTED_SIGNALS` below; keep the two
    in step. `src/tie.py` is the deliberate two-author tie AC1 names: bob adds
    three lines first, alice adds three later, so insertion order and
    last-writer order both point at bob and only the tie rule points at alice.
    """
    root = _init(tmp_path / "authored")

    _write(root, "src/core.py", _lines(5, "core"))
    _write(root, "README.md", _lines(2, "readme"))
    _commit(root, "feat: add core", author=ALICE, days_ago=60)

    _append(root, "src/core.py", 2, "core")
    _commit(root, "fix(core): off by one", author=BOB, days_ago=50)

    _write(root, "src/helper.py", _lines(4, "help"))
    _append(root, "src/core.py", 1, "core")
    _commit(root, "feat: helper", author=ZOE, days_ago=40)

    _append(root, "src/helper.py", 3, "help")
    _append(root, "src/core.py", 1, "core")
    _commit(root, "fix: handle empty", author=ALICE, days_ago=30)

    _append(root, "src/helper.py", 1, "help")
    _append(root, "src/core.py", 1, "core")
    _commit(root, "fix(helper): guard", author=BOB, days_ago=20)

    _append(root, "README.md", 1, "readme")
    _commit(root, "docs: readme", author=ALICE, days_ago=15)

    _write(root, "src/tie.py", _lines(3, "tie"))
    _commit(root, "feat: tie part one", author=BOB, days_ago=12)

    _append(root, "src/tie.py", 3, "tie")
    _commit(root, "feat: tie part two", author=ALICE, days_ago=10)

    return root


#: path -> (churn_commits, churn_added, churn_deleted, top_author, share,
#:          distinct_authors, bugfix_commits)
EXPECTED_SIGNALS = {
    "src/core.py": (5, 10, 0, ALICE, 0.6, 3, 3),
    "src/helper.py": (3, 8, 0, ZOE, 0.5, 3, 2),
    "README.md": (2, 3, 0, ALICE, 1.0, 1, 0),
    "src/tie.py": (2, 6, 0, ALICE, 0.5, 2, 0),
}


@pytest.fixture(name="commitless_repo")
def commitless_repo_fixture(tmp_path: Path) -> Path:
    return _init(tmp_path / "commitless")


@pytest.fixture(name="uncommitted_repo")
def uncommitted_repo_fixture(tmp_path: Path) -> Path:
    """History, plus one file added to the working copy and never committed."""
    root = _init(tmp_path / "uncommitted")
    _write(root, "src/committed.py", _lines(3, "c"))
    _commit(root, "feat: committed", author=ALICE, days_ago=5)
    _write(root, "src/never_committed.py", _lines(3, "n"))
    return root


@pytest.fixture(name="not_a_repo")
def not_a_repo_fixture(tmp_path: Path) -> Path:
    """A directory that exists and holds no repository.

    The distinction matters: git only runs, and only produces an exit code
    and a stderr, when its working directory exists. A path that does not
    exist fails in `chdir` before git is reached, so a test that asserts an
    exit code against one is asserting a number the implementation had to
    invent.
    """
    root = tmp_path / "not-a-repo"
    root.mkdir()
    (root / "file.txt").write_text("not tracked by anything\n", encoding="utf-8")
    return root


@pytest.fixture(name="outside_window_repo")
def outside_window_repo_fixture(tmp_path: Path) -> Path:
    """One commit, dated well outside the default 180-day window."""
    root = _init(tmp_path / "outside")
    _write(root, "src/ancient.py", _lines(3, "a"))
    _commit(root, "feat: ancient", author=ALICE, days_ago=400)
    return root


@pytest.fixture(name="window_repo")
def window_repo_fixture(tmp_path: Path) -> Path:
    """One commit outside the default window and one inside it."""
    root = _init(tmp_path / "window")
    _write(root, "src/ancient.py", _lines(3, "a"))
    _commit(root, "feat: ancient", author=ALICE, days_ago=400)
    _write(root, "src/recent.py", _lines(2, "r"))
    _commit(root, "feat: recent", author=ALICE, days_ago=5)
    return root


@pytest.fixture(name="deletion_repo")
def deletion_repo_fixture(tmp_path: Path) -> Path:
    """A path whose only in-window history is a deletion: zero added lines."""
    root = _init(tmp_path / "deletion")
    _write(root, "src/doomed.py", _lines(4, "d"))
    _commit(root, "feat: doomed", author=ALICE, days_ago=400)
    (root / "src/doomed.py").unlink()
    _commit(root, "chore: drop doomed", author=BOB, days_ago=5)
    return root


@pytest.fixture(name="kindwise_fix_repo")
def kindwise_fix_repo_fixture(tmp_path: Path) -> Path:
    """Doc-only, test-only and config-only `fix:` commits, plus two controls.

    Mandatory rather than opportunistic: measured on this repository there are
    ZERO doc-only fix commits, so a test against real history would pass
    without exercising anything (spec R11).
    """
    root = _init(tmp_path / "kindwise")

    _write(root, "src/app.py", _lines(2, "app"))
    _write(root, "docs/guide.md", _lines(2, "doc"))
    _write(root, "server/tests/test_app.py", _lines(2, "t"))
    _write(root, "pyproject.toml", _lines(2, "cfg"))
    _commit(root, "feat: seed", author=ALICE, days_ago=60)

    _append(root, "docs/guide.md", 1, "doc")
    _commit(root, "fix(docs): typo", author=ALICE, days_ago=50)

    _append(root, "server/tests/test_app.py", 1, "t")
    _commit(root, "fix: flaky test", author=ALICE, days_ago=40)

    _append(root, "pyproject.toml", 1, "cfg")
    _commit(root, "fix(ci): bad key", author=ALICE, days_ago=30)

    _append(root, "src/app.py", 1, "app")
    _commit(root, "fix: real bug", author=ALICE, days_ago=20)

    _append(root, "src/app.py", 1, "app")
    _append(root, "docs/guide.md", 1, "doc")
    _commit(root, "fix(core): mixed source and doc", author=ALICE, days_ago=10)

    return root


@pytest.fixture(name="cochange_repo")
def cochange_repo_fixture(tmp_path: Path) -> Path:
    """Pairs at support 3 and 2, and one sweeping commit of six paths.

    (a, b) is seen three times, (c, d) twice, and s1..s6 share exactly one
    commit. That gives a boundary on both knobs: `min_support` separates (a, b)
    from (c, d), and `max_files_per_commit` at 5 vs 6 separates the sweep from
    everything else.
    """
    root = _init(tmp_path / "cochange")

    for index, days in enumerate((60, 50, 40)):
        _append(root, "a.py", 1, "a")
        _append(root, "b.py", 1, "b")
        _commit(root, f"feat: pair ab {index}", author=ALICE, days_ago=days)

    for index, days in enumerate((30, 20)):
        _append(root, "c.py", 1, "c")
        _append(root, "d.py", 1, "d")
        _commit(root, f"feat: pair cd {index}", author=ALICE, days_ago=days)

    for n in range(1, 7):
        _write(root, f"s{n}.py", _lines(1, f"s{n}"))
    _commit(root, "chore: sweep six files", author=ALICE, days_ago=10)

    return root


@pytest.fixture(name="hostile_path_repo")
def hostile_path_repo_fixture(tmp_path: Path) -> Path:
    """A space, a double quote, a non-ASCII character, and a binary file."""
    root = _init(tmp_path / "hostile")
    _write(root, 'dir with space/a "quoted" café.txt', _lines(2, "q"))
    _write(root, "plain.py", _lines(1, "p"))
    _commit(root, "feat: hostile paths", author=ALICE, days_ago=30)
    (root / "asset.bin").write_bytes(b"\x00\x01\x02\x03\x00\xff")
    _commit(root, "fix(assets): binary", author=BOB, days_ago=20)
    _git(root, "commit", "-q", "--allow-empty", "-m", "chore: nothing changed", when=_when(10))
    return root


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session: Session, authored_repo: Path) -> Workspace:
    ws = Workspace(slug="signals", name="signals", repo_path=str(authored_repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="other_workspace")
def other_workspace_fixture(session: Session) -> Workspace:
    ws = Workspace(slug="bystander", name="bystander", repo_path="")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="migrated_engine")
def migrated_engine_fixture(tmp_path: Path):
    """A database with every table *except* the two this ticket adds.

    Built by creating the full metadata and dropping the pair back off, so the
    migration runs against a realistic schema (workspaces exists, so the
    foreign key resolves) and still has work to do.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'migrate.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS file_co_change_pairs"))
        conn.execute(text("DROP TABLE IF EXISTS file_git_signals"))
    return engine


# --------------------------------------------------------------------------
# Small readers over the results.
# --------------------------------------------------------------------------


def _pairs(signals) -> dict[tuple[str, str], int]:
    return {(p.file_path, p.partner_path): p.support for p in signals.pairs}


def _indexes(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA index_list({table})"))}


def _columns(engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        return {row[1]: row[2] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _package_modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.glob("*.py"))


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):  # py-org: allow-isinstance - ast node dispatch
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance - ast node dispatch
            if node.module:
                names.add(node.module)
    return names


# ==========================================================================
# R1 / AC4 — SignalConfig
# ==========================================================================


def test_the_config_carries_exactly_three_knobs_at_their_measured_defaults():
    cfg = SignalConfig()
    assert cfg.window_days == 180
    assert cfg.min_support == 3
    assert cfg.max_files_per_commit == 50
    assert set(SignalConfig.model_fields) == {"window_days", "min_support", "max_files_per_commit"}


def test_the_config_is_frozen():
    cfg = SignalConfig()
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError on a frozen set
        cfg.window_days = 2
    assert SignalConfig(window_days=1).window_days == 1
    assert cfg.model_copy(update={"window_days": 1}).window_days == 1


@pytest.mark.parametrize("field", ["window_days", "min_support", "max_files_per_commit"])
def test_each_default_carries_its_measurement_in_a_comment(field):
    """AC4 asks for the measurement beside the default, not for particular words."""
    source = Path(config_module.__file__).read_text(encoding="utf-8").splitlines()
    declared = next(i for i, line in enumerate(source) if line.strip().startswith(f"{field}:"))
    preceding = [line.strip() for line in source[max(0, declared - 8) : declared]]
    assert any(line.startswith("#") for line in preceding), f"{field} has no comment above it"


# ==========================================================================
# R2 — PathKind classification
# ==========================================================================


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("server/tests/fixtures/data.json", PathKind.TEST),
        ("server/tests/test_app.py", PathKind.TEST),
        ("client/src/lib/__tests__/thing.test.ts", PathKind.TEST),
        ("client/src/lib/thing.spec.tsx", PathKind.TEST),
        ("docs/architecture.md", PathKind.DOC),
        ("AGENTS.md", PathKind.DOC),
        ("notes/readme.rst", PathKind.DOC),
        ("pyproject.toml", PathKind.CONFIG),
        (".github/workflows/ci.yml", PathKind.CONFIG),
        ("uv.lock", PathKind.CONFIG),
        ("Dockerfile", PathKind.CONFIG),
        ("server/loregarden/services/foo.py", PathKind.SOURCE),
        ("client/src/App.tsx", PathKind.SOURCE),
        ("tools/thing.zig", PathKind.SOURCE),
    ],
)
def test_path_kind_is_first_match_wins_in_the_normative_order(path, kind):
    assert classify_path(path) == kind


def test_an_unrecognised_extension_falls_through_to_source_rather_than_being_dropped():
    """SOURCE is the fallthrough. Treating unknown code as not-code would
    silently drop bug-fix signal for every language this list has not met."""
    assert classify_path("engine/renderer.wgsl") == PathKind.SOURCE


def test_a_fixture_under_tests_is_test_not_config():
    """The rule order is normative: TEST before CONFIG decides this one."""
    assert classify_path("server/tests/fixtures/x.json") == PathKind.TEST
    assert classify_path("config/x.json") == PathKind.CONFIG


# ==========================================================================
# R3 / AC3 — the fix-commit predicate
# ==========================================================================


@pytest.mark.parametrize(
    "subject",
    [
        "fix: thing",
        "fix(merge-queue): thing",
        "fix!: thing",
        "fix(api)!: thing",
        "FIX: thing",
        "Fix(core): thing",
        "bugfix: thing",
        "hotfix(db): thing",
        "patch: thing",
    ],
)
def test_a_conventional_fix_subject_matches(subject):
    assert is_fix_subject(subject) is True


@pytest.mark.parametrize(
    "subject",
    [
        "feat: this fixes the thing",
        "refactor: prefix the fixture",
        "chore: fix up later",
        "docs: fixing typos",
        "feat(fix): not a fix type",
        "prefix: thing",
        "",
    ],
)
def test_prose_containing_fix_does_not_match(subject):
    """The exact false positive `--grep='^fix'` produces: measured on this
    repository it returns 330 against 316 subjects that really start with fix."""
    assert is_fix_subject(subject) is False


def test_the_package_never_asks_git_to_grep():
    for module in _package_modules():
        source = module.read_text(encoding="utf-8")
        assert "--grep" not in source, f"{module.name} uses git log --grep"


# ==========================================================================
# R4 / AC12 — parsing, with no git process involved
# ==========================================================================

#: Captured from real git on 2026-11-17 with
#:   git log --no-merges --no-renames -z --numstat \
#:       --since='3650 days ago' --format='%x00LGCS%x00%H%x00%aE%x00%ct%x00%s'
#: against a repository built exactly like `hostile_path_repo`. Only the record
#: sentinel and the header field separator are parameterised, because the spec
#: leaves the sentinel to the implementer; every framing byte around them — the
#: NUL that `-z` puts after the header, the newline git emits before the numstat
#: block, the tabs, the per-path NUL terminator, the raw UTF-8 of the path — is
#: verbatim capture. `test_the_captured_framing_still_matches_real_git` re-reads
#: a live repository and fails if git ever changes that framing.
_CAPTURE_TEMPLATE = (
    b"@S@4505849e3eddef4c1ba7b5c25557f3dbd5b64c99@F@a@example.com@F@1767582245"
    b"@F@chore: nothing changed\x00"
    b"@S@41c56cd76dfccce9b9c1799f53b565147a9f06ad@F@b@example.com@F@1767495845"
    b"@F@fix(assets): binary\x00\n"
    b"-\t-\tasset.bin\x00"
    b"@S@c2452ff94443049904b3c6cfc2905e513e747837@F@a@example.com@F@1767409445"
    b"@F@feat: hostile paths\x00\n"
    b'2\t0\tdir with space/a "quoted" caf\xc3\xa9.txt\x00'
    b"1\t0\tplain.py\x00"
)

HOSTILE_PATH = 'dir with space/a "quoted" café.txt'


def _captured_stream() -> bytes:
    return _CAPTURE_TEMPLATE.replace(b"@S@", git_history.RECORD_SENTINEL).replace(
        b"@F@", git_history.FIELD_SEP
    )


@pytest.fixture(name="captured_stream")
def captured_stream_fixture() -> bytes:
    return _captured_stream()


def test_the_parser_reads_every_row_shape_without_a_git_process(captured_stream):
    records = parse_log_stream(captured_stream)

    assert [r.sha[:8] for r in records] == ["4505849e", "41c56cd7", "c2452ff9"]
    assert [r.author_email for r in records] == [
        "a@example.com",
        "b@example.com",
        "a@example.com",
    ]
    assert [r.subject for r in records] == [
        "chore: nothing changed",
        "fix(assets): binary",
        "feat: hostile paths",
    ]
    assert records[0].files == []
    assert records[1].files == [("asset.bin", 0, 0)]
    assert records[2].files == [(HOSTILE_PATH, 2, 0), ("plain.py", 1, 0)]


def test_a_binary_row_is_zero_added_zero_deleted_and_still_counts_as_a_touch(captured_stream):
    binary_commit = parse_log_stream(captured_stream)[1]
    assert binary_commit.files == [("asset.bin", 0, 0)]


def test_the_committer_timestamp_is_read_as_aware_utc(captured_stream):
    first = parse_log_stream(captured_stream)[0]
    assert first.committed_at == datetime.fromtimestamp(1767582245, tz=UTC)
    assert first.committed_at.tzinfo is not None


def test_an_unrecognised_row_raises_and_names_the_row(captured_stream):
    """Not skipped, and not mis-keyed as a path: a silent wrong path key is the
    failure this guard exists for."""
    corrupted = captured_stream.replace(b"1\t0\tplain.py\x00", b"not a numstat row\x00")

    with pytest.raises(GitSignalsParseError) as caught:
        parse_log_stream(corrupted)

    assert "not a numstat row" in str(caught.value)


def test_a_rename_shaped_row_is_not_silently_accepted(captured_stream):
    """`--no-renames` makes the rename shape structurally absent (plan D8).

    Under `-z` a rename is NOT `{old => new}`: git emits an EMPTY path field
    followed by the two paths as separate NUL-delimited chunks, captured here
    as `0\\t0\\t\\0old.py\\0new.py\\0`. A parser that split naively would key a
    row on the empty string and then on a bare `new.py` with no counts. If the
    flag is ever dropped this must shout rather than invent a path key.
    """
    corrupted = captured_stream.replace(b"1\t0\tplain.py\x00", b"0\t0\t\x00old.py\x00new.py\x00")

    with pytest.raises(GitSignalsParseError):
        parse_log_stream(corrupted)


def test_the_log_command_carries_the_flags_the_correctness_rests_on():
    args = git_history.log_args(SignalConfig())
    assert "--no-renames" in args
    assert "-z" in args
    assert "--numstat" in args
    assert "--no-merges" in args
    assert any(arg.startswith("--since=") and "180" in arg for arg in args)
    assert not any("--grep" in arg for arg in args)


def test_the_captured_framing_still_matches_real_git(hostile_path_repo, config):
    """The one test that pins the literal above to reality. If git changes how
    `-z --numstat --format` are framed, this fails and the capture is stale."""
    signals = compute_git_signals(hostile_path_repo, config)

    assert HOSTILE_PATH in signals.files
    assert signals.files["asset.bin"].churn_added == 0
    assert signals.files["asset.bin"].churn_commits == 1
    assert signals.commits_read == 3


# ==========================================================================
# R5 / AC9 — empty and failed must not collapse
# ==========================================================================


def test_a_commitless_repository_is_empty_with_a_basis_and_no_exception(commitless_repo, config):
    signals = compute_git_signals(commitless_repo, config)

    assert signals.empty_basis == EmptyBasis.NO_HISTORY
    assert signals.commits_read == 0
    assert signals.records == []
    assert signals.files == {}
    assert signals.head_sha == ""


def test_history_entirely_outside_the_window_is_a_different_basis(outside_window_repo, config):
    signals = compute_git_signals(outside_window_repo, config)

    assert signals.empty_basis == EmptyBasis.NO_COMMITS_IN_WINDOW
    assert signals.commits_read == 0
    assert signals.files == {}
    assert signals.head_sha != ""


def test_the_empty_basis_vocabulary_has_exactly_the_two_reachable_members():
    """`NO_SOURCE_FILES` was dropped in the spec stage: rows are written for
    every path in the window regardless of PathKind, so nothing can set it."""
    assert {member.value for member in EmptyBasis} == {
        EmptyBasis.NO_HISTORY.value,
        EmptyBasis.NO_COMMITS_IN_WINDOW.value,
    }


def test_a_populated_window_has_no_empty_basis(authored_repo, config):
    assert compute_git_signals(authored_repo, config).empty_basis is None


def test_a_directory_that_is_not_a_repository_raises_and_carries_git_s_own_exit(not_a_repo, config):
    """Exit 128 means both 'no commits yet' and 'not a git repository', so the
    classification cannot be made on the exit code alone — and the error must
    carry the code and stderr git really produced, not a stand-in.

    This runs against a directory that EXISTS on purpose. Against one that does
    not, `subprocess` fails in `chdir` and git never runs, so there is no exit
    code to carry and the assertion below could only be met by inventing one.
    """
    with pytest.raises(GitSignalsReadError) as caught:
        compute_git_signals(not_a_repo, config)

    assert caught.value.returncode != 0
    assert caught.value.stderr
    assert str(caught.value.returncode) in str(caught.value)


def test_a_repo_root_that_does_not_exist_is_loud_rather_than_empty(tmp_path, config):
    """It must not come back as a successful empty result: returning
    `GitSignals` here would let `refresh_workspace_signals` delete a
    workspace's good rows because somebody mistyped a repo path.

    AC13 narrowed this from `(GitSignalsReadError, OSError)` to the one class
    the package's contract names; the full case, including the not-a-directory
    sibling and the no-rows-deleted property, lives in
    `test_code_signals_error_contract.py`.
    """
    with pytest.raises(GitSignalsReadError):
        compute_git_signals(tmp_path / "definitely-not-a-repo", config)


def test_a_file_never_committed_produces_no_row(uncommitted_repo, config):
    signals = compute_git_signals(uncommitted_repo, config)

    assert "src/committed.py" in signals.files
    assert "src/never_committed.py" not in signals.files


# ==========================================================================
# R6 / AC1 — the four reducers
# ==========================================================================


@pytest.mark.parametrize(("path", "expected"), sorted(EXPECTED_SIGNALS.items()))
def test_every_signal_group_matches_the_scripted_history(authored_repo, config, path, expected):
    commits, added, deleted, author, share, authors, bugfixes = expected
    row = compute_git_signals(authored_repo, config).files[path]

    assert row.file_path == path
    assert (row.churn_commits, row.churn_added, row.churn_deleted) == (commits, added, deleted)
    assert row.top_author == author
    assert row.top_author_added_share == pytest.approx(share)
    assert row.distinct_authors == authors
    assert row.bugfix_commits == bugfixes


def test_the_window_sees_every_commit_and_every_path(authored_repo, config):
    signals = compute_git_signals(authored_repo, config)
    assert signals.commits_read == 8
    assert set(signals.files) == set(EXPECTED_SIGNALS)


def test_last_commit_at_is_the_newest_touch_not_the_newest_commit(authored_repo, config):
    files = compute_git_signals(authored_repo, config).files

    assert files["src/core.py"].last_commit_at < files["src/tie.py"].last_commit_at
    assert files["README.md"].last_commit_at < files["src/tie.py"].last_commit_at
    assert files["src/core.py"].last_commit_at.tzinfo is not None


def test_last_bugfix_at_is_set_for_fixed_paths_and_null_for_the_rest(authored_repo, config):
    files = compute_git_signals(authored_repo, config).files

    assert files["src/core.py"].last_bugfix_at is not None
    assert files["src/core.py"].last_bugfix_at == files["src/helper.py"].last_bugfix_at
    assert files["README.md"].last_bugfix_at is None
    assert files["src/tie.py"].last_bugfix_at is None


def test_a_two_author_tie_breaks_to_the_lexicographically_smallest_email(authored_repo, config):
    """Both authors added three lines; bob got there first and committed last.
    Only the tie rule picks alice, so this fails if the rule is dropped."""
    row = compute_git_signals(authored_repo, config).files["src/tie.py"]

    assert row.top_author == ALICE
    assert row.top_author_added_share == pytest.approx(0.5)


def test_the_computation_is_identical_across_two_runs(authored_repo, config):
    first = compute_git_signals(authored_repo, config)
    second = compute_git_signals(authored_repo, config)

    assert first.files == second.files
    assert _pairs(first) == _pairs(second)
    assert first.commits_read == second.commits_read


def test_a_deletion_only_history_has_no_owner_rather_than_an_arbitrary_one(deletion_repo, config):
    row = compute_git_signals(deletion_repo, config).files["src/doomed.py"]

    assert row.churn_added == 0
    assert row.churn_deleted == 4
    assert row.churn_commits == 1
    assert row.top_author == ""
    assert row.top_author_added_share == 0.0
    assert row.distinct_authors == 1


def test_rows_exist_for_every_path_kind_not_only_source(authored_repo, config):
    """PathKind gates bug-fix attribution only. A doc with churn still gets a row."""
    files = compute_git_signals(authored_repo, config).files
    assert files["README.md"].churn_commits == 2
    assert files["README.md"].bugfix_commits == 0


# --- AC3: the three kind-scoped fix tests, each on its own fixture repo ----


def test_a_doc_only_fix_commit_raises_no_files_bugfix_count(kindwise_fix_repo, config):
    files = compute_git_signals(kindwise_fix_repo, config).files

    assert files["docs/guide.md"].bugfix_commits == 0
    assert files["docs/guide.md"].last_bugfix_at is None
    assert files["docs/guide.md"].churn_commits == 3


def test_a_test_only_fix_commit_raises_no_files_bugfix_count(kindwise_fix_repo, config):
    files = compute_git_signals(kindwise_fix_repo, config).files

    assert files["server/tests/test_app.py"].bugfix_commits == 0
    assert files["server/tests/test_app.py"].last_bugfix_at is None
    assert files["server/tests/test_app.py"].churn_commits == 2


def test_a_config_only_fix_commit_raises_no_files_bugfix_count(kindwise_fix_repo, config):
    files = compute_git_signals(kindwise_fix_repo, config).files

    assert files["pyproject.toml"].bugfix_commits == 0
    assert files["pyproject.toml"].last_bugfix_at is None
    assert files["pyproject.toml"].churn_commits == 2


def test_a_mixed_fix_commit_credits_its_source_path_and_not_its_doc(kindwise_fix_repo, config):
    """Spec S3: the exclusion is path-level, so the mixed commit is not wholly
    included nor wholly dropped."""
    files = compute_git_signals(kindwise_fix_repo, config).files

    assert files["src/app.py"].bugfix_commits == 2
    assert files["docs/guide.md"].bugfix_commits == 0


# --- AC4: each knob changes the output ------------------------------------


def test_co_change_pairs_are_emitted_in_both_directions(cochange_repo, config):
    pairs = _pairs(compute_git_signals(cochange_repo, config))

    assert pairs == {("a.py", "b.py"): 3, ("b.py", "a.py"): 3}


def test_a_pair_below_min_support_is_absent_and_at_min_support_is_present(cochange_repo):
    at_three = _pairs(compute_git_signals(cochange_repo, SignalConfig(min_support=3)))
    at_two = _pairs(compute_git_signals(cochange_repo, SignalConfig(min_support=2)))

    assert ("c.py", "d.py") not in at_three
    assert at_two[("c.py", "d.py")] == 2
    assert at_two[("d.py", "c.py")] == 2


def test_a_commit_above_the_file_cap_contributes_no_pairs(cochange_repo):
    cfg = SignalConfig(min_support=1, max_files_per_commit=5)
    pairs = _pairs(compute_git_signals(cochange_repo, cfg))

    assert not any(key[0].startswith("s") for key in pairs)
    assert pairs[("a.py", "b.py")] == 3


def test_the_same_commit_at_the_cap_contributes_its_pairs(cochange_repo):
    cfg = SignalConfig(min_support=1, max_files_per_commit=6)
    pairs = _pairs(compute_git_signals(cochange_repo, cfg))

    sweep = {key: value for key, value in pairs.items() if key[0].startswith("s")}
    assert len(sweep) == 30  # 15 unordered pairs, both directions
    assert all(value == 1 for value in sweep.values())


def test_a_single_file_commit_contributes_no_pair(cochange_repo):
    cfg = SignalConfig(min_support=1, max_files_per_commit=50)
    pairs = _pairs(compute_git_signals(cochange_repo, cfg))

    assert all(a != b for a, b in pairs)
    assert ("a.py", "a.py") not in pairs


def test_a_commit_outside_the_window_contributes_nothing(window_repo):
    narrow = compute_git_signals(window_repo, SignalConfig(window_days=180))
    wide = compute_git_signals(window_repo, SignalConfig(window_days=1000))

    assert set(narrow.files) == {"src/recent.py"}
    assert narrow.commits_read == 1
    assert set(wide.files) == {"src/recent.py", "src/ancient.py"}
    assert wide.commits_read == 2


# ==========================================================================
# R7 / AC5, AC7 — schema and migration
# ==========================================================================

EXPECTED_INDEXES = {
    "file_git_signals": {
        "uq_file_git_signals_path",
        "ix_file_git_signals_churn",
        "ix_file_git_signals_bugfix",
    },
    "file_co_change_pairs": {"uq_file_co_change", "ix_file_co_change_support"},
}


def test_the_migration_builds_both_tables_and_all_five_named_indexes(migrated_engine):
    with migrated_engine.begin() as conn:
        code_signals_migration.m_code_signals(conn)

    for table, names in EXPECTED_INDEXES.items():
        assert names <= _indexes(migrated_engine, table), table


def test_running_the_migration_twice_is_a_no_op(migrated_engine):
    with migrated_engine.begin() as conn:
        code_signals_migration.m_code_signals(conn)
    before = {t: _indexes(migrated_engine, t) for t in EXPECTED_INDEXES}

    with migrated_engine.begin() as conn:
        code_signals_migration.m_code_signals(conn)

    assert {t: _indexes(migrated_engine, t) for t in EXPECTED_INDEXES} == before


def test_a_partial_build_is_completed_rather_than_failed(migrated_engine):
    with migrated_engine.begin() as conn:
        code_signals_migration.m_code_signals(conn)
    with migrated_engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_file_git_signals_bugfix"))

    with migrated_engine.begin() as conn:
        code_signals_migration.m_code_signals(conn)

    assert "ix_file_git_signals_bugfix" in _indexes(migrated_engine, "file_git_signals")


def test_the_migration_is_registered_in_the_chain():
    ids = [mid for mid, _ in MIGRATIONS]
    registered = [mid for mid in ids if "code_signal" in mid]
    assert len(registered) == 1, "exactly one code-signals migration id"


def test_every_signal_is_its_own_typed_scalar_column(migrated_engine):
    """AC5: no JSON column, no serialized list, no delimited signal set."""
    with migrated_engine.begin() as conn:
        code_signals_migration.m_code_signals(conn)

    for table in EXPECTED_INDEXES:
        columns = _columns(migrated_engine, table)
        assert columns, table
        assert not any(name.endswith("_json") for name in columns), table
        assert not any("JSON" in (kind or "").upper() for kind in columns.values()), table

    signals = _columns(migrated_engine, "file_git_signals")
    assert {
        "workspace_id",
        "file_path",
        "churn_commits",
        "churn_added",
        "churn_deleted",
        "last_commit_at",
        "top_author",
        "top_author_added_share",
        "distinct_authors",
        "bugfix_commits",
        "last_bugfix_at",
        "window_days",
        "head_sha",
        "computed_at",
    } <= set(signals)
    assert {"workspace_id", "file_path", "partner_path", "support", "computed_at"} <= set(
        _columns(migrated_engine, "file_co_change_pairs")
    )


def test_the_share_column_is_named_for_authored_lines_not_ownership():
    """Plan D1: the column must not read as surviving-line share to 301."""
    names = set(FileGitSignal.model_fields)
    assert "top_author_added_share" in names
    assert "ownership_pct" not in names


# ==========================================================================
# R8 / AC6, AC10 — the store seam
# ==========================================================================


def _signal_rows(session: Session, workspace_id: str) -> list[FileGitSignal]:
    return list(
        session.exec(select(FileGitSignal).where(FileGitSignal.workspace_id == workspace_id)).all()
    )


def _pair_rows(session: Session, workspace_id: str) -> list[FileCoChangePair]:
    return list(
        session.exec(
            select(FileCoChangePair).where(FileCoChangePair.workspace_id == workspace_id)
        ).all()
    )


def test_a_refresh_writes_one_row_per_path_and_two_per_pair(
    session, workspace, authored_repo, config
):
    report = refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert report.commits_read == 8
    assert report.files_seen == 4
    assert report.signal_rows == 4
    assert report.pair_rows == 2
    assert report.empty_basis is None
    assert report.head_sha == _git(authored_repo, "rev-parse", "HEAD").stdout.strip()
    assert report.elapsed_seconds >= 0
    assert report.elapsed_seconds < 22

    assert len(_signal_rows(session, workspace.id)) == 4
    assert {(p.file_path, p.partner_path) for p in _pair_rows(session, workspace.id)} == {
        ("src/core.py", "src/helper.py"),
        ("src/helper.py", "src/core.py"),
    }


def test_the_report_is_a_return_value_carrying_every_figure(
    session, workspace, authored_repo, config
):
    """AC10: a count nobody reads cannot tell a working computation from a
    silent no-op, so the measurement is returned rather than logged."""
    report = refresh_workspace_signals(session, workspace.id, authored_repo, config)

    for field in (
        "commits_read",
        "files_seen",
        "signal_rows",
        "pair_rows",
        "elapsed_seconds",
        "head_sha",
        "empty_basis",
    ):
        assert hasattr(report, field), field


def test_two_consecutive_refreshes_leave_identical_rows(session, workspace, authored_repo, config):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    first = sorted(
        (r.file_path, r.churn_commits, r.top_author, r.bugfix_commits)
        for r in _signal_rows(session, workspace.id)
    )

    second_report = refresh_workspace_signals(session, workspace.id, authored_repo, config)
    second = sorted(
        (r.file_path, r.churn_commits, r.top_author, r.bugfix_commits)
        for r in _signal_rows(session, workspace.id)
    )

    assert first == second
    assert second_report.signal_rows == 4
    assert len(_pair_rows(session, workspace.id)) == 2


def test_a_refresh_over_a_commitless_repo_writes_zero_rows_and_does_not_raise(
    session, workspace, commitless_repo, config
):
    report = refresh_workspace_signals(session, workspace.id, commitless_repo, config)

    assert report.empty_basis == EmptyBasis.NO_HISTORY
    assert report.signal_rows == 0
    assert _signal_rows(session, workspace.id) == []


def test_an_empty_window_clears_previous_rows_because_it_is_a_true_answer(
    session, workspace, authored_repo, outside_window_repo, config
):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    assert _signal_rows(session, workspace.id)

    report = refresh_workspace_signals(session, workspace.id, outside_window_repo, config)

    assert report.empty_basis == EmptyBasis.NO_COMMITS_IN_WINDOW
    assert _signal_rows(session, workspace.id) == []


def test_a_failed_read_raises_and_leaves_the_existing_rows_intact(
    session, workspace, authored_repo, not_a_repo, config
):
    """The one way this writer can destroy data: deleting before reading."""
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    before = len(_signal_rows(session, workspace.id))
    assert before == 4

    with pytest.raises(GitSignalsReadError):
        refresh_workspace_signals(session, workspace.id, not_a_repo, config)

    session.rollback()
    assert len(_signal_rows(session, workspace.id)) == before
    assert len(_pair_rows(session, workspace.id)) == 2


def test_a_refresh_does_not_touch_another_workspaces_rows(
    session, workspace, other_workspace, authored_repo, config
):
    session.add(
        FileGitSignal(
            workspace_id=other_workspace.id,
            file_path="src/core.py",
            churn_commits=99,
            churn_added=0,
            churn_deleted=0,
            last_commit_at=datetime.now(UTC).replace(tzinfo=None),
            top_author=ZOE,
            top_author_added_share=1.0,
            distinct_authors=1,
            bugfix_commits=0,
            window_days=180,
            head_sha="deadbeef",
        )
    )
    session.commit()

    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    survivors = _signal_rows(session, other_workspace.id)
    assert [r.churn_commits for r in survivors] == [99]


def test_signals_for_path_reads_one_row_and_returns_none_for_an_unknown_path(
    session, workspace, authored_repo, config
):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    found = signals_for_path(session, workspace.id, "src/core.py")
    assert found is not None
    assert found.churn_commits == 5
    assert found.top_author == ALICE
    assert found.top_author_added_share == pytest.approx(0.6)
    assert found.last_commit_at.tzinfo is not None

    assert signals_for_path(session, workspace.id, "src/nowhere.py") is None


def test_the_stored_row_round_trips_the_computed_value(session, workspace, authored_repo, config):
    computed = compute_git_signals(authored_repo, config).files["src/helper.py"]
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert signals_for_path(session, workspace.id, "src/helper.py") == computed


def test_top_files_by_churn_returns_the_highest_first(session, workspace, authored_repo, config):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    top = top_files_by(session, workspace.id, SignalKind.CHURN_COMMITS, 3)

    assert [row.file_path for row in top] == ["src/core.py", "src/helper.py", "README.md"]
    assert [row.churn_commits for row in top] == [5, 3, 2]


def test_top_files_by_bugfix_returns_the_most_fixed_first(
    session, workspace, authored_repo, config
):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    top = top_files_by(session, workspace.id, SignalKind.BUGFIX_COMMITS, 2)

    assert [row.file_path for row in top] == ["src/core.py", "src/helper.py"]
    assert [row.bugfix_commits for row in top] == [3, 2]


def test_top_files_by_is_scoped_to_the_workspace(
    session, workspace, other_workspace, authored_repo, config
):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert top_files_by(session, other_workspace.id, SignalKind.CHURN_COMMITS, 5) == []


def test_the_signal_vocabulary_has_one_member_per_top_n_index():
    """An index with no caller is a guess; an enum member with no index is a
    silent table scan."""
    assert {member.value for member in SignalKind} == {
        SignalKind.CHURN_COMMITS.value,
        SignalKind.BUGFIX_COMMITS.value,
    }


# ==========================================================================
# R9 / AC2, AC12 — boundaries
# ==========================================================================

FORBIDDEN_IMPORTS = (
    "loregarden.agents",
    "loregarden.mcp",
    "loregarden.api",
    "loregarden.services.builtin_orchestrator",
    "loregarden.services.workflow_state",
    "loregarden.services.workflow_routing",
    "loregarden.services.orchestration",
)


@pytest.mark.parametrize("module", _package_modules(), ids=lambda p: p.name)
def test_no_module_in_the_package_can_reach_the_orchestrator(module):
    """A derived index must not be able to move a ticket. The consumers import
    this package; it imports none of them."""
    imported = _imported_names(module.read_text(encoding="utf-8"))

    for name in imported:
        for forbidden in FORBIDDEN_IMPORTS:
            assert not (name == forbidden or name.startswith(f"{forbidden}.")), (
                f"{module.name} imports {name}"
            )


def test_the_reducers_know_nothing_about_a_database():
    imported = _imported_names(Path(compute_module.__file__).read_text(encoding="utf-8"))

    assert not any(
        name.split(".")[0] in {"sqlmodel", "sqlalchemy"} or name.startswith("loregarden.db")
        for name in imported
    )


def test_the_classifier_knows_nothing_about_git_or_a_database():
    imported = _imported_names(Path(classify_module.__file__).read_text(encoding="utf-8"))

    assert "loregarden.services.git_subprocess" not in imported
    assert "subprocess" not in imported


@pytest.mark.parametrize("forbidden", ["subprocess.run", "subprocess.Popen", "os.system"])
def test_the_package_never_spawns_a_process_itself(forbidden):
    """AC2: GIT_DIR overrides cwd, so an unscrubbed call reads another repo."""
    for module in _package_modules():
        assert forbidden not in module.read_text(encoding="utf-8"), module.name


def test_only_git_history_shells_git_and_it_does_so_at_most_twice():
    """One `git log` read plus the HEAD lookup. Any third call site is a second
    read nobody budgeted for."""
    call_sites = {
        module.name: module.read_text(encoding="utf-8").count("run_git(")
        for module in _package_modules()
    }

    assert {name for name, count in call_sites.items() if count} == {"git_history.py"}
    assert call_sites["git_history.py"] <= 2


# ==========================================================================
# R10 / AC11 — no operator-visible surface
# ==========================================================================

SURFACE_ROOTS = (
    Path(settings.repo_root) / "server" / "loregarden" / "api",
    Path(settings.repo_root) / "server" / "loregarden" / "mcp",
    Path(settings.repo_root) / "server" / "loregarden" / "cli",
    Path(settings.repo_root) / "client" / "src",
)


@pytest.mark.parametrize("root", SURFACE_ROOTS, ids=lambda p: p.name)
def test_nothing_operator_facing_reaches_the_signals(root):
    """AC11: the signals are reachable only as Python functions and rows. A
    route, a tool, a subcommand or a page here has not met the criterion."""
    if not root.exists():
        pytest.skip(f"{root} is not present in this checkout")

    offenders = [
        path
        for pattern in ("*.py", "*.ts", "*.tsx")
        for path in root.rglob(pattern)
        if "code_signals" in path.read_text(encoding="utf-8", errors="ignore")
    ]

    assert offenders == []


# ==========================================================================
# R12 / AC8, AC10 — the computation writes nothing, and the measurement
# ==========================================================================


def _porcelain(root: Path) -> set[str]:
    result = run_git(
        ["status", "--porcelain"], cwd=root, check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.splitlines())


def test_computing_the_signals_writes_nothing_into_the_repository(authored_repo, config):
    before = _porcelain(authored_repo)
    assert before == set()

    compute_git_signals(authored_repo, config)

    assert _porcelain(authored_repo) == set()


def test_refreshing_writes_nothing_into_the_repository_under_measurement(
    session, workspace, authored_repo, config
):
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert _porcelain(authored_repo) == set()


def test_a_refresh_over_this_checkout_fits_the_measured_shape(session, other_workspace, config):
    """AC10's measurement harness, against this checkout and a throwaway
    database. The porcelain assertion is a DELTA: during implement the worktree
    legitimately holds the implementer's own new files.

    Recorded scale as of 2026-11-16: 1027 commits and 3445 tracked files, which
    supersedes the description's original 728 files / 229 commits (stale by
    roughly 4.5x). Expected shape: 1-3s and 4-20k rows across both tables.
    """
    repo_root = Path(settings.repo_root)
    if not (repo_root / ".git").exists():
        pytest.skip("this checkout is not a git repository")

    before = _porcelain(repo_root)
    report = refresh_workspace_signals(session, other_workspace.id, repo_root, config)
    after = _porcelain(repo_root)

    assert after == before
    assert report.elapsed_seconds < 22
    assert report.commits_read > 0
    assert report.signal_rows > 0
    assert report.head_sha


# ==========================================================================
# Adversarial coverage (test-break stage).
#
# Everything above pins the behaviour the spec describes. Everything below
# attacks it: input shapes git really produces that the spec never named, the
# naive implementations that pass every test above, and the reader paths whose
# determinism nothing yet holds.
#
# Each fixture repository here was built against real git before any number
# below was written down, and every expected value was produced by a throwaway
# reference reducer over git's own output, never by hand.
# ==========================================================================


#: The sentinel and field separator with their NUL framing removed. A commit
#: subject can contain any byte except NUL, so these are exactly the parts of
#: the record framing an adversarial commit message can forge. A parser that
#: scans for the visible text instead of the NUL-framed sentinel splits a
#: record in half here; one that relies on the NULs is untouched.
_VISIBLE_SENTINEL = git_history.RECORD_SENTINEL.replace(b"\x00", b"").decode("utf-8", "replace")
_VISIBLE_FIELD_SEP = git_history.FIELD_SEP.replace(b"\x00", b"").decode("utf-8", "replace")

#: A tab (a header field separator if anyone chose one), the forgeable part of
#: the sentinel, and the literal bytes of a binary numstat row.
HOSTILE_SUBJECT = f"fix: a\ttab, {_VISIBLE_SENTINEL}, {_VISIBLE_FIELD_SEP}, and a -\t- row"

TAB_PATH = "tab\there.py"
NEWLINE_PATH = "new\nline.py"


# --------------------------------------------------------------------------
# Adversarial fixtures.
# --------------------------------------------------------------------------


@pytest.fixture(name="merge_repo")
def merge_repo_fixture(tmp_path: Path) -> Path:
    """Three ordinary commits and one conflict-resolving merge commit.

    The merge adds a line of its own (`RESOLVED`) that exists in no parent, and
    carries a `fix:` subject. `--no-merges` must drop it whole: measured, with
    the flag `base.py` is (3 commits, 5 added); without it the merge appears as
    a fourth record.
    """
    root = _init(tmp_path / "merge")

    _write(root, "base.py", "1\n2\n3\n")
    _commit(root, "feat: base", author=ALICE, days_ago=60)

    _git(root, "checkout", "-q", "-b", "side")
    _write(root, "base.py", "1\nSIDE\n2\n3\n")
    _write(root, "side.py", "s\n")
    _commit(root, "feat: side", author=BOB, days_ago=50)

    _git(root, "checkout", "-q", "main")
    _write(root, "base.py", "1\nMAIN\n2\n3\n")
    _commit(root, "feat: main", author=ALICE, days_ago=40)

    _git(root, "merge", "--no-ff", "side", check=False)  # conflicts on purpose
    assert (root / "base.py").read_text(encoding="utf-8").count("<<<<<<<") == 1
    _write(root, "base.py", "1\nMAIN\nSIDE\n2\n3\nRESOLVED\n")
    _git(root, "add", "-A")
    _git(
        root,
        "commit",
        "-q",
        "--no-edit",
        "-m",
        "fix: resolve the merge",
        when=_when(30),
        author=ZOE,
    )
    return root


@pytest.fixture(name="weird_path_repo")
def weird_path_repo_fixture(tmp_path: Path) -> Path:
    """Paths containing a tab and a newline, under `core.quotePath=true`.

    A tab is the numstat column separator and a newline is what a line-oriented
    parser splits on, so both are legal in a path and both are fatal to the
    obvious parser. `core.quotePath` is turned ON to prove `-z` is what keeps
    the bytes raw: with the flag and without `-z`, git escapes them instead.
    """
    root = _init(tmp_path / "weird")
    _git(root, "config", "core.quotePath", "true")

    _write(root, TAB_PATH, _lines(2, "t"))
    _write(root, NEWLINE_PATH, _lines(1, "n"))
    _write(root, "plain.py", _lines(1, "p"))
    _commit(root, HOSTILE_SUBJECT, author=ALICE, days_ago=30)
    return root


@pytest.fixture(name="churn_mix_repo")
def churn_mix_repo_fixture(tmp_path: Path) -> Path:
    """One path with added AND deleted lines, and an author who only deletes.

    Measured: (3 commits, 7 added, 6 deleted); alice added 4 and bob 3, so
    alice owns 4/7; zoe only removed lines, so she is a distinct author with no
    ownership at all. Every fixture above has `churn_deleted == 0` on a path
    that anyone authored, so a reducer that swapped the two columns, or that
    counted deletions toward ownership, passes all of them.
    """
    root = _init(tmp_path / "churnmix")

    _write(root, "src/m.py", "a\nb\nc\nd\n")
    _commit(root, "feat: m", author=ALICE, days_ago=60)

    _write(root, "src/m.py", "a\nX\nY\nd\ne\n")
    _commit(root, "fix: m", author=BOB, days_ago=40)

    _write(root, "src/m.py", "a\n")
    _commit(root, "chore: shrink", author=ZOE, days_ago=20)
    return root


@pytest.fixture(name="nodiff_repo")
def nodiff_repo_fixture(tmp_path: Path) -> Path:
    """A plain-text source file that git reports as binary.

    A committed `.gitattributes` marking a path `-diff` makes numstat emit
    `-`/`-` for it (measured), so the binary row shape is reachable for an
    ordinary `.py` file. The result is a SOURCE path with a real bug-fix
    history, two authors, and no owner at all — a combination no other fixture
    produces, and the one that breaks a reducer which infers "no adds means
    nothing happened here".
    """
    root = _init(tmp_path / "nodiff")

    _write(root, ".gitattributes", "src/opaque.py -diff\n")
    _write(root, "src/opaque.py", _lines(3, "o"))
    _write(root, "src/plain.py", _lines(1, "p"))
    _commit(root, "feat: seed", author=ALICE, days_ago=60)

    _append(root, "src/opaque.py", 1, "o")
    _commit(root, "fix: opaque bug", author=BOB, days_ago=30)
    return root


# ==========================================================================
# Parsing: framing an adversarial commit can forge, and row shapes git emits
# ==========================================================================


def test_an_empty_stream_is_no_records_rather_than_an_error():
    """A repository with no commits in the window yields an empty stream. That
    is a legitimate answer, not a malformed one, and R5 needs it to reach the
    `NO_COMMITS_IN_WINDOW` basis rather than a parse error."""
    assert parse_log_stream(b"") == []


def test_a_subject_cannot_forge_the_record_framing(captured_stream, weird_path_repo):
    """The only framing bytes a commit message can contain. A subject is read
    from `%s`, is attacker-controlled on any repository with contributors, and
    can hold a tab, the visible text of the sentinel, and the literal `-\\t-` of
    a binary row. Only the NUL framing separates it from a record boundary."""
    forged = captured_stream.replace(b"chore: nothing changed", HOSTILE_SUBJECT.encode("utf-8"))

    records = parse_log_stream(forged)

    assert len(records) == 3
    assert records[0].subject == HOSTILE_SUBJECT
    assert records[0].files == []
    assert records[1].files == [("asset.bin", 0, 0)]
    assert is_fix_subject(records[0].subject) is True


def test_a_path_containing_a_tab_is_not_split_on_the_numstat_separator(captured_stream):
    """Tab separates the numstat columns and is legal in a path, so the split
    is bounded at two. A `split(b"\\t")` with no limit keys this row on `here.py`."""
    spliced = captured_stream.replace(b"1\t0\tplain.py\x00", f"1\t0\t{TAB_PATH}\x00".encode())

    assert parse_log_stream(spliced)[2].files[1] == (TAB_PATH, 1, 0)


def test_a_path_containing_a_newline_is_not_split_into_two_rows(captured_stream):
    """Under `-z` rows are NUL-terminated, not newline-separated: the single
    newline in a record is the one git puts between the header and the numstat
    block. A line-oriented parser reads this one path as two rows, the second
    of which has no counts."""
    spliced = captured_stream.replace(b"1\t0\tplain.py\x00", f"1\t0\t{NEWLINE_PATH}\x00".encode())

    records = parse_log_stream(spliced)

    assert records[2].files == [(HOSTILE_PATH, 2, 0), (NEWLINE_PATH, 1, 0)]


@pytest.mark.parametrize(
    ("row", "why"),
    [
        (b"1\tplain.py\x00", "one column missing"),
        (b"1\t0\x00", "no path at all"),
        (b"x\t0\tplain.py\x00", "a non-numeric added count"),
        (b"1\tz\tplain.py\x00", "a non-numeric deleted count"),
        (b"-\t0\tplain.py\x00", "half a binary row"),
    ],
)
def test_every_malformed_row_shape_raises_rather_than_being_guessed_at(captured_stream, row, why):
    """R4's rule is one rule: an unrecognised row raises and names itself. A
    parser that only guards the shape the spec illustrated silently mis-keys
    the rest, and a mis-keyed path is a wrong answer nobody can see."""
    spliced = captured_stream.replace(b"1\t0\tplain.py\x00", row)

    with pytest.raises(GitSignalsParseError):
        parse_log_stream(spliced)


def test_a_truncated_header_raises_rather_than_yielding_a_short_record():
    """A stream cut mid-header (a killed git, a full pipe) must not produce a
    record with an empty author or a zero timestamp."""
    truncated = git_history.RECORD_SENTINEL + b"4505849e" + git_history.FIELD_SEP + b"a@e.com"

    with pytest.raises(GitSignalsParseError):
        parse_log_stream(truncated)


# ==========================================================================
# `--no-merges`: the flag is in the args, but nothing tested what it does
# ==========================================================================


def test_a_merge_commit_is_excluded_whole_rather_than_counted(merge_repo, config):
    """The merge resolves a conflict, so it introduces a line (`RESOLVED`) that
    exists in neither parent and carries a `fix:` subject. Both are invisible
    in the result: three records, and `base.py` at 5 added lines."""
    signals = compute_git_signals(merge_repo, config)

    assert signals.commits_read == 3
    assert len(signals.records) == 3
    assert not any(r.subject.startswith("Merge") for r in signals.records)
    assert not any(r.subject == "fix: resolve the merge" for r in signals.records)

    base = signals.files["base.py"]
    assert (base.churn_commits, base.churn_added, base.churn_deleted) == (3, 5, 0)
    assert base.top_author == ALICE
    assert base.top_author_added_share == pytest.approx(0.8)
    assert base.distinct_authors == 2
    assert base.bugfix_commits == 0
    assert base.last_bugfix_at is None


def test_a_merge_only_author_never_appears_as_an_owner(merge_repo, config):
    """Zoe authored only the merge. A merge-blind reducer credits her the
    resolution's lines and makes her a distinct author of `base.py`."""
    signals = compute_git_signals(merge_repo, config)

    assert all(record.author_email != ZOE for record in signals.records)
    assert signals.files["side.py"].top_author == BOB


# ==========================================================================
# Hostile paths and a forged subject, against real git
# ==========================================================================


def test_paths_with_a_tab_and_a_newline_survive_a_real_read(weird_path_repo, config):
    """The synthetic splices above with git actually producing the bytes, and
    with `core.quotePath=true` set on the repository so the test fails if `-z`
    is ever dropped in favour of git's own escaping."""
    signals = compute_git_signals(weird_path_repo, config)

    assert set(signals.files) == {TAB_PATH, NEWLINE_PATH, "plain.py"}
    assert signals.files[TAB_PATH].churn_added == 2
    assert signals.files[NEWLINE_PATH].churn_added == 1
    assert all(row.churn_commits == 1 for row in signals.files.values())


def test_a_forged_subject_read_back_from_real_git_is_still_one_record(weird_path_repo, config):
    """Read the subject git itself stored rather than the string handed to
    `commit`, so git's own message cleanup cannot make this a false failure."""
    stored = _git(weird_path_repo, "log", "-1", "--format=%s").stdout.rstrip("\n")
    signals = compute_git_signals(weird_path_repo, config)

    assert signals.commits_read == 1
    assert [r.subject for r in signals.records] == [stored]
    assert "\t" in stored


def test_the_hostile_subject_still_attributes_its_bug_fix(weird_path_repo, config):
    """A fix subject carrying framing bytes is a fix subject. If the parser
    truncated it at the forged sentinel, the predicate would still match the
    `fix:` prefix — so this asserts the paths too, which truncation loses."""
    signals = compute_git_signals(weird_path_repo, config)

    assert [signals.files[p].bugfix_commits for p in (TAB_PATH, NEWLINE_PATH, "plain.py")] == [
        1,
        1,
        1,
    ]


# ==========================================================================
# Churn columns, and ownership against an author who only removes lines
# ==========================================================================


def test_added_and_deleted_are_distinct_sums_on_one_path(churn_mix_repo, config):
    """Measured against git: 4+3+0 added, 0+2+4 deleted. Every other fixture
    has one of the two at zero, so a reducer that swapped the columns, or added
    them together, is green everywhere else."""
    row = compute_git_signals(churn_mix_repo, config).files["src/m.py"]

    assert (row.churn_commits, row.churn_added, row.churn_deleted) == (3, 7, 6)


def test_an_author_who_only_deletes_counts_as_an_author_but_owns_nothing(churn_mix_repo, config):
    """`distinct_authors` is who touched the path; ownership is authored lines.
    Conflating them hands the file to whoever deleted the most."""
    row = compute_git_signals(churn_mix_repo, config).files["src/m.py"]

    assert row.distinct_authors == 3
    assert row.top_author == ALICE
    assert row.top_author_added_share == pytest.approx(4 / 7)


def test_a_source_file_git_calls_binary_keeps_its_bug_fix_history(nodiff_repo, config):
    """`.gitattributes` `-diff` makes numstat report `-`/`-` for ordinary text,
    so a SOURCE path can have zero authored lines and a real fix history at the
    same time. Attribution must not be inferred from the line counts."""
    row = compute_git_signals(nodiff_repo, config).files["src/opaque.py"]

    assert (row.churn_commits, row.churn_added, row.churn_deleted) == (2, 0, 0)
    assert row.top_author == ""
    assert row.top_author_added_share == 0.0
    assert row.distinct_authors == 2
    assert row.bugfix_commits == 1
    assert row.last_bugfix_at is not None


def test_the_binary_shaped_path_does_not_swallow_its_neighbours(nodiff_repo, config):
    """The sibling committed alongside it parses normally."""
    files = compute_git_signals(nodiff_repo, config).files

    assert files["src/plain.py"].churn_added == 1
    assert files["src/plain.py"].top_author == ALICE
    assert ".gitattributes" in files


@pytest.mark.parametrize(
    "repo_fixture",
    ["authored_repo", "kindwise_fix_repo", "churn_mix_repo", "nodiff_repo", "deletion_repo"],
)
def test_the_authored_share_is_a_share_on_every_fixture(request, repo_fixture, config):
    """An invariant rather than a case: the column is a fraction, it is zero
    exactly when nobody authored a line, and it is never attributed to an empty
    author while lines exist."""
    root = request.getfixturevalue(repo_fixture)

    for path, row in compute_git_signals(root, config).files.items():
        assert 0.0 <= row.top_author_added_share <= 1.0, path
        assert (row.top_author == "") == (row.churn_added == 0), path
        assert (row.top_author_added_share == 0.0) == (row.churn_added == 0), path
        assert row.distinct_authors >= 1, path
        assert row.bugfix_commits <= row.churn_commits, path


def test_an_empty_commit_is_read_without_touching_any_path(hostile_path_repo, config):
    """The repository's third commit is `--allow-empty`. It counts as a commit
    read and contributes to no path's churn: 3 commits, 3 single-path touches."""
    signals = compute_git_signals(hostile_path_repo, config)

    assert signals.commits_read == 3
    assert sum(row.churn_commits for row in signals.files.values()) == 3
    assert any(record.files == [] for record in signals.records)


# ==========================================================================
# Co-change: what the knobs drop, and what they must not
# ==========================================================================


def test_the_file_cap_drops_pairs_and_never_drops_files(cochange_repo):
    """AC4 asks that the cap change the output; it must change only the pair
    output. A sweeping commit is still history, and the files it touched still
    have churn, ownership and a bug-fix count."""
    cfg = SignalConfig(min_support=1, max_files_per_commit=5)
    signals = compute_git_signals(cochange_repo, cfg)

    sweep = [f"s{n}.py" for n in range(1, 7)]
    assert set(sweep) <= set(signals.files)
    assert all(signals.files[path].churn_commits == 1 for path in sweep)
    assert not any(key[0] in sweep for key in _pairs(signals))


def test_a_cap_below_two_leaves_no_pair_anywhere(cochange_repo):
    """The rule is `2 <= len(paths) <= cap`, so a cap of one is empty, not an
    off-by-one that lets two-file commits through."""
    assert (
        _pairs(
            compute_git_signals(cochange_repo, SignalConfig(min_support=1, max_files_per_commit=1))
        )
        == {}
    )


def test_a_min_support_above_every_pair_leaves_the_files_untouched(cochange_repo):
    """Dropping pairs is not dropping history."""
    signals = compute_git_signals(cochange_repo, SignalConfig(min_support=99))

    assert _pairs(signals) == {}
    assert signals.files["a.py"].churn_commits == 3


def test_every_pair_is_stored_in_both_directions_with_equal_support(cochange_repo):
    """Plan D4's invariant, as an invariant rather than one hand-checked pair:
    a consumer asking for one file's partners must never need an OR."""
    pairs = _pairs(compute_git_signals(cochange_repo, SignalConfig(min_support=1)))

    assert pairs
    for (left, right), support in pairs.items():
        assert left != right
        assert pairs.get((right, left)) == support, (left, right)


# ==========================================================================
# The fix predicate, against the subjects a naive pattern lets through
# ==========================================================================


@pytest.mark.parametrize(
    "subject",
    [
        "fixes: thing",
        "fixup! fix: thing",
        'Revert "fix: thing"',
        "patchwork: thing",
        "hotfixes: thing",
        "fix the thing: really",
        "fix thing",
        "fix",
        "   ",
        "feat(fix)!: thing",
        "chore: patch: nested",
    ],
)
def test_a_subject_that_only_looks_like_a_fix_type_does_not_match(subject):
    """Each of these matches at least one plausible shortcut. `^fix` takes
    `fixes:` and `fixup!`; a substring search takes the revert and the nested
    `patch:`; a prefix-without-colon takes `patchwork:` and `fix thing`. A
    reverted fix in particular is the opposite of a fix, and counting it marks
    the file that was repaired as the file that keeps breaking."""
    assert is_fix_subject(subject) is False


@pytest.mark.parametrize(
    "subject",
    [
        "FIX(CORE)!: shouting",
        "bugfix(ui): thing",
        "hotfix!: thing",
        "patch(deps)!: thing",
        "fix(a/b-c_d): a scope with punctuation",
        "fix: " + "x" * 4000,
    ],
)
def test_the_conventional_shapes_still_match_at_the_edges(subject):
    assert is_fix_subject(subject) is True


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/contest/app.py", PathKind.SOURCE),
        ("server/loregarden/services/latest_results.py", PathKind.SOURCE),
        ("src/docstring_utils.py", PathKind.SOURCE),
        ("client/src/components/Protest.tsx", PathKind.SOURCE),
    ],
)
def test_a_substring_that_looks_like_a_rule_does_not_classify_the_path(path, kind):
    """`"test" in path` takes `contest` and `latest`; `"doc" in path` takes
    `docstring`. Misclassifying a source file as TEST silently zeroes its
    bug-fix count, which is the signal consumers are here for."""
    assert classify_path(path) == kind


# ==========================================================================
# The read is bound to the path it was given, not to the environment
# ==========================================================================


def test_an_ambient_git_dir_cannot_redirect_the_read(
    monkeypatch, authored_repo, window_repo, config
):
    """AC2's actual stake, as behaviour rather than a grep over the source.
    GIT_DIR overrides `cwd`, so an unscrubbed call made from a hook — which is
    where this control plane's git runs — reads the hook's repository instead.
    Verified against real git: with GIT_DIR set, `git log` run inside one
    repository returns the other's history.
    """
    monkeypatch.setenv("GIT_DIR", str(window_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(window_repo))

    signals = compute_git_signals(authored_repo, config)

    assert signals.commits_read == 8
    assert set(signals.files) == set(EXPECTED_SIGNALS)
    assert "src/recent.py" not in signals.files


def test_an_ambient_git_dir_cannot_redirect_the_empty_classification(
    monkeypatch, commitless_repo, authored_repo, config
):
    """The same leak on the path that decides `NO_HISTORY`: a redirected read
    would report eight commits for a repository that has none."""
    monkeypatch.setenv("GIT_DIR", str(authored_repo / ".git"))

    signals = compute_git_signals(commitless_repo, config)

    assert signals.empty_basis == EmptyBasis.NO_HISTORY
    assert signals.commits_read == 0


@pytest.mark.parametrize(
    "forbidden",
    [
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.popen",
        "os.execv",
        "os.spawnv",
        "importlib.import_module",
    ],
)
def test_the_package_cannot_reach_a_process_or_an_import_by_another_spelling(forbidden):
    """AC2 names three spellings and AC12 walks static imports. Both are
    satisfiable while still spawning a process or importing the orchestrator by
    name at runtime, which is the form a static walk cannot see."""
    for module in _package_modules():
        assert forbidden not in module.read_text(encoding="utf-8"), module.name


# ==========================================================================
# Schema: the model must declare what the migration builds
# ==========================================================================


def _declared_constraint_names(model) -> set[str]:
    declared = getattr(model, "__table_args__", ())
    return {name for name in (getattr(arg, "name", None) for arg in declared) if name}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (FileGitSignal, EXPECTED_INDEXES["file_git_signals"]),
        (FileCoChangePair, EXPECTED_INDEXES["file_co_change_pairs"]),
    ],
    ids=["file_git_signals", "file_co_change_pairs"],
)
def test_the_model_declares_every_index_the_migration_builds(model, expected):
    """AC6 is asserted above only after the migration runs. Every database this
    repo creates from metadata — every test database, and a fresh install —
    would then have the tables and none of the indexes, and `signals_for_path`
    would be a table scan with no unique constraint behind it."""
    assert expected <= _declared_constraint_names(model)


def test_two_rows_for_one_path_in_one_workspace_are_refused_by_the_database(session, workspace):
    """The uniqueness `refresh_workspace_signals` relies on, enforced where a
    second writer cannot talk its way past it."""
    for _ in range(2):
        session.add(
            FileGitSignal(
                workspace_id=workspace.id,
                file_path="src/core.py",
                churn_commits=1,
                churn_added=1,
                churn_deleted=0,
                last_commit_at=datetime.now(UTC).replace(tzinfo=None),
                top_author=ALICE,
                top_author_added_share=1.0,
                distinct_authors=1,
                bugfix_commits=0,
                window_days=180,
                head_sha="abc",
            )
        )

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ==========================================================================
# The store: staleness stamps, timezone discipline, and reader determinism
# ==========================================================================


def test_every_row_carries_the_run_that_produced_it(session, workspace, authored_repo):
    """`window_days`, `head_sha` and `computed_at` are the staleness stamp the
    sibling tickets read. A row stamped with the default window after a
    non-default run is a lie about what it measured."""
    cfg = SignalConfig(window_days=365)
    report = refresh_workspace_signals(session, workspace.id, authored_repo, cfg)

    rows = _signal_rows(session, workspace.id)
    assert rows
    assert {row.window_days for row in rows} == {365}
    assert {row.head_sha for row in rows} == {report.head_sha}
    assert all(row.computed_at is not None for row in rows)


def test_the_stored_timestamps_are_naive_utc_while_the_value_object_is_aware(
    session, workspace, authored_repo, config
):
    """R7 stores naive UTC through the ORM; compute returns aware UTC. Mixing
    them raises on comparison, so which side each is on has to be pinned."""
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    row = next(r for r in _signal_rows(session, workspace.id) if r.file_path == "src/core.py")

    assert row.last_commit_at.tzinfo is None
    assert row.computed_at.tzinfo is None
    assert row.last_bugfix_at is not None
    assert row.last_bugfix_at.tzinfo is None

    computed = compute_git_signals(authored_repo, config).files["src/core.py"]
    assert computed.last_commit_at.tzinfo is not None
    assert row.last_commit_at == computed.last_commit_at.replace(tzinfo=None)


def test_a_narrower_window_removes_the_rows_it_no_longer_covers(session, workspace, window_repo):
    """Delete-and-reinsert, on a refresh whose answer shrinks but is not empty.
    A writer that upserts instead leaves `src/ancient.py` behind forever."""
    refresh_workspace_signals(session, workspace.id, window_repo, SignalConfig(window_days=1000))
    assert {r.file_path for r in _signal_rows(session, workspace.id)} == {
        "src/ancient.py",
        "src/recent.py",
    }

    refresh_workspace_signals(session, workspace.id, window_repo, SignalConfig(window_days=180))

    assert {r.file_path for r in _signal_rows(session, workspace.id)} == {"src/recent.py"}


def test_a_refresh_replaces_the_pair_rows_rather_than_accumulating_them(
    session, workspace, cochange_repo, authored_repo, config
):
    """The pair table is the one that grows quadratically, so a refresh that
    only replaced the signal rows would be invisible until it was enormous."""
    refresh_workspace_signals(session, workspace.id, cochange_repo, config)
    assert {(p.file_path, p.partner_path) for p in _pair_rows(session, workspace.id)} == {
        ("a.py", "b.py"),
        ("b.py", "a.py"),
    }

    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert {(p.file_path, p.partner_path) for p in _pair_rows(session, workspace.id)} == {
        ("src/core.py", "src/helper.py"),
        ("src/helper.py", "src/core.py"),
    }


def test_another_workspaces_pair_rows_survive_a_refresh(
    session, workspace, other_workspace, authored_repo, config
):
    """The signal rows are checked for this above; the pair delete is a second
    statement with its own WHERE, and nothing held it."""
    session.add(
        FileCoChangePair(
            workspace_id=other_workspace.id,
            file_path="x.py",
            partner_path="y.py",
            support=7,
        )
    )
    session.commit()

    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert [p.support for p in _pair_rows(session, other_workspace.id)] == [7]


def test_signals_for_path_does_not_read_across_workspaces(
    session, workspace, other_workspace, authored_repo, config
):
    """The unique index is `(workspace_id, file_path)`; a reader that queries on
    the path alone returns whichever row the planner reaches first."""
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert signals_for_path(session, other_workspace.id, "src/core.py") is None


def test_top_files_by_honours_a_zero_and_an_oversized_limit(
    session, workspace, authored_repo, config
):
    """Zero is a legitimate request for nothing, and must not read as
    'unlimited' the way SQLite's own LIMIT treats a negative."""
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    assert top_files_by(session, workspace.id, SignalKind.CHURN_COMMITS, 0) == []
    assert len(top_files_by(session, workspace.id, SignalKind.CHURN_COMMITS, 1)) == 1
    assert len(top_files_by(session, workspace.id, SignalKind.CHURN_COMMITS, 500)) == 4


def test_top_files_by_is_stable_across_calls_when_two_files_tie(
    session, workspace, authored_repo, config
):
    """`README.md` and `src/tie.py` both have two commits, and the existing
    top-N tests stop at three rows so neither ever meets the other. AC1 asks
    for determinism across runs; a reader whose tie order comes from the
    planner gives two answers to one question."""
    refresh_workspace_signals(session, workspace.id, authored_repo, config)

    first = [
        row.file_path for row in top_files_by(session, workspace.id, SignalKind.CHURN_COMMITS, 4)
    ]
    second = [
        row.file_path for row in top_files_by(session, workspace.id, SignalKind.CHURN_COMMITS, 4)
    ]

    assert first == second
    assert first[:2] == ["src/core.py", "src/helper.py"]
    assert sorted(first[2:]) == ["README.md", "src/tie.py"]


def test_the_computation_leaves_an_untracked_file_untracked(uncommitted_repo, config):
    """AC8 is asserted above against a clean tree, where `git add` or a stash
    would also leave the porcelain empty. Here the porcelain is non-empty
    before and must be the same set after."""
    before = _porcelain(uncommitted_repo)
    assert before

    compute_git_signals(uncommitted_repo, config)

    assert _porcelain(uncommitted_repo) == before


# ==========================================================================
# AC11: no orchestrator or scheduler hook, not only no route or page
# ==========================================================================

ORCHESTRATION_SURFACES = (
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "orchestration.py",
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "builtin_orchestrator.py",
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "workflow_state.py",
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "workflow_routing.py",
    Path(settings.repo_root) / "agent_context" / "orchestration",
    Path(settings.repo_root) / "lefthook.yml",
)


@pytest.mark.parametrize("surface", ORCHESTRATION_SURFACES, ids=lambda p: p.name)
def test_nothing_schedules_or_triggers_the_signals(surface):
    """AC11 names a scheduler and a stage-transition hook alongside the route
    and the page, and the test above covers only the second pair. A recompute
    wired into a stage transition would also make AC8 the orchestrator's
    problem rather than this package's."""
    if not surface.exists():
        pytest.skip(f"{surface} is not present in this checkout")

    sources = sorted(surface.rglob("*")) if surface.is_dir() else [surface]
    offenders = [
        path.name
        for path in sources
        if path.is_file() and "code_signals" in path.read_text(encoding="utf-8", errors="ignore")
    ]

    assert offenders == []
