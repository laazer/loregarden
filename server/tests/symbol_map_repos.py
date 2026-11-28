"""Repository scaffolding and fixtures for the symbol-map suite.

Split out of `test_symbol_map.py` so that module stays under the 2500-line
organization cap; it holds no tests. Every repository a symbol-map test uses
is built here, in a fixture, never in a test body — a broken setup inside a
test body fails in the call phase and is indistinguishable from a healthy
failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import SymbolKind, Workspace
from loregarden.services.git_subprocess import run_git
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine
from tests.repo_templates import from_template

# --------------------------------------------------------------------------
# Repository scaffolding. Used only by fixtures.
# --------------------------------------------------------------------------


def _git(root: Path, *args: str, check: bool = True):
    """One git command in *root*, with git's repo-binding env vars scrubbed."""
    return run_git(list(args), cwd=root, check=check, capture_output=True, text=True)


def _init(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _write(root: Path, rel: str, body: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def _write_bytes(root: Path, rel: str, body: bytes) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


#: One module holding every Python shape R3.4 names, at lines this test pins.
#: Line numbers are asserted by `PYTHON_EXPECTED` below; keep the two in step.
PY_MODULE = '''\
"""A fixture module."""

MODULE_CONSTANT = 1
ANNOTATED: int = 2
FIRST, SECOND = 3, 4


class Widget:
    CLASS_LEVEL = 5

    def render(self):
        def nested_helper():
            return 1

        return nested_helper()

    async def fetch(self):
        return None


def render():
    return "a free function with a method's name"


async def amain():
    return None
'''

#: (name, kind, line, parent) for every row PY_MODULE must produce, and for
#: nothing else. `CLASS_LEVEL`, `nested_helper`, `FIRST` and `SECOND` are
#: deliberately absent (R3.4, assumption A6).
PYTHON_EXPECTED = {
    ("MODULE_CONSTANT", SymbolKind.CONSTANT, 3, ""),
    ("ANNOTATED", SymbolKind.CONSTANT, 4, ""),
    ("Widget", SymbolKind.CLASS, 8, ""),
    ("render", SymbolKind.METHOD, 11, "Widget"),
    ("fetch", SymbolKind.METHOD, 17, "Widget"),
    ("render", SymbolKind.FUNCTION, 21, ""),
    ("amain", SymbolKind.FUNCTION, 25, ""),
}

TS_MODULE = """\
import { thing } from "./thing";

export function makeThing(): number {
  const localOnly = 1;
  return localOnly;
}

export const MAX_RETRIES = 3;

export const useThing = () => thing;

export class Engine {
  start() {
    return 1;
  }
}

export interface Shape {
  kind: string;
}

export type Alias = Shape | null;

export enum Mode {
  Fast,
}

declare function ambient(): void;
"""

#: (name, kind, line) for TS_MODULE. `localOnly` is indented, so it is not a
#: declaration this tier claims; `start` is a class method, which 281 does not
#: extract at all (R4.1 constraint, deferred to 386).
TS_EXPECTED = {
    ("makeThing", SymbolKind.FUNCTION, 3),
    ("MAX_RETRIES", SymbolKind.CONSTANT, 8),
    ("useThing", SymbolKind.VARIABLE, 10),
    ("Engine", SymbolKind.CLASS, 12),
    ("Shape", SymbolKind.INTERFACE, 18),
    ("Alias", SymbolKind.TYPE_ALIAS, 22),
    ("Mode", SymbolKind.ENUM, 24),
    ("ambient", SymbolKind.FUNCTION, 28),
}

BARREL_MODULE = """\
export { Engine } from "./engine";
export * from "./shapes";
export { default as Thing } from "./thing";
"""

OVERLOAD_MODULE = """\
export function parse(input: string): number;
export function parse(input: number): number;
export function parse(input: string | number): number {
  return Number(input);
}
"""

TSX_MODULE = """\
export function Thing({ label }: { label: string }) {
  return <span>{label}</span>;
}
"""


# --------------------------------------------------------------------------
# Fixtures: one repository shape per behaviour under test.
# --------------------------------------------------------------------------


@pytest.fixture(name="mapped_repo")
def mapped_repo_fixture(tmp_path: Path) -> Path:
    """Every supported and unsupported shape in one tracked tree."""
    return from_template(tmp_path / "mapped", "symbol_map_mapped", _build_mapped_mapped_repo)


def _build_mapped_mapped_repo(root: Path) -> None:
    root = _init(root)
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/widget.py", PY_MODULE)
    _write(root, "pkg/other.py", "def render():\n    return 2\n")
    _write(root, "web/module.ts", TS_MODULE)
    _write(root, "web/index.ts", BARREL_MODULE)
    _write(root, "web/overloads.ts", OVERLOAD_MODULE)
    _write(root, "web/Thing.tsx", TSX_MODULE)
    _write(root, "README.md", "# fixture\n")
    _write_bytes(root, "assets/blob.bin", bytes(range(256)))
    _commit(root, "feat: the mapped tree")


@pytest.fixture(name="broken_repo")
def broken_repo_fixture(tmp_path: Path) -> Path:
    """One good module and one `.py` that `ast.parse` cannot read.

    It has to be written here rather than taken from the checkout: verified at
    HEAD a4d45f9f8f, no real file in `server/loregarden` fails to parse under
    the 3.11 this suite runs on.
    """
    return from_template(
        tmp_path / "broken", "symbol_map_repos_broken_repo", _build_broken_broken_repo
    )


def _build_broken_broken_repo(root: Path) -> None:
    root = _init(root)
    _write(root, "good.py", "def good():\n    return 1\n")
    _write(root, "bad.py", "def oops(:\n")
    _write_bytes(root, "undecodable.ts", b"export const A = 1;\n\xff\xfe not utf-8\n")
    _commit(root, "feat: one of each failure")


@pytest.fixture(name="unreadable_repo")
def unreadable_repo_fixture(tmp_path: Path) -> Path:
    """An unsupported path the process is not permitted to read.

    R3.3 says a file of language OTHER is never opened. Permission is the only
    way to assert "never opened" without reaching inside the implementation:
    a build that opens it either raises or records PARSE_FAILED, and a build
    that honours the rule records UNSUPPORTED_LANGUAGE and never notices.
    """
    root = _init(tmp_path / "unreadable")
    _write(root, "keep.py", "def keep():\n    return 1\n")
    _write_bytes(root, "secret.bin", b"\x00\x01\x02")
    _commit(root, "feat: an unreadable blob")
    (root / "secret.bin").chmod(0o000)
    yield root
    (root / "secret.bin").chmod(0o644)


@pytest.fixture(name="suggest_repo")
def suggest_repo_fixture(tmp_path: Path) -> Path:
    """Names spread over lengths 1..30, so R5.5's window has something to drop.

    The query in the test is 20 characters, whose lossless window is
    [ceil(3*20/7), floor(7*20/3)] = [9, 46]; the eight names shorter than nine
    characters are the ones the SQL must never fetch.
    """
    return from_template(
        tmp_path / "suggest", "symbol_map_repos_suggest_repo", _build_suggest_suggest_repo
    )


def _build_suggest_suggest_repo(root: Path) -> None:
    root = _init(root)
    lines = [f"def {'a' * n}():\n    return {n}\n" for n in range(1, 31)]
    lines.append("def calculate_total_price():\n    return 0\n")
    _write(root, "names.py", "\n".join(lines))
    _commit(root, "feat: a spread of name lengths")


@pytest.fixture(name="worktree_pair")
def worktree_pair_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A primary checkout and a linked worktree at a different sha.

    The worktree's copy of `pkg/widget.py` has two extra lines at the top, so
    every symbol in it sits two lines lower than in the primary. A build that
    walked `Workspace.repo_path` instead of the root it was handed would store
    the primary's numbers under the worktree's identity — R2's named risk, and
    the one failure the sha stamp exists to prevent.
    """
    primary = _init(tmp_path / "primary")
    _write(primary, "pkg/widget.py", PY_MODULE)
    _commit(primary, "feat: primary")

    linked = tmp_path / "linked"
    _git(primary, "worktree", "add", "-q", "-b", "side", str(linked))
    _write(linked, "pkg/widget.py", "# shifted\n# shifted\n" + PY_MODULE)
    _commit(linked, "feat: shift every line by two")

    return primary, linked


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session: Session) -> Workspace:
    ws = Workspace(slug="symbols", name="symbols", repo_path="")
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
    """A database with every table except the three this ticket adds."""
    engine = create_engine(f"sqlite:///{tmp_path / 'migrate.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS symbol_map_symbols"))
        conn.execute(text("DROP TABLE IF EXISTS symbol_map_files"))
        conn.execute(text("DROP TABLE IF EXISTS symbol_map_partitions"))
    return engine


@pytest.fixture(name="recorded_sql")
def recorded_sql_fixture(session: Session):
    """Every statement the engine executes, so AC5.5 can measure rather than
    assert in prose that `suggest_names` does not fetch the whole map."""
    statements: list[tuple[str, object]] = []
    engine = session.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    yield statements
    event.remove(engine, "before_cursor_execute", record)


# --------------------------------------------------------------------------
# lg-code-knowledge-284: repositories the incremental refresh needs.
#
# Every one of these is a SHAPE the full build never had to care about,
# because a full build re-reads everything: a tree that moved since the
# stored sha, a stored sha this checkout cannot resolve, a cold checkout
# with an ancestor already mapped, and two checkouts standing on the same
# commit. Built here, in fixtures, for the same reason as everything above.
# --------------------------------------------------------------------------

#: Four modules, one of each thing a refresh has to decide about. The line
#: numbers matter: `beta.py` is edited in place by the tests below so an
#: unchanged neighbour's rows can be asserted byte-identical afterwards.
ALPHA_MODULE = "def alpha():\n    return 1\n"
BETA_MODULE = "def beta():\n    return 2\n"
GAMMA_MODULE = "def gamma():\n    return 3\n"
DELTA_MODULE = "export const DELTA = 4;\n"

#: The edited body `beta.py` takes. `beta` moves from line 1 to line 4 and
#: `beta_helper` appears: a refresh that re-stamps the sha without re-reading
#: the file leaves the old line 1 in place and reads as MATCHES, which is the
#: precise failure this ticket exists to prevent.
BETA_REWRITTEN = '''\
"""Rewritten."""


def beta():
    return 22


def beta_helper():
    return 222
'''


@pytest.fixture(name="refresh_repo")
def refresh_repo_fixture(tmp_path: Path) -> Path:
    """One committed tree with a file to edit, one to delete and one to leave.

    `alpha.py` is the untouched control: its `symbol_map_files.id` is what AC1
    asserts survives a refresh, and an id is only meaningful as a control if
    nothing in the test ever writes that path.
    """
    return from_template(tmp_path / "refresh", "symbol_map_refresh", _build_refresh_refresh_repo)


def _build_refresh_refresh_repo(root: Path) -> None:
    root = _init(root)
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/alpha.py", ALPHA_MODULE)
    _write(root, "pkg/beta.py", BETA_MODULE)
    _write(root, "pkg/gamma.py", GAMMA_MODULE)
    _write(root, "web/delta.ts", DELTA_MODULE)
    _write(root, "README.md", "# refresh\n")
    _commit(root, "feat: the tree a refresh starts from")


@pytest.fixture(name="unborn_repo")
def unborn_repo_fixture(tmp_path: Path) -> Path:
    """A git repository with no commit at all.

    `sources.head_sha` raises `UnbornHeadError` here, and 281 lets that escape
    through every reader. AC11 closes it, so this shape needs a fixture rather
    than an inline `git init` in one test body.
    """
    return from_template(
        tmp_path / "unborn", "symbol_map_repos_unborn_repo", _build_unborn_unborn_repo
    )


def _build_unborn_unborn_repo(root: Path) -> None:
    root = _init(root)
    _write(root, "pkg/thing.py", ALPHA_MODULE)


@pytest.fixture(name="ancestor_checkouts")
def ancestor_checkouts_fixture(tmp_path: Path) -> tuple[Path, Path, frozenset[str]]:
    """A mapped primary at an OLD commit, and a cold worktree three ahead.

    Returns `(primary, cold, paths_changed_since_primary_head)`. The primary
    is left standing on the ancestor commit, so a partition built against it
    carries the ancestor's sha — which is exactly the seed R6 looks for. The
    cold checkout is a linked worktree, so it shares the object database and
    `merge-base --is-ancestor` can answer about a commit it never checked out.
    """
    primary = _init(tmp_path / "ancestor-primary")
    _write(primary, "pkg/alpha.py", ALPHA_MODULE)
    _write(primary, "pkg/beta.py", BETA_MODULE)
    _write(primary, "pkg/gamma.py", GAMMA_MODULE)
    _write(primary, "pkg/unchanged.py", "def unchanged():\n    return 0\n")
    _commit(primary, "feat: the ancestor")

    _git(primary, "checkout", "-q", "-b", "ahead")
    _write(primary, "pkg/alpha.py", "def alpha():\n    return 11\n")
    _commit(primary, "feat: one")
    _write(primary, "pkg/beta.py", BETA_REWRITTEN)
    _commit(primary, "feat: two")
    _write(primary, "pkg/gamma.py", "def gamma():\n    return 33\n")
    _commit(primary, "feat: three")

    cold = tmp_path / "ancestor-cold"
    _git(primary, "worktree", "add", "-q", "--detach", str(cold), "ahead")
    # Put the primary back on the ancestor so its partition is the seed.
    _git(primary, "checkout", "-q", "main")

    return primary, cold, frozenset({"pkg/alpha.py", "pkg/beta.py", "pkg/gamma.py"})


@pytest.fixture(name="same_commit_checkouts")
def same_commit_checkouts_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Two checkouts of one repository standing on the SAME commit.

    This is AC12's trap, as a fixture. `git worktree add` writes every file
    fresh, so every mtime in `second` is newer than every mtime in `first`; an
    oracle built on `stat().st_mtime` reports the whole tree as changed here,
    which is the whole-repository rebuild per worktree this ticket must not
    ship. Our oracle is the sha, so the honest answer is zero files.
    """
    first = _init(tmp_path / "same-first")
    _write(first, "pkg/alpha.py", ALPHA_MODULE)
    _write(first, "pkg/beta.py", BETA_MODULE)
    _write(first, "web/delta.ts", DELTA_MODULE)
    _commit(first, "feat: one commit, two checkouts")

    second = tmp_path / "same-second"
    _git(first, "worktree", "add", "-q", "--detach", str(second), "HEAD")
    return first, second


# --------------------------------------------------------------------------
# lg-code-knowledge-284, test-break: the shapes a delta oracle meets in a
# real tree and never in a fixture written to be convenient.
#
# A full build asked git for `ls-files` and indexed exactly what came back.
# An incremental refresh asks a different question — "what moved" — and that
# question has answers `ls-files` never produces: a path git has to quote, a
# path git is supposed to ignore, a directory git refuses to descend into,
# and a tree with nothing in it at all.
# --------------------------------------------------------------------------

#: Paths git cannot print without quoting under its default (non `-z`)
#: output. A parser that splits newline-delimited output loses the first two
#: and INVENTS a third path from the halves of the last one.
SPACED_PATH = "pkg/with space.py"
QUOTED_PATH = 'pkg/with"quote.py'
NEWLINE_PATH = "pkg/with\nnewline.py"


@pytest.fixture(name="hostile_paths_repo")
def hostile_paths_repo_fixture(tmp_path: Path) -> Path:
    """A committed tree holding every path shape that breaks a naive parser.

    Three tracked paths git must quote, a `.gitignore` covering a directory
    and a suffix, and a nested git repository. The ignored and nested paths
    are created but never committed: they are what the untracked half of the
    delta would sweep in if it asked git the wrong question.

    `node_modules` and `.venv` are the production spelling of this: an
    untracked scan without `--exclude-standard` indexes tens of thousands of
    vendored files on every run entry, and a scan that descends into a nested
    checkout indexes another repository's sources under this one's identity.
    """
    return from_template(
        tmp_path / "hostile",
        "symbol_map_repos_hostile_paths_repo",
        _build_hostile_hostile_paths_repo,
    )


def _build_hostile_hostile_paths_repo(root: Path) -> None:
    root = _init(root)
    _write(root, ".gitignore", "vendor/\n*.generated.py\n")
    _write(root, "pkg/plain.py", "def plain():\n    return 1\n")
    _write(root, SPACED_PATH, "def with_space():\n    return 1\n")
    _write(root, QUOTED_PATH, "def with_quote():\n    return 1\n")
    _write(root, NEWLINE_PATH, "def with_newline():\n    return 1\n")
    _commit(root, "feat: paths git has to quote")

    # Ignored, and therefore never part of any answer about this tree.
    _write(root, "vendor/vendored.py", "def vendored():\n    return 1\n")
    _write(root, "pkg/thing.generated.py", "def generated():\n    return 1\n")
    # A nested checkout: git reports the DIRECTORY as untracked and refuses
    # to descend. A scan that descends indexes another repository.
    nested = _init(root / "nested")
    _write(nested, "inner.py", "def inner():\n    return 1\n")
    _commit(nested, "feat: another repository entirely")


@pytest.fixture(name="empty_tree_repo")
def empty_tree_repo_fixture(tmp_path: Path) -> Path:
    """A repository with a commit and no tracked files at all.

    Zero indexed files and zero files behind is the division `build_progress`
    has to answer without raising: `0 / (0 + 0)`. A fresh `git init` plus one
    empty commit is not exotic — it is every repository on its first day, and
    `map_status` is specified to never refuse.
    """
    return from_template(
        tmp_path / "empty-tree",
        "symbol_map_repos_empty_tree_repo",
        _build_empty_tree_empty_tree_repo,
    )


def _build_empty_tree_empty_tree_repo(root: Path) -> None:
    root = _init(root)
    _git(root, "commit", "-q", "--allow-empty", "-m", "chore: nothing yet")


# --------------------------------------------------------------------------
# Fingerprints and grounding (lg-code-knowledge-368).
#
# Scaffolding only. Nothing here imports the ticket's own modules, so a
# collection error in `test_symbol_fingerprints.py` cannot take the 281 suite
# down with it.
# --------------------------------------------------------------------------

#: The module whose every fingerprint is hardcoded in `GOLDEN_FINGERPRINTS`.
#: Eight symbols, one of each shape `python_ast` records plus a decorated
#: function and an async one, because an interpreter upgrade that changed
#: `ast.unparse` for any one of those shapes would silently re-fingerprint a
#: stored corpus (risk 2 / criterion 1).
GOLDEN_MODULE = '''\
"""A module whose every fingerprint is pinned by a golden test."""

import os

SCHEME_NAME = "sym1"
RETRIES: int = 3


class Ledger:
    """A class whose fingerprint covers its whole body."""

    def record(self, amount):
        return amount + 1

    async def flush(self):
        return None


def apply_stage_route(ticket, stage):
    """The function the ticket's own opening paragraph names."""
    return (ticket, stage)


async def drain(queue):
    while queue:
        queue.pop()


@staticmethod
def decorated():
    return os.sep
'''

#: qualified_name -> the fingerprint `fingerprint_for(ast.unparse(node))` must
#: produce for that node of `GOLDEN_MODULE`, written at `pkg/golden.py`.
#:
#: Computed by execution on CPython 3.11.15 on 2026-11-18, which is the
#: interpreter `server/pyproject.toml` pins the floor of. These are not a
#: derivation anybody repeats at test time: the whole point is that an
#: interpreter whose `ast.unparse` renders differently reds CI here rather
#: than re-fingerprinting a stored corpus in silence.
GOLDEN_FINGERPRINTS = {
    "pkg.golden.SCHEME_NAME": "sym1:4749999d68f4058965e3db7b3237b399",
    "pkg.golden.RETRIES": "sym1:0d1ef6fade671cafe52c988f990bbdaa",
    "pkg.golden.Ledger": "sym1:1182bf44fbff1e964b930fc402925f04",
    "pkg.golden.Ledger.record": "sym1:dcbd3346e34300fb69e72b85bd10778d",
    "pkg.golden.Ledger.flush": "sym1:17cd1d9a2d16c9c4b4c290ef1425c51b",
    "pkg.golden.apply_stage_route": "sym1:9405c71f785f3af8270b458e9e778ddb",
    "pkg.golden.drain": "sym1:6754142da07de9ce3c7c3661fe4dfa49",
    "pkg.golden.decorated": "sym1:cac5f843429edaad38bb85fa0106bedd",
}

#: Four functions, one per verdict the drift produces. Line numbers are
#: asserted against `GROUNDING_BASE_LINES`; keep the two in step.
GROUNDING_ALPHA = """\
def steady(ticket):
    return ticket.id


def edited(ticket):
    return ticket.id + 1


def renamed_away(ticket):
    return ticket.stage


def moving_house(ticket):
    return ticket.workspace_id
"""

#: `pkg/alpha.py` after the code moved on: one comment line at the top shifts
#: every symbol below it, `edited`'s one statement changes, `renamed_away`
#: becomes `gone_forever`, and `moving_house` leaves for `pkg/gamma.py`
#: byte-identical.
GROUNDING_ALPHA_DRIFTED = """\
# One comment line, so every symbol below it moves.
def steady(ticket):
    return ticket.id


def edited(ticket):
    return ticket.id + 2


def gone_forever(ticket):
    return ticket.stage
"""

GROUNDING_GAMMA = """\
def moving_house(ticket):
    return ticket.workspace_id
"""

GROUNDING_TS = """\
export function tsThing(): number {
  return 1;
}
"""

#: qualified_name -> (path, line) in the base tree.
GROUNDING_BASE_LINES = {
    "pkg.alpha.steady": ("pkg/alpha.py", 1),
    "pkg.alpha.edited": ("pkg/alpha.py", 5),
    "pkg.alpha.renamed_away": ("pkg/alpha.py", 9),
    "pkg.alpha.moving_house": ("pkg/alpha.py", 13),
}

#: Where each anchored symbol ends up after the drift, for the two verdicts
#: that carry a location.
GROUNDING_DRIFTED_LOCATIONS = {
    "pkg.alpha.steady": "pkg/alpha.py:2",
    "pkg.alpha.moving_house": "pkg/gamma.py:1",
}

#: The TypeScript symbol's qualified name, in the regex tier's own spelling.
TS_ANCHOR_NAME = "web/thing.ts#tsThing"


@pytest.fixture(name="golden_repo")
def golden_repo_fixture(tmp_path: Path) -> Path:
    """`GOLDEN_MODULE` alone, so the golden hexes can be read off a real build."""
    root = _init(tmp_path / "golden")
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/golden.py", GOLDEN_MODULE)
    _commit(root, "feat: the golden module")
    return root


@pytest.fixture(name="grounding_repo")
def grounding_repo_fixture(tmp_path: Path) -> Path:
    """The tree every verdict is measured against, BEFORE it drifts."""
    root = _init(tmp_path / "grounding")
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/alpha.py", GROUNDING_ALPHA)
    _write(root, "web/thing.ts", GROUNDING_TS)
    _commit(root, "feat: four anchorable functions")
    return root


def drift_grounding_repo(root: Path) -> str:
    """Move the code on, and return the new HEAD sha.

    A helper rather than a fixture because the order matters: anchors are
    resolved against the tree as it was, and only then does it drift. Every
    caller is itself a fixture, so a failure here is still an ERROR rather
    than a plausible-looking red.
    """
    _write(root, "pkg/alpha.py", GROUNDING_ALPHA_DRIFTED)
    _write(root, "pkg/gamma.py", GROUNDING_GAMMA)
    return _commit(root, "refactor: edit one, rename one, move one")


def commit_unindexed_change(root: Path) -> str:
    """One commit nothing rebuilds the map for, so freshness reads SHA_MISMATCH."""
    _write(root, "pkg/alpha.py", GROUNDING_ALPHA + "\n\ndef added_later(ticket):\n    return 0\n")
    return _commit(root, "feat: a commit the map has not seen")


# --------------------------------------------------------------------------
# Adversarial edges of the 7-step rule (lg-code-knowledge-368, test-break).
#
# The ticket's own scenario gives each verdict one clean cause. These shapes
# give two causes at once, which is where an ordered rule either holds its
# order or quietly reports the wrong fact:
#
#   `duplicated`  one fingerprint in two files   -> step 6 with a tie to break
#   `doubled`     one qualified name twice, the  -> step 4 must match ANY row,
#                 NON-matching copy written first    not the first one it reads
#   `forked`      edited in place AND copied     -> step 5 outranks step 6
#                 away byte-identical
# --------------------------------------------------------------------------

#: Three functions whose drift collides, one per corner above.
GROUNDING_BETA = """\
def duplicated(x):
    return x


def forked(ticket):
    return ticket.id


def doubled(x):
    return x + 1
"""

#: `pkg/beta.py` after the drift. `duplicated` has left for two files at once;
#: `forked` keeps its name and loses its body; `doubled` is defined twice, the
#: EDITED copy first, so an implementation that compares the first row it finds
#: reports CHANGED for a symbol whose original body is still in the file.
GROUNDING_BETA_DRIFTED = """\
def forked(ticket):
    return ticket.id + 1


def doubled(x):
    return x + 2


def doubled(x):
    return x + 1
"""

#: The byte-identical copy of the ORIGINAL `forked`, in a file of its own. Its
#: fingerprint matches the anchor, and it must not rescue a symbol that is
#: still present under its own name with a different body.
GROUNDING_DELTA = """\
def forked(ticket):
    return ticket.id
"""

#: Two byte-identical landing sites for `duplicated`, so the MOVED lookup has
#: a genuine collision to order. `aaa` sorts before `zzz`, which is the whole
#: content of the "(path, line)" promise the grounding docstring makes.
GROUNDING_DUPLICATE_COPY = """\
def duplicated(x):
    return x
"""

#: The three anchors the edges scenario stamps, and what each must decide.
EDGE_ANCHORS = ("pkg.beta.duplicated", "pkg.beta.forked", "pkg.beta.doubled")

#: Where `duplicated` must be reported after the drift: the first of its two
#: landing sites ordered by (path, line), never the last and never a coin flip.
EDGE_MOVED_LOCATION = "pkg/aaa.py:1"


@pytest.fixture(name="edges_repo")
def edges_repo_fixture(tmp_path: Path) -> Path:
    """The tree the colliding anchors are stamped against, BEFORE it drifts."""
    root = _init(tmp_path / "edges")
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/beta.py", GROUNDING_BETA)
    _commit(root, "feat: three anchorable functions that will collide")
    return root


def drift_edges_repo(root: Path) -> str:
    """Move the code on so every corner of the rule fires at once.

    A helper rather than a fixture for the reason `drift_grounding_repo` is:
    the ORDER is the test. Every caller is itself a fixture, so a failure in
    here is an ERROR rather than a plausible-looking red.
    """
    _write(root, "pkg/beta.py", GROUNDING_BETA_DRIFTED)
    _write(root, "pkg/delta.py", GROUNDING_DELTA)
    _write(root, "pkg/aaa.py", GROUNDING_DUPLICATE_COPY)
    _write(root, "pkg/zzz.py", GROUNDING_DUPLICATE_COPY)
    return _commit(root, "refactor: copy one twice, redefine one, fork one")
