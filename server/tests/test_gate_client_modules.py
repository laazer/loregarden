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

import json
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


def _with_client_change(workspace: Path) -> Path:
    # One of each source kind: the motion gate reads only stylesheets, and a
    # gate handed nothing it reads would pass instead of reporting unavailable.
    (workspace / "client" / "src" / "A.tsx").write_text(
        "export const A = () => <div>{items.map((i) => <p key={i}>{i}</p>)}</div>;\n"
    )
    (workspace / "client" / "src" / "a.css").write_text(".a { transition: opacity 0.2s; }\n")
    return workspace


def _run(scripts: Path, gate: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(scripts / gate), "--repo", str(workspace), "--scope", "worktree"],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=120,
    )


def _stub_package(checkout: Path, name: str, *, engines: str | None, index: str) -> None:
    package = checkout / "client" / "node_modules" / name
    package.mkdir(parents=True)
    manifest = {"name": name, "main": "index.js"}
    if engines is not None:
        manifest["engines"] = {"node": engines}
    (package / "package.json").write_text(json.dumps(manifest))
    (package / "index.js").write_text(index)


def _semver_dir() -> Path:
    """The semver typescript-estree itself resolves (the hoisted one may differ)."""
    manifest = _NODE_MODULES / "@typescript-eslint" / "typescript-estree" / "package.json"
    resolved = subprocess.run(
        [
            "node",
            "-e",
            "const m=require('module');"
            "console.log(require('path').dirname("
            "m.createRequire(process.argv[1]).resolve('semver/package.json')))",
            str(manifest),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(resolved.stdout.strip())


@pytest.fixture
def scripts_with_unsupported_node(scripts_without_client: Path) -> Path:
    """Every client package the gates load declares an engines range no Node meets."""
    checkout = scripts_without_client.parents[1]
    for name in ("@typescript-eslint/typescript-estree", "postcss"):
        _stub_package(checkout, name, engines=">=999", index="module.exports = {};\n")
    (checkout / "client" / "node_modules" / "semver").symlink_to(_semver_dir())
    return scripts_without_client


def test_every_ts_gate_is_covered():
    assert _GATES, f"no ts_*_check.cjs under {_SCRIPTS}"


@pytest.mark.parametrize("gate", _GATES)
def test_missing_toolchain_is_unavailable_not_failed(gate, scripts_without_client, workspace):
    result = _run(scripts_without_client, gate, _with_client_change(workspace))
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
    result = _run(scripts_without_client, gate, _with_client_change(workspace))
    assert result.returncode in (0, 1), result.stdout + result.stderr


@pytest.mark.skipif(not _NODE_MODULES.is_dir(), reason="needs client/node_modules")
@pytest.mark.parametrize("gate", _GATES)
def test_node_outside_engines_is_unavailable(gate, scripts_with_unsupported_node, workspace):
    """Under Node 14 typescript-estree does not load, `parseFile` swallowed the
    throw, and every gate reported "passed" over files it never parsed."""
    result = _run(scripts_with_unsupported_node, gate, _with_client_change(workspace))
    assert result.returncode == GATE_EX_UNAVAILABLE, result.stdout + result.stderr
    node_version = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert f"needs Node >=999; this is Node {node_version}" in result.stderr
    assert "passed" not in result.stdout


@pytest.mark.parametrize("gate", _GATES)
def test_package_that_fails_to_load_is_unavailable(gate, scripts_without_client, workspace):
    """Any load error, not only a missing package: a throw would reach `parseFile`."""
    checkout = scripts_without_client.parents[1]
    for name in ("@typescript-eslint/typescript-estree", "postcss"):
        _stub_package(checkout, name, engines=None, index="module.exports = ;\n")
    result = _run(scripts_without_client, gate, _with_client_change(workspace))
    assert result.returncode == GATE_EX_UNAVAILABLE, result.stdout + result.stderr
    assert "failed to load under Node" in result.stderr


_OLD_NODES = sorted(Path.home().glob(".nvm/versions/node/v1[0-7].*/bin/node"))


@pytest.mark.skipif(
    not (_OLD_NODES and _NODE_MODULES.is_dir()), reason="needs an nvm Node <18 and node_modules"
)
@pytest.mark.parametrize("gate", _GATES)
def test_a_real_old_node_never_changes_the_verdict(gate, workspace):
    """The installed toolchain under a real too-old Node, as it happened: each
    gate either reports itself unavailable or reaches the verdict current Node
    does (the motion gate's postcss supports Node 14; the parser does not)."""
    _with_client_change(workspace)

    def run(node: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [node, str(_SCRIPTS / gate), "--repo", str(workspace), "--scope", "worktree"],
            capture_output=True,
            text=True,
            env=_env(),
            timeout=120,
        )

    old, current = run(str(_OLD_NODES[0])), run("node")
    assert current.returncode in (0, 1), current.stdout + current.stderr
    if old.returncode != GATE_EX_UNAVAILABLE:
        assert (old.returncode, old.stdout) == (current.returncode, current.stdout)


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
