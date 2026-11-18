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
    root = _init(tmp_path / "mapped")
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
    return root


@pytest.fixture(name="broken_repo")
def broken_repo_fixture(tmp_path: Path) -> Path:
    """One good module and one `.py` that `ast.parse` cannot read.

    It has to be written here rather than taken from the checkout: verified at
    HEAD a4d45f9f8f, no real file in `server/loregarden` fails to parse under
    the 3.11 this suite runs on.
    """
    root = _init(tmp_path / "broken")
    _write(root, "good.py", "def good():\n    return 1\n")
    _write(root, "bad.py", "def oops(:\n")
    _write_bytes(root, "undecodable.ts", b"export const A = 1;\n\xff\xfe not utf-8\n")
    _commit(root, "feat: one of each failure")
    return root


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
    root = _init(tmp_path / "suggest")
    lines = [f"def {'a' * n}():\n    return {n}\n" for n in range(1, 31)]
    lines.append("def calculate_total_price():\n    return 0\n")
    _write(root, "names.py", "\n".join(lines))
    _commit(root, "feat: a spread of name lengths")
    return root


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
