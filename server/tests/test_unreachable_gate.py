"""The unreachable-module gate, run as the node script it is.

Both directions are pinned: a module nothing imports must fail, and one the app
reaches by an unusual route (a lazy import, a worker URL, a type augmentation)
must not, because a gate that calls live code dead gets it deleted.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE
from tests.repo_paths import REPO_ROOT
from tests.toolchain import NPM_CI, require_commands, require_path

_GATE = REPO_ROOT / ".lefthook" / "scripts" / "ts_unreachable_check.cjs"
_REASON = "- its criteria need this module"


@pytest.fixture(autouse=True)
def _toolchain() -> None:
    require_commands("node")
    require_path(REPO_ROOT / "client" / "node_modules" / "typescript", NPM_CI)


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_env())


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    _write(tmp_path, "client/index.html", '<script type="module" src="/src/main.tsx"></script>')
    _write(tmp_path, "client/src/main.tsx", 'import "./used";\n')
    _write(tmp_path, "client/src/used.ts", "export const used = 1;\n")
    _git(tmp_path, "init", "-q", "-b", "main", ".")
    return tmp_path


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def _gate(repo: Path, scope: str = "worktree") -> tuple[int, str]:
    result = subprocess.run(
        ["node", str(_GATE), "--repo", str(repo), "--scope", scope],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    return result.returncode, result.stderr


def _findings(repo: Path) -> list[str]:
    code, err = _gate(repo)
    found = [line.strip() for line in err.splitlines() if line.startswith("  ")]
    assert code == (1 if found else 0), err
    return found


def test_a_tree_the_app_fully_reaches_passes(repo):
    assert _findings(repo) == []


def test_a_module_nothing_imports_fails_by_name(repo):
    _write(repo, "client/src/orphan.ts", "export const x = 1;\n")
    (finding,) = _findings(repo)
    assert finding.startswith("client/src/orphan.ts:")
    assert "kept-for:" in finding


def test_a_module_only_its_own_test_imports_fails(repo):
    _write(repo, "client/src/orphan.ts", "export const x = 1;\n")
    _write(repo, "client/src/__tests__/orphan.test.ts", 'import { x } from "../orphan";\nx;\n')
    (finding,) = _findings(repo)
    assert finding.startswith("client/src/orphan.ts:")


@pytest.mark.parametrize(
    "main",
    [
        'const load = () => import("./lazy");\nload();\n',
        'export * from "./lazy";\n',
        'import type { x } from "./lazy";\nexport type Y = typeof x;\n',
        'new Worker(new URL("./lazy.ts", import.meta.url));\n',
    ],
    ids=["dynamic-import", "re-export", "type-import", "worker-url"],
)
def test_every_way_of_loading_a_module_counts_as_use(repo, main):
    _write(repo, "client/src/main.tsx", f'import "./used";\n{main}')
    _write(repo, "client/src/lazy.ts", "export const x = 1;\n")
    assert _findings(repo) == []


@pytest.mark.parametrize(
    "body",
    [
        'declare module "some-lib" { interface Register { a: 1 } }\nexport {};\n',
        "declare global { interface Window { a: 1 } }\nexport {};\n",
    ],
    ids=["module", "global"],
)
def test_a_type_augmentation_is_loaded_by_the_compiler(repo, body):
    _write(repo, "client/src/augment.ts", body)
    assert _findings(repo) == []


def test_a_namespace_does_not_pass_for_an_augmentation(repo):
    _write(repo, "client/src/orphan.ts", "export namespace A { export const x = 1; }\n")
    assert len(_findings(repo)) == 1


def test_kept_for_covers_the_module_and_what_it_imports(repo):
    _write(
        repo,
        "client/src/floor.ts",
        f'// kept-for: lg-floor-72 {_REASON}\nimport "./scene";\n',
    )
    _write(repo, "client/src/scene.ts", "export const x = 1;\n")
    _write(repo, "client/src/orphan.ts", "export const x = 1;\n")
    (finding,) = _findings(repo)
    assert finding.startswith("client/src/orphan.ts:")


def test_kept_for_on_a_module_the_app_reaches_is_out_of_date(repo):
    _write(repo, "client/src/used.ts", f"// kept-for: lg-floor-72 {_REASON}\nexport {{}};\n")
    (finding,) = _findings(repo)
    assert finding.startswith("client/src/used.ts:1:")
    assert "Remove it" in finding


@pytest.mark.parametrize(
    ("marker", "complaint"),
    [
        ("kept-for: lg-floor-72", "a reason"),
        ("kept-for: lg-floor-72 - soon", "a reason"),
        (f"kept-for: 72 {_REASON}", "not a ticket's external id"),
        (f"used-by: scripts/missing.mjs {_REASON}", "does not exist"),
        (f"used-by: scripts/other.mjs {_REASON}", "never names orphan"),
    ],
)
def test_a_malformed_marker_covers_nothing(repo, marker, complaint):
    # Names another module whose name starts with this one's.
    _write(repo, "scripts/other.mjs", 'import("../client/src/orphanVerdict.ts");\n')
    _write(repo, "client/src/orphan.ts", f"// {marker}\nexport const x = 1;\n")
    findings = _findings(repo)
    assert len(findings) == 2
    assert any(complaint in f for f in findings)


def test_used_by_names_the_script_that_loads_it(repo):
    _write(repo, "scripts/qa.mjs", 'import("../client/src/orphan.ts");\n')
    _write(repo, "client/src/orphan.ts", f"// used-by: scripts/qa.mjs {_REASON}\nexport {{}};\n")
    assert _findings(repo) == []


def test_a_marker_in_a_string_is_not_one(repo):
    _write(
        repo, "client/src/orphan.ts", f'export const s = "// kept-for: lg-floor-72 {_REASON}";\n'
    )
    assert len(_findings(repo)) == 1


def test_a_commit_is_not_graded_on_untracked_files(repo):
    _git(repo, "add", "-A")
    _write(repo, "client/src/orphan.ts", "export const x = 1;\n")
    assert _gate(repo, "staged")[0] == 0
    _git(repo, "add", "-A")
    assert _gate(repo, "staged")[0] == 1


def test_globbing_sources_is_refused_rather_than_misread(repo):
    _write(
        repo,
        "client/src/main.tsx",
        'import "./used";\nconst all = import.meta.glob("./pages/*.tsx");\n',
    )
    (finding,) = _findings(repo)
    assert "import.meta.glob" in finding


def test_a_page_with_no_entry_fails_instead_of_passing_everything(repo):
    _write(repo, "client/index.html", "<p>no script</p>")
    (finding,) = _findings(repo)
    assert "no module entry" in finding


def test_a_checkout_git_cannot_list_fails_instead_of_passing(tmp_path):
    _write(tmp_path, "client/index.html", '<script type="module" src="/src/main.tsx"></script>')
    _write(tmp_path, "client/src/main.tsx", "export {};\n")
    code, err = _gate(tmp_path)
    assert code == GATE_EX_UNAVAILABLE, err
