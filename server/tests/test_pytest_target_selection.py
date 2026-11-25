"""Pre-push test selection: which tests can the pushed commits actually break?

The full suite is minutes, so pre-push runs only the tests that reach the
changed modules. That trade is only safe while the fallbacks hold: every case
the selector cannot map has to widen to the full suite, never narrow to a fast
green. These tests pin the fallbacks first and the graph second.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_ROOT = REPO_ROOT
_SELECTOR = _ROOT / ".lefthook" / "scripts" / "select_pytest_targets.py"
_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _load():
    spec = importlib.util.spec_from_file_location("select_pytest_targets", _SELECTOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


selector = _load()


# --------------------------------------------------------------------------- #
# fallbacks: everything unmappable must widen, never narrow
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "server/tests/conftest.py",
        "server/tests/factories.py",
        "server/tests/worktree_helpers.py",
        "server/pyproject.toml",
        "server/loregarden/data/seed.json",
        "lefthook.yml",
        ".lefthook/lefthook-local.yml",
        "agent_context/orchestration/default.yaml",
        "agent_context/workflows/default.yaml",
        "agent_context/README.md",
    ],
)
def test_unmappable_change_runs_everything(path):
    assert selector.full_suite_reason([path]) is not None


@pytest.mark.parametrize(
    "paths",
    [
        ["server/loregarden/services/doctor.py"],
        ["server/tests/test_doctor.py"],
        ["client/src/App.tsx"],  # jest's problem, not pytest's
        ["server/loregarden/services/doctor.py", "client/src/App.tsx"],
        ["agent_context/agents/misc_agents/repair_v1.md"],
        ["agent_context/skills/refactor/SKILL.md"],
        [".lefthook/scripts/sync-deps.sh"],
        ["scripts/reload-backend.sh", "server/loregarden/services/doctor.py"],
        ["CLAUDE.md"],
        [".github/pull_request_template.md", "docs/tauri.md"],
    ],
)
def test_mappable_change_is_narrowed(paths):
    assert selector.full_suite_reason(paths) is None


# --------------------------------------------------------------------------- #
# the import graph
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "server" / "loregarden" / "services"
    pkg.mkdir(parents=True)
    (tmp_path / "server" / "loregarden" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "leaf.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "middle.py").write_text("from loregarden.services.leaf import VALUE\n", encoding="utf-8")
    (pkg / "unrelated.py").write_text("OTHER = 2\n", encoding="utf-8")

    tests = tmp_path / "server" / "tests"
    tests.mkdir()
    (tests / "test_middle.py").write_text(
        "from loregarden.services import middle\n\ndef test_x():\n    assert middle\n",
        encoding="utf-8",
    )
    (tests / "test_unrelated.py").write_text(
        "from loregarden.services import unrelated\n\ndef test_y():\n    assert unrelated\n",
        encoding="utf-8",
    )
    (tests / "test_dynamic.py").write_text(
        "import importlib\n\ndef test_z():\n"
        '    assert importlib.import_module("loregarden.services.leaf")\n',
        encoding="utf-8",
    )
    return tmp_path


def _names(paths) -> set[str]:
    return {p.name for p in paths}


def test_transitive_import_is_selected(fake_repo: Path):
    """test_middle imports middle, which imports leaf. Changing leaf must select it."""
    selected = selector.select(fake_repo, ["server/loregarden/services/leaf.py"])
    assert "test_middle.py" in _names(selected)
    assert "test_unrelated.py" not in _names(selected)


def test_string_reference_counts_as_an_import(fake_repo: Path):
    """importlib.import_module("loregarden.x") is invisible to an import-only walk."""
    selected = selector.select(fake_repo, ["server/loregarden/services/leaf.py"])
    assert "test_dynamic.py" in _names(selected)


def test_select_direct_keeps_only_the_tests_importing_the_module_themselves(fake_repo: Path):
    """test_middle reaches leaf only through middle, so the direct ring drops it;
    test_dynamic names leaf itself (a string import counts) and stays."""
    selected = selector.select_direct(fake_repo, ["server/loregarden/services/leaf.py"])
    assert _names(selected) == {"test_dynamic.py"}
    assert set(selected) <= set(selector.select(fake_repo, ["server/loregarden/services/leaf.py"]))


def test_select_direct_ignores_changed_tests_and_non_modules(fake_repo: Path):
    assert selector.select_direct(fake_repo, ["server/tests/test_unrelated.py"]) == []


def test_changed_test_file_runs_itself(fake_repo: Path):
    selected = selector.select(fake_repo, ["server/tests/test_unrelated.py"])
    assert _names(selected) == {"test_unrelated.py"}


def test_deleted_test_file_is_not_selected(fake_repo: Path):
    """pytest reports a missing path as an error, and a stale name would abort the run."""
    selected = selector.select(fake_repo, ["server/tests/test_gone.py"])
    assert selected == []


@pytest.fixture
def chain_repo(tmp_path: Path) -> Path:
    """base <- one <- two <- three, each with a test; `far` is reached only from three."""
    pkg = tmp_path / "server" / "loregarden"
    tests = tmp_path / "server" / "tests"
    pkg.mkdir(parents=True)
    tests.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    imports = {"base": "far", "one": "base", "two": "one", "three": "two", "alone": "", "far": ""}
    for name, imported in imports.items():
        body = f"from loregarden import {imported}\n" if imported else ""
        (pkg / f"{name}.py").write_text(body, encoding="utf-8")
    for name in ("base", "one", "two", "three"):
        (tests / f"test_{name}.py").write_text(f"from loregarden import {name}\n", encoding="utf-8")
    return tmp_path


def _selected(repo: Path, *modules: str, limit: int | None = None) -> list[str]:
    paths = [f"server/loregarden/{name}.py" for name in modules]
    return [path.name for path in selector.run_first(repo, paths, limit)]


def test_the_nearest_tests_are_those_within_two_imports(chain_repo: Path):
    assert _selected(chain_repo, "base", limit=2) == ["test_base.py", "test_one.py"]


def test_without_a_limit_every_test_the_change_reaches_is_kept_closest_first(chain_repo: Path):
    expected = ["test_base.py", "test_one.py", "test_two.py", "test_three.py"]
    assert _selected(chain_repo, "base") == expected


def test_a_module_with_no_test_that_near_keeps_its_nearest(chain_repo: Path):
    (chain_repo / "server" / "tests" / "test_base.py").unlink()
    (chain_repo / "server" / "tests" / "test_one.py").unlink()
    assert _selected(chain_repo, "base", limit=2) == ["test_two.py"]


def test_a_well_covered_module_does_not_hide_one_that_is_not(chain_repo: Path):
    """`three` has a test of its own; `far`'s nearest is two imports away, and counts."""
    (chain_repo / "server" / "tests" / "test_base.py").unlink()
    (chain_repo / "server" / "tests" / "test_one.py").unlink()
    assert _selected(chain_repo, "far", "three", limit=2) == ["test_three.py", "test_two.py"]


def test_a_test_reading_the_packages_source_runs_whatever_module_changed(chain_repo: Path):
    (chain_repo / "server" / "tests" / "test_tree.py").write_text(
        'import pathlib\n\nFILES = pathlib.Path("loregarden").rglob("*.py")\n', encoding="utf-8"
    )
    assert "test_tree.py" in _selected(chain_repo, "base", limit=2)


def test_a_test_globbing_other_files_does_not(chain_repo: Path):
    (chain_repo / "server" / "tests" / "test_tree.py").write_text(
        'import pathlib\n\nFILES = pathlib.Path("loregarden").rglob("*.sql")\n', encoding="utf-8"
    )
    assert "test_tree.py" not in _selected(chain_repo, "base", limit=2)


def test_a_test_naming_the_changed_file_runs_with_its_importers(chain_repo: Path):
    """test_hold_line reads builtin_orchestrator.py and imports nothing that does."""
    (chain_repo / "server" / "tests" / "test_about.py").write_text(
        'SOURCE = "base.py"\n', encoding="utf-8"
    )
    assert _selected(chain_repo, "base", limit=2) == [
        "test_about.py",
        "test_base.py",
        "test_one.py",
    ]


def test_a_test_is_as_near_as_its_nearest_import(chain_repo: Path):
    (chain_repo / "server" / "tests" / "test_wide.py").write_text(
        "from loregarden import base, three\n", encoding="utf-8"
    )
    assert "test_wide.py" in _selected(chain_repo, "base", limit=1)


def test_source_readers_alone_are_no_evidence_a_test_reaches_the_change(chain_repo: Path):
    (chain_repo / "server" / "tests" / "test_tree.py").write_text(
        'import pathlib\n\nFILES = pathlib.Path("loregarden").rglob("*.py")\n', encoding="utf-8"
    )
    assert _selected(chain_repo, "alone", limit=2) == []


def test_a_source_reader_is_not_handed_to_mutation_testing(chain_repo: Path):
    """It fails on any edit to what it reads, which would count as killing every mutant."""
    (chain_repo / "server" / "tests" / "test_tree.py").write_text(
        'import pathlib\n\nFILES = pathlib.Path("loregarden").rglob("*.py")\n', encoding="utf-8"
    )
    assert "test_tree.py" not in _names(selector.select(chain_repo, ["server/loregarden/base.py"]))


@pytest.fixture
def hub_repo(chain_repo: Path) -> Path:
    """A package re-exporting two modules, and a test importing a name from each."""
    hub = chain_repo / "server" / "loregarden" / "hub"
    hub.mkdir()
    (hub / "__init__.py").write_text(
        "from loregarden.hub.cats import *\nfrom loregarden.hub.dogs import Dog, OTHER as KENNEL\n",
        encoding="utf-8",
    )
    (hub / "cats.py").write_text("class Cat: ...\nPURR: str = 'r'\nCLAW = 1\n", encoding="utf-8")
    (hub / "dogs.py").write_text("class Dog: ...\nOTHER = 2\n", encoding="utf-8")
    tests = chain_repo / "server" / "tests"
    for name, imported in {
        "cat": "Cat",
        "purr": "PURR",
        "claw": "CLAW",
        "dog": "Dog",
        "kennel": "KENNEL",
        "unknown": "Elsewhere",
    }.items():
        (tests / f"test_{name}.py").write_text(
            f"from loregarden.hub import {imported}\n", encoding="utf-8"
        )
    (tests / "test_whole.py").write_text("import loregarden.hub\n", encoding="utf-8")
    return chain_repo


def test_a_name_from_a_package_reaches_the_module_that_defines_it(hub_repo: Path):
    """Importing `Ticket` from models.domain is not importing every enum beside it."""
    cats = {"test_cat.py", "test_purr.py", "test_claw.py", "test_whole.py", "test_unknown.py"}
    assert set(_selected(hub_repo, "hub/cats")) == cats
    dogs = {"test_dog.py", "test_kennel.py", "test_whole.py", "test_unknown.py"}
    assert set(_selected(hub_repo, "hub/dogs")) == dogs


def test_a_packages_own_file_reaches_everything_imported_from_it(hub_repo: Path):
    everyone = {
        "test_cat.py",
        "test_purr.py",
        "test_claw.py",
        "test_dog.py",
        "test_kennel.py",
        "test_whole.py",
        "test_unknown.py",
    }
    assert set(_selected(hub_repo, "hub/__init__")) == everyone


@pytest.mark.parametrize(
    ("named_base", "reach"), [("", "--nearest"), ("origin/main", "")], ids=["push", "landing"]
)
def test_a_push_runs_the_nearest_and_a_named_range_runs_the_whole_reach(named_base, reach):
    result = subprocess.run(
        ["bash", "-euc", 'source "$0"; printf "%s" "${SELECT_REACH[@]+${SELECT_REACH[*]}}"']
        + [str(_ROOT / ".lefthook" / "scripts" / "push-base.sh")],
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV | {"LOREGARDEN_TESTS_BASE": named_base},
        check=True,
    )
    assert result.stdout == reach
    for script in ("server-tests.sh", "push-prerequisites.sh"):
        text = (_ROOT / ".lefthook" / "scripts" / script).read_text(encoding="utf-8")
        calls = [line for line in text.splitlines() if "--repo " in line and "--base " in line]
        assert calls and all('${SELECT_REACH[@]+"${SELECT_REACH[@]}"}' in line for line in calls)


@pytest.fixture
def client_reader_repo(fake_repo: Path) -> Path:
    """`fake_repo` plus tests that read the client tree, and one that only says the word."""
    tests = fake_repo / "server" / "tests"
    (tests / "test_reads_client.py").write_text(
        "from pathlib import Path\n\n"
        'ROOT = Path(__file__).parents[2] / "client"\n\n'
        "def test_c():\n    assert ROOT\n",
        encoding="utf-8",
    )
    (tests / "test_reads_package_json.py").write_text(
        'def test_p():\n    assert "client/package.json"\n', encoding="utf-8"
    )
    (tests / "test_says_client_id.py").write_text(
        'def test_i():\n    assert {"client_id": 1, "who": "api client"}\n', encoding="utf-8"
    )
    # A package module naming the client tree is NOT followed: in this repo
    # every such module names some workspace's tree, not this repo's client.
    (fake_repo / "server" / "loregarden" / "services" / "unrelated.py").write_text(
        'OTHER = 2\nSCOPE = ("client",)\n', encoding="utf-8"
    )
    return fake_repo


def test_client_change_selects_the_tests_that_read_the_client_tree(client_reader_repo: Path):
    """A client-only diff once selected zero, which meant the whole server suite."""
    selected = selector.select(client_reader_repo, ["client/src/App.tsx"])
    assert _names(selected) == {"test_reads_client.py", "test_reads_package_json.py"}


def test_client_and_server_change_selects_both(client_reader_repo: Path):
    selected = selector.select(
        client_reader_repo, ["client/src/App.tsx", "server/loregarden/services/leaf.py"]
    )
    assert _names(selected) == {
        "test_reads_client.py",
        "test_reads_package_json.py",
        "test_middle.py",
        "test_dynamic.py",
    }


def test_server_only_change_does_not_pull_in_client_readers(client_reader_repo: Path):
    selected = selector.select(client_reader_repo, ["server/loregarden/services/leaf.py"])
    assert "test_reads_client.py" not in _names(selected)


ROLE = "agent_context/agents/misc_agents/role_v1.md"
SKILL = "agent_context/skills/sorting/SKILL.md"


@pytest.fixture
def prose_repo(fake_repo: Path) -> Path:
    """`fake_repo` plus agent prose, the modules naming it, and tests at each distance."""
    for rel in (ROLE, SKILL, "agent_context/agents/misc_agents/other_v1.md"):
        (fake_repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (fake_repo / rel).write_text("# prose\n", encoding="utf-8")
    pkg = fake_repo / "server" / "loregarden" / "services"
    (pkg / "roles.py").write_text('ROLE_FILE = "agents/misc_agents/role_v1.md"\n', encoding="utf-8")
    (pkg / "skills.py").write_text('SKILL_FILE = "SKILL.md"\n', encoding="utf-8")
    (pkg / "wrapper.py").write_text("from loregarden.services import roles\n", encoding="utf-8")
    tests = fake_repo / "server" / "tests"
    bodies = {
        "test_roles.py": "from loregarden.services import roles\n",
        "test_skills.py": "from loregarden.services import skills\n",
        # Two hops from the reader: every DB test is, through init_db.
        "test_wrapper.py": "from loregarden.services import wrapper\n",
        "test_reads_tree.py": 'from pathlib import Path\nROOT = Path("agent_context")\n',
        "test_names_role.py": 'ROLE = "role_v1.md"\n',
    }
    for name, body in bodies.items():
        (tests / name).write_text(f"{body}\ndef test_t():\n    pass\n", encoding="utf-8")
    return fake_repo


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        pytest.param(
            ROLE, {"test_roles.py", "test_reads_tree.py", "test_names_role.py"}, id="role"
        ),
        pytest.param(SKILL, {"test_skills.py", "test_reads_tree.py"}, id="skill-by-glob"),
        pytest.param(
            "agent_context/agents/misc_agents/other_v1.md", {"test_reads_tree.py"}, id="unnamed"
        ),
    ],
)
def test_agent_prose_selects_the_tests_that_read_it(prose_repo: Path, changed, expected):
    """Editing one role file once ran every test in the suite."""
    assert _names(selector.select(prose_repo, [changed])) == expected


def test_agent_prose_and_a_module_change_select_both(prose_repo: Path):
    selected = selector.select(prose_repo, [SKILL, "server/loregarden/services/leaf.py"])
    assert _names(selected) == {
        "test_skills.py",
        "test_reads_tree.py",
        "test_middle.py",
        "test_dynamic.py",
    }


@pytest.mark.parametrize(
    ("constant", "names_it"),
    [
        ("agent_context/agents/misc_agents/role_v1.md", True),
        ("agents/misc_agents/role_v1.md", True),
        ("misc_agents/role_v1.md", True),
        ("role_v1.md", True),
        ("isc_agents/role_v1.md", False),
        ("le_v1.md", False),
        ("other_v1.md", False),
    ],
)
def test_a_constant_names_a_prose_file_by_path_suffix_or_basename(constant, names_it):
    assert selector.names_context_file(frozenset([constant]), ROLE) is names_it


# --------------------------------------------------------------------------- #
# hook and repo scripts: named by path, module name or import, never the graph
# --------------------------------------------------------------------------- #


@pytest.fixture
def script_repo(fake_repo: Path) -> Path:
    """`run.sh` runs `gate.py`, which imports `helper`; tests name each a different way."""
    hooks = fake_repo / ".lefthook" / "scripts"
    hooks.mkdir(parents=True)
    (hooks / "run.sh").write_text('python3 .lefthook/scripts/gate.py "$@"\n', encoding="utf-8")
    (hooks / "gate.py").write_text("from helper import parse\n", encoding="utf-8")
    (hooks / "helper.py").write_text("def parse(): ...\n", encoding="utf-8")
    (hooks / "lonely.sh").write_text("echo hi\n", encoding="utf-8")
    (fake_repo / "scripts").mkdir()
    (fake_repo / "scripts" / "reload.sh").write_text("touch x\n", encoding="utf-8")
    (fake_repo / "scripts" / "unnamed.sh").write_text("echo\n", encoding="utf-8")
    pkg = fake_repo / "server" / "loregarden" / "services"
    (pkg / "runner.py").write_text('SCRIPT = "lonely.sh"\n', encoding="utf-8")

    tests = fake_repo / "server" / "tests"
    bodies = {
        "test_runs_hook.py": 'HOOK = ROOT / ".lefthook" / "scripts" / "run.sh"',
        "test_spec_gate.py": 'spec = spec_from_file_location("gate", PATH)',
        "test_imports_helper.py": "import helper",
        "test_reload.py": 'SCRIPT = "scripts/reload.sh"',
        "test_runner.py": "from loregarden.services import runner",
        "test_bystander.py": 'NAME = "something else.sh"',
    }
    for name, body in bodies.items():
        (tests / name).write_text(f"{body}\ndef test_t():\n    pass\n", encoding="utf-8")
    return fake_repo


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        # Named by its own path; nothing reaches it.
        ("scripts/reload.sh", {"test_reload.py"}),
        # By module name; and run.sh, which runs it, brings in test_runs_hook.
        (".lefthook/scripts/gate.py", {"test_spec_gate.py", "test_runs_hook.py"}),
        # Imported by gate.py, which run.sh runs: every way in is selected.
        (
            ".lefthook/scripts/helper.py",
            {"test_imports_helper.py", "test_spec_gate.py", "test_runs_hook.py"},
        ),
        # Named only by a package module: the tests importing that module.
        (".lefthook/scripts/lonely.sh", {"test_runner.py"}),
        # Named by nothing: zero, which the CLI turns into the full suite.
        ("scripts/unnamed.sh", set()),
    ],
)
def test_a_script_change_selects_the_tests_that_reach_it(
    script_repo: Path, changed: str, expected: set[str]
):
    assert _names(selector.select(script_repo, [changed])) == expected


def test_repo_markdown_selects_the_tests_naming_it_and_the_scripts_that_read_it(
    script_repo: Path,
):
    (script_repo / "CLAUDE.md").write_text("# rules\n", encoding="utf-8")
    (script_repo / ".lefthook" / "scripts" / "md_check.py").write_text(
        'ALLOWED = {"CLAUDE.md"}\n', encoding="utf-8"
    )
    tests = script_repo / "server" / "tests"
    (tests / "test_reads_rules.py").write_text(
        'RULES = ROOT / "CLAUDE.md"\ndef test_t():\n    pass\n', encoding="utf-8"
    )
    (tests / "test_md_check.py").write_text(
        'NAME = "md_check"\ndef test_t():\n    pass\n', encoding="utf-8"
    )

    assert _names(selector.select(script_repo, ["CLAUDE.md"])) == {
        "test_reads_rules.py",
        "test_md_check.py",
    }


def test_a_deleted_script_still_selects_what_named_it(script_repo: Path):
    (script_repo / ".lefthook" / "scripts" / "helper.py").unlink()

    selected = _names(selector.select(script_repo, [".lefthook/scripts/helper.py"]))

    assert "test_imports_helper.py" in selected


@pytest.mark.parametrize(
    ("names", "rel", "named"),
    [
        ({"sync-deps.sh"}, ".lefthook/scripts/sync-deps.sh", True),
        ({"scripts/sync-deps.sh"}, ".lefthook/scripts/sync-deps.sh", True),
        ({"py_organization_check"}, ".lefthook/scripts/py_organization_check.py", True),
        # A stem names a Python module only; `capture` is not `capture.sh`.
        ({"capture"}, ".lefthook/scripts/capture.sh", False),
        ({"sync-deps"}, ".lefthook/scripts/sync-deps.sh", False),
    ],
)
def test_a_name_reaches_a_script_by_path_basename_or_module(names, rel, named):
    assert selector.names_script(frozenset(names), rel) is named


def test_cli_narrows_a_hook_only_change(script_repo: Path):
    _git(script_repo, "init", "-q", ".")
    _git(script_repo, "config", "user.email", "t@example.com")
    _git(script_repo, "config", "user.name", "t")
    _git(script_repo, "add", "-A")
    _git(script_repo, "commit", "-qm", "base")
    (script_repo / "scripts" / "reload.sh").write_text("touch y\n", encoding="utf-8")
    _git(script_repo, "commit", "-aqm", "revise the reload script")

    result = _run_cli(script_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_SELECTED, result.stderr
    assert result.stdout.split() == ["server/tests/test_reload.py"]


# --------------------------------------------------------------------------- #
# the CLI contract the hook depends on
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_SCRUBBED_ENV)


def _run_cli(repo: Path, base: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SELECTOR), "--repo", str(repo), "--base", base, *extra],
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV,
    )


@pytest.fixture
def git_repo(fake_repo: Path) -> Path:
    _git(fake_repo, "init", "-q", ".")
    _git(fake_repo, "config", "user.email", "t@example.com")
    _git(fake_repo, "config", "user.name", "t")
    _git(fake_repo, "add", "-A")
    _git(fake_repo, "commit", "-qm", "base")
    return fake_repo


def test_cli_prints_repo_relative_paths(git_repo: Path):
    leaf = git_repo / "server" / "loregarden" / "services" / "leaf.py"
    leaf.write_text("VALUE = 2\n", encoding="utf-8")
    _git(git_repo, "commit", "-aqm", "change leaf")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_SELECTED
    assert result.stdout.split() == ["server/tests/test_dynamic.py", "server/tests/test_middle.py"]


def test_cli_signals_full_suite_with_a_reason(git_repo: Path):
    (git_repo / "lefthook.yml").write_text("x: 1\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "touch repo config")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING
    assert "lefthook.yml" in result.stderr


def test_a_full_run_still_names_what_the_mappable_changes_reach(git_repo: Path, tmp_path: Path):
    """`lefthook.yml` forces the full suite; the leaf change's tests go first in it."""
    (git_repo / "lefthook.yml").write_text("x: 1\n", encoding="utf-8")
    leaf = git_repo / "server" / "loregarden" / "services" / "leaf.py"
    leaf.write_text("VALUE = 2\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "touch repo config and leaf")
    out = tmp_path / "run-first.txt"

    result = _run_cli(git_repo, "HEAD~1", "--run-first-out", str(out))

    assert result.returncode == selector.EXIT_RUN_EVERYTHING, result.stderr
    assert "lefthook.yml" in result.stderr
    assert result.stdout == ""
    assert out.read_text(encoding="utf-8").split() == [
        "server/tests/test_dynamic.py",
        "server/tests/test_middle.py",
    ]


def test_run_first_puts_the_closest_tests_first(fake_repo: Path):
    """Changed tests, then direct importers, then transitive ones — not alphabetical.

    "Reaches" is most of the real suite for a widely imported module (691 of
    ~720 files for `git_subprocess.py`); in path order the broken test there
    ran 4546th of 14386, and closest-first moved it to 859th.
    """
    tests = fake_repo / "server" / "tests"
    (tests / "test_aaa_transitive.py").write_text(
        "from loregarden.services import middle\n\ndef test_a():\n    assert middle\n",
        encoding="utf-8",
    )
    (tests / "test_zzz_changed.py").write_text("def test_c():\n    pass\n", encoding="utf-8")

    order = selector.run_first(
        fake_repo,
        ["lefthook.yml", "server/loregarden/services/leaf.py", "server/tests/test_zzz_changed.py"],
    )

    assert [path.name for path in order] == [
        "test_zzz_changed.py",
        "test_dynamic.py",
        "test_aaa_transitive.py",
        "test_middle.py",
    ]


def test_cli_keeps_the_nearest_when_asked(chain_repo: Path):
    _git(chain_repo, "init", "-q", ".")
    _git(chain_repo, "config", "user.email", "t@example.com")
    _git(chain_repo, "config", "user.name", "t")
    _git(chain_repo, "add", "-A")
    _git(chain_repo, "commit", "-qm", "base")
    (chain_repo / "server" / "loregarden" / "base.py").write_text("X = 1\n", encoding="utf-8")
    _git(chain_repo, "commit", "-aqm", "change base")

    nearest = _run_cli(chain_repo, "HEAD~1", "--nearest")
    whole = _run_cli(chain_repo, "HEAD~1")

    assert nearest.stdout.split() == ["server/tests/test_base.py", "server/tests/test_one.py"]
    assert len(whole.stdout.split()) == 4


def test_a_narrowed_run_names_its_selection_to_run_first(git_repo: Path, tmp_path: Path):
    leaf = git_repo / "server" / "loregarden" / "services" / "leaf.py"
    leaf.write_text("VALUE = 2\n", encoding="utf-8")
    _git(git_repo, "commit", "-aqm", "change leaf")
    out = tmp_path / "run-first.txt"

    result = _run_cli(git_repo, "HEAD~1", "--run-first-out", str(out))

    assert result.returncode == selector.EXIT_SELECTED, result.stderr
    assert out.read_text(encoding="utf-8").split() == result.stdout.split()


def test_an_unmappable_only_diff_names_nothing_to_run_first(git_repo: Path, tmp_path: Path):
    (git_repo / "lefthook.yml").write_text("x: 1\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "touch repo config")
    out = tmp_path / "run-first.txt"

    result = _run_cli(git_repo, "HEAD~1", "--run-first-out", str(out))

    assert result.returncode == selector.EXIT_RUN_EVERYTHING
    assert out.read_text(encoding="utf-8") == ""


def test_cli_runs_everything_when_nothing_is_reachable(git_repo: Path):
    """Zero selected tests is more often a hole in the graph than a fact."""
    orphan = git_repo / "server" / "loregarden" / "services" / "orphan.py"
    orphan.write_text("X = 1\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "add orphan module")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING
    assert "no test file reaches" in result.stderr


def test_cli_runs_everything_on_an_empty_diff(git_repo: Path):
    result = _run_cli(git_repo, "HEAD")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("from loregarden.services.leaf import VALUE\n\x00\n", id="nul-byte"),
        pytest.param("from loregarden.services.leaf import VALUE\ndef (:\n", id="syntax-error"),
    ],
)
def test_cli_runs_everything_when_a_module_cannot_be_parsed(git_repo: Path, body: str):
    """An unparseable module is a hole in the graph, not a module importing nothing.

    test_middle reaches leaf only through middle. Recording middle as edgeless
    dropped test_middle from a leaf change while test_dynamic kept the run
    looking narrowed and green.
    """
    pkg = git_repo / "server" / "loregarden" / "services"
    (pkg / "middle.py").write_text(body, encoding="utf-8")
    (pkg / "leaf.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(git_repo, "commit", "-aqm", "break middle, change leaf")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING, result.stdout
    assert "middle.py" in result.stderr, result.stderr
    assert result.stdout == "", result.stdout


def test_cli_narrows_a_change_to_agent_prose(prose_repo: Path):
    _git(prose_repo, "init", "-q", ".")
    _git(prose_repo, "config", "user.email", "t@example.com")
    _git(prose_repo, "config", "user.name", "t")
    _git(prose_repo, "add", "-A")
    _git(prose_repo, "commit", "-qm", "base")
    (prose_repo / ROLE).write_text("# prose, revised\n", encoding="utf-8")
    _git(prose_repo, "commit", "-aqm", "revise the role")

    result = _run_cli(prose_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_SELECTED, result.stderr
    assert result.stdout.split() == [
        "server/tests/test_names_role.py",
        "server/tests/test_reads_tree.py",
        "server/tests/test_roles.py",
    ]


# --------------------------------------------------------------------------- #
# the hook wiring
# --------------------------------------------------------------------------- #


def test_server_hook_falls_back_to_the_full_suite():
    """Every path out of selection must reach a real pytest run.

    The worker count used to be the literal `-n auto`, and this asserted that
    string twice. It is now `-n "$TEST_WORKERS"`, a budget shared with the client
    runner: `auto` takes one worker per core, which on a box that also serves the
    MCP gateway produced load average 158 and a control plane that stopped
    answering. The intent asserted here is unchanged — both paths out of
    selection invoke pytest for real — and the *absence* of `-n auto` is pinned
    in test_prepush_worker_budget, which owns the budget itself.

    Matched on the invocation's stable tail rather than on the whole command.
    This assertion has now broken twice for a reason it does not care about: once
    when the worker count changed, and once when `-x` was added in front of `-q`.
    Both times the property it names — every path out of selection reaches a real
    pytest run — was still true. `-n "$TEST_WORKERS" --basetemp=` identifies the
    two real call sites exactly (the echo lines interpolate the variable
    unquoted), and survives a flag being added ahead of it.
    """
    script = (_ROOT / ".lefthook" / "scripts" / "server-tests.sh").read_text(encoding="utf-8")
    assert "select_pytest_targets.py" in script
    # Not a bare `python3`: a 3.9 on PATH cannot parse a module using `match`.
    assert 'server_python.sh" "$ROOT/.lefthook/scripts/select_pytest_targets.py"' in script
    assert "LOREGARDEN_FULL_TESTS" in script
    assert "-n auto" not in script, "a worker per core is what the budget replaced"
    # Two invocation sites: the full-suite fallback and the narrowed run.
    assert script.count('-n "$TEST_WORKERS" --basetemp=') >= 2


def test_both_pre_push_runners_stop_at_the_first_failure():
    """Fail fast, on purpose.

    The first failure is the only one worth reading: a cascade dominates the
    output and picks the wrong suspect — one recorded run here showed 54 "unable
    to find an element" errors downstream of 4 real timeouts. It also bounds what
    a broken push costs, which is not academic on this box: the full-suite
    fallback ran 1:40 under load, and the push that added these flags came back
    in 7:23 because `-x` stopped it.

    Pinned because removing a flag for "one full local run" is a one-character
    diff that reads as harmless, and nothing else would notice. CI runs to
    completion without either flag, so the total count is never lost.
    """
    scripts = _ROOT / ".lefthook" / "scripts"
    server = (scripts / "server-tests.sh").read_text(encoding="utf-8")
    client = (scripts / "client-tests.sh").read_text(encoding="utf-8")

    # The invocation lines, not the whole file. The first version of this
    # counted "pytest -x " across the script and was satisfied by the two `echo`
    # lines that merely print the command — it passed with `-x` stripped from a
    # real call site, which a mutation check caught and nothing else would have.
    pytest_calls = [
        line.strip() for line in server.splitlines() if line.strip().startswith("pytest ")
    ]
    assert len(pytest_calls) >= 2, f"expected both invocation sites, found {pytest_calls}"
    for call in pytest_calls:
        assert call.startswith("pytest -x "), f"call site is no longer fail-fast: {call}"
        # `-x` stops at a failure only once the run reaches it: last push's
        # failures go first, so the re-push after a fix answers in seconds.
        assert " --ff " in call, f"call site no longer runs last failures first: {call}"
        # Without it xdist hands worker 1 the whole front of the collection.
        assert " --maxschedchunk=1 " in call, f"call site no longer runs in order: {call}"
    # A full run starts with what the change reaches (pytest_run_first.py).
    assert '-p pytest_run_first --run-first="$RUN_FIRST"' in server

    for line in client.splitlines():
        stripped = line.strip()
        if stripped.startswith('"${TEST_NICE[@]}" npm test'):
            assert "--bail" in stripped, f"jest run does not stop at the first failure: {stripped}"


def test_server_hook_gives_pytest_a_private_temp_root():
    """Both pytest invocations must name their own basetemp.

    pytest garbage-collects the older `pytest-of-<user>/pytest-N` roots it finds
    at session start, so two concurrent runs delete each other's trees and the
    loser dies in `pytest_sessionfinish` — reporting every test passed and then
    exiting non-zero. A push rejected that way looks like a flaky suite and is
    not one, which is why this is pinned rather than left to a comment.
    """
    script = (_ROOT / ".lefthook" / "scripts" / "server-tests.sh").read_text(encoding="utf-8")
    assert "mktemp -d" in script
    # Every real run, not just the fallback one. Counted against the pytest
    # invocation itself rather than the prefix in front of it: the prefix gained
    # `"${TEST_NICE[@]}"` when the worker budget landed, and a literal that long
    # pins the command's spelling instead of the property being asserted.
    # Per invocation line, not by comparing counts of two long literals. That
    # comparison broke when `-x` was added ahead of `-q` — one side went to zero
    # and the assertion read `2 == 0`, which says nothing about basetemps. The
    # property is that no pytest call can be started without its own temp root,
    # and asking each call line directly is what actually states it.
    pytest_calls = [
        line.strip() for line in script.splitlines() if line.strip().startswith("pytest ")
    ]
    assert len(pytest_calls) >= 2, f"expected both invocation sites, found {pytest_calls}"
    for call in pytest_calls:
        assert '--basetemp="$BASETEMP"' in call, f"pytest call without its own temp root: {call}"
    # Cleanup that cannot itself fail the push.
    # The EXIT trap also prints the step summary, so it is matched by what it
    # must do — remove the root, and never fail the push doing it — not by its
    # whole spelling.
    (trap,) = [line for line in script.splitlines() if line.startswith("trap ")]
    assert trap.endswith("' EXIT")
    # The root is $RUN_TEMP, not $BASETEMP: pytest rmtree's a basetemp it is
    # handed, so the test-count file lives beside it, and removing the parent is
    # what removes both.
    assert 'BASETEMP="$RUN_TEMP/pytest"' in script
    assert 'rm -rf "$RUN_TEMP" 2>/dev/null || true' in trap


def test_client_hook_narrows_jest_and_keeps_the_wide_checks():
    script = (_ROOT / ".lefthook" / "scripts" / "client-tests.sh").read_text(encoding="utf-8")
    assert "--changedSince=" in script
    assert "LOREGARDEN_FULL_TESTS" in script
    # oxlint and tsc still cover everything.
    assert "npm run lint" in script
    assert "npx tsc -b" in script


def test_naming_an_init_file_names_no_package_in_particular(hub_repo: Path):
    (hub_repo / "server" / "tests" / "test_about.py").write_text(
        'SOURCE = "__init__.py"\n', encoding="utf-8"
    )
    selected = _selected(hub_repo, "hub/__init__")
    assert "test_whole.py" in selected
    assert "test_about.py" not in selected
