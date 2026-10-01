"""The TypeScript gates, run where `client/node_modules` is not installed.

They parse with loregarden's own `client/` toolchain. Without it, node's
MODULE_NOT_FOUND used to exit 1 — the code for "found violations" — so every
stage transition in a fresh checkout reported a failure for the autofix loop to
chase. `gate_client_modules.cjs` turns it into 69 (EX_UNAVAILABLE), which
`gate_runner` reports as "could not run": the Node half of `gate_python_guard`.

Black-box: each gate is copied, with its siblings, into a tree whose `client/`
has a package.json and no node_modules, then run over a real git workspace with
something to parse. The set of gates is globbed, so a new `ts_*_check.cjs` is
covered without editing this file.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_NODE_MODULES = _ROOT / "client" / "node_modules"
_GATES = sorted(p.name for p in _SCRIPTS.glob("ts_*_check.cjs"))

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _env() -> dict[str, str]:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_env())


@pytest.fixture
def scripts_without_client(tmp_path: Path) -> Path:
    """The gate scripts, in a checkout whose client toolchain is not installed."""
    checkout = tmp_path / "loregarden"
    shutil.copytree(_SCRIPTS, checkout / ".lefthook" / "scripts")
    # ts_organization_check resolves its scope through server_python.sh.
    (checkout / "server").symlink_to(_ROOT / "server")
    (checkout / "client").mkdir()
    (checkout / "client" / "package.json").write_text('{"name": "client", "private": true}\n')
    return checkout / ".lefthook" / "scripts"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace with a committed base; tests add uncommitted work to it."""
    repo = tmp_path / "workspace"
    (repo / "client" / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "client" / "src" / ".keep").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _with_tsx_change(workspace: Path) -> Path:
    (workspace / "client" / "src" / "A.tsx").write_text(
        "export const A = () => <div>{items.map((i) => <p key={i}>{i}</p>)}</div>;\n"
    )
    return workspace


def _run(scripts: Path, gate: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(scripts / gate), "--repo", str(workspace), "--scope", "worktree"],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=120,
    )


def test_every_ts_gate_is_covered():
    assert _GATES, f"no ts_*_check.cjs under {_SCRIPTS}"


@pytest.mark.parametrize("gate", _GATES)
def test_missing_toolchain_is_unavailable_not_failed(gate, scripts_without_client, workspace):
    result = _run(scripts_without_client, gate, _with_tsx_change(workspace))
    assert result.returncode == GATE_EX_UNAVAILABLE, result.stdout + result.stderr


def test_theme_gate_without_postcss_is_unavailable(scripts_without_client, workspace):
    # The stylesheet path loads postcss, a different module from the TS parser.
    (workspace / "client" / "src" / "a.css").write_text(".a { color: var(--fg); }\n")
    result = _run(scripts_without_client, "ts_theme_check.cjs", workspace)
    assert result.returncode == GATE_EX_UNAVAILABLE, result.stdout + result.stderr


@pytest.mark.parametrize("gate", _GATES)
def test_nothing_to_parse_needs_no_toolchain(gate, scripts_without_client, workspace):
    # The parser loads lazily: a backend-only change neither pays for it nor needs it.
    result = _run(scripts_without_client, gate, workspace)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not _NODE_MODULES.is_dir(), reason="needs client/node_modules")
@pytest.mark.parametrize("gate", _GATES)
def test_installed_toolchain_examines_the_change(gate, scripts_without_client, workspace):
    """Control: the same tree with node_modules present does not report 69, so
    the cases above reach the parser rather than failing for some other reason."""
    (scripts_without_client.parents[1] / "client" / "node_modules").symlink_to(_NODE_MODULES)
    result = _run(scripts_without_client, gate, _with_tsx_change(workspace))
    assert result.returncode in (0, 1), result.stdout + result.stderr


@pytest.mark.parametrize("gate", _GATES)
def test_gates_load_client_packages_only_through_the_guard(gate):
    source = (_SCRIPTS / gate).read_text()
    assert 'require("./gate_client_modules.cjs")' in source
    assert "createRequire" not in source


def test_exit_code_agrees_with_the_gate_runner():
    source = (_SCRIPTS / "gate_client_modules.cjs").read_text()
    match = re.search(r"const EX_UNAVAILABLE = (\d+);", source)
    assert match is not None
    assert int(match.group(1)) == GATE_EX_UNAVAILABLE
